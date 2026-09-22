"""Bounded one-shot Language Server Protocol client."""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote, urlparse

from core.errors import ToolError
from core.executor import _creation_flags

_MAX_HEADER_BYTES = 64 * 1024
_MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
_MAX_STDERR_BYTES = 64 * 1024


def position_to_offset(text: str, position: dict[str, int]) -> int:
    line = position.get("line")
    target = position.get("character")
    if not isinstance(line, int) or not isinstance(target, int) or line < 0 or target < 0:
        raise ToolError("lsp_position_invalid", "LSP line and character must be non-negative integers.")

    # LSP line boundaries are LF or CRLF. Python str.splitlines() is broader
    # (for example it treats U+2028 as a line break), which would corrupt LSP
    # positions. Keep an explicit table of line content ranges instead.
    ranges: list[tuple[int, int]] = []
    start = 0
    index = 0
    while index < len(text):
        if text[index] == "\n":
            end = index - 1 if index > start and text[index - 1] == "\r" else index
            ranges.append((start, end))
            start = index + 1
        index += 1
    ranges.append((start, len(text)))

    if line >= len(ranges):
        raise ToolError("lsp_position_invalid", "LSP line is outside the document.")
    start, end = ranges[line]
    units = 0
    for offset in range(start, end):
        if units == target:
            return offset
        units += len(text[offset].encode("utf-16-le")) // 2
        if units > target:
            raise ToolError("lsp_position_invalid", "LSP position splits a UTF-16 surrogate pair.")
    if units == target:
        return end
    raise ToolError("lsp_position_invalid", "LSP character is outside the line.")


def uri_to_path(uri: str, workspace: Path) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ToolError("lsp_uri_unsupported", "Only file URIs are supported.")
    raw = unquote(parsed.path)
    if parsed.netloc:
        raw = f"//{parsed.netloc}{raw}"
    if len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
        raw = raw[1:]
    path = Path(raw).resolve(strict=False)
    root = workspace.resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ToolError("lsp_path_outside_workspace", "Language server returned a path outside the workspace.") from exc
    return path


def _frame(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode() + body


def _parse_frames(payload: bytes, max_message_bytes: int) -> list[dict[str, Any]]:
    """Parse a finite payload for tests and compatibility helpers."""

    messages: list[dict[str, Any]] = []
    offset = 0
    while offset < len(payload):
        boundary = payload.find(b"\r\n\r\n", offset)
        if boundary < 0:
            raise ToolError("lsp_protocol_error", "Language server returned malformed headers.")
        headers = payload[offset:boundary].decode("ascii", errors="strict").split("\r\n")
        length_header = next((line for line in headers if line.lower().startswith("content-length:")), None)
        if length_header is None:
            raise ToolError("lsp_protocol_error", "Language server response has no Content-Length header.")
        try:
            length = int(length_header.split(":", 1)[1].strip())
        except ValueError as exc:
            raise ToolError("lsp_protocol_error", "Language server returned invalid Content-Length.") from exc
        if length < 0 or length > max_message_bytes:
            raise ToolError("lsp_message_too_large", "Language server response exceeded the configured message limit.")
        start = boundary + 4
        end = start + length
        if end > len(payload):
            raise ToolError("lsp_protocol_error", "Language server response ended before its declared length.")
        value = json.loads(payload[start:end])
        if not isinstance(value, dict):
            raise ToolError("lsp_protocol_error", "Language server response must be a JSON object.")
        messages.append(value)
        offset = end
    return messages


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    output = bytearray()
    while len(output) < length:
        chunk = stream.read(length - len(output))
        if not chunk:
            raise ToolError("lsp_protocol_error", "Language server response ended before its declared length.")
        output.extend(chunk)
    return bytes(output)


def _reader(
    stream: BinaryIO,
    output: queue.Queue[dict[str, Any] | BaseException],
    *,
    max_message_bytes: int,
    max_total_bytes: int,
) -> None:
    total = 0
    try:
        while True:
            headers: list[bytes] = []
            header_size = 0
            while True:
                line = stream.readline(_MAX_HEADER_BYTES + 1)
                if not line:
                    if not headers:
                        output.put(EOFError("language server stdout closed"))
                        return
                    raise ToolError("lsp_protocol_error", "Language server returned malformed or incomplete headers.")
                header_size += len(line)
                total += len(line)
                if header_size > _MAX_HEADER_BYTES or total > max_total_bytes:
                    raise ToolError("lsp_message_too_large", "Language server response exceeded the configured total limit.")
                if len(line) > _MAX_HEADER_BYTES or (not line.endswith(b"\n") and len(line) >= _MAX_HEADER_BYTES):
                    raise ToolError("lsp_protocol_error", "Language server returned an oversized header.")
                if line in {b"\r\n", b"\n"}:
                    break
                headers.append(line.rstrip(b"\r\n"))
            length: int | None = None
            for raw in headers:
                try:
                    name, value = raw.decode("ascii", errors="strict").split(":", 1)
                except (UnicodeDecodeError, ValueError) as exc:
                    raise ToolError("lsp_protocol_error", "Language server returned malformed headers.") from exc
                if name.casefold() == "content-length":
                    try:
                        length = int(value.strip())
                    except ValueError as exc:
                        raise ToolError("lsp_protocol_error", "Language server returned invalid Content-Length.") from exc
            if length is None:
                raise ToolError("lsp_protocol_error", "Language server response has no Content-Length header.")
            if length < 0 or length > max_message_bytes:
                raise ToolError("lsp_message_too_large", "Language server response exceeded the configured message limit.")
            total += length
            if total > max_total_bytes:
                raise ToolError("lsp_message_too_large", "Language server response exceeded the configured total limit.")
            body = _read_exact(stream, length)
            try:
                value = json.loads(body)
            except json.JSONDecodeError as exc:
                raise ToolError("lsp_protocol_error", "Language server returned invalid JSON.") from exc
            if not isinstance(value, dict):
                raise ToolError("lsp_protocol_error", "Language server response must be a JSON object.")
            output.put(value)
    except BaseException as exc:
        output.put(exc)


def _stderr_reader(stream: BinaryIO, sink: list[bytes]) -> None:
    tail = bytearray()
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            tail.extend(chunk)
            if len(tail) > _MAX_STDERR_BYTES:
                del tail[:-_MAX_STDERR_BYTES]
    except OSError:
        pass
    sink.append(bytes(tail))


def _language_id(path: Path) -> str:
    return {
        ".py": "python",
        ".pyi": "python",
        ".js": "javascript",
        ".jsx": "javascriptreact",
        ".ts": "typescript",
        ".tsx": "typescriptreact",
        ".json": "json",
        ".rs": "rust",
        ".go": "go",
    }.get(path.suffix.casefold(), "plaintext")


class LspClient:
    def __init__(
        self,
        command: list[str],
        workspace: Path,
        *,
        timeout_sec: float = 30,
        max_message_bytes: int = 4_000_000,
        max_total_bytes: int = 8_000_000,
    ) -> None:
        if not command or not command[0]:
            raise ValueError("A language server command is required.")
        if timeout_sec <= 0:
            raise ValueError("timeout_sec must be positive.")
        if max_message_bytes <= 0 or max_total_bytes < max_message_bytes:
            raise ValueError("LSP response bounds are invalid.")
        self.command = command
        self.workspace = workspace.resolve(strict=False)
        self.timeout_sec = timeout_sec
        self.max_message_bytes = max_message_bytes
        self.max_total_bytes = max_total_bytes
        self.capabilities: dict[str, Any] = {}

    @staticmethod
    def _send(process: subprocess.Popen[bytes], payload: dict[str, Any]) -> None:
        if process.stdin is None:
            raise ToolError("lsp_protocol_error", "Language server stdin is unavailable.")
        try:
            process.stdin.write(_frame(payload))
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ToolError("lsp_protocol_error", "Language server closed stdin unexpectedly.") from exc

    def _wait_response(
        self,
        process: subprocess.Popen[bytes],
        messages: queue.Queue[dict[str, Any] | BaseException],
        request_id: int,
        deadline: float,
    ) -> dict[str, Any]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ToolError("lsp_timeout", "Language server did not respond before the timeout.")
            try:
                item = messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise ToolError("lsp_timeout", "Language server did not respond before the timeout.") from exc
            if isinstance(item, BaseException):
                if isinstance(item, EOFError):
                    raise ToolError("lsp_protocol_error", "Language server closed stdout before the required response.")
                if isinstance(item, ToolError):
                    raise item
                raise ToolError("lsp_protocol_error", str(item)) from item
            if item.get("id") == request_id and "method" not in item:
                return item
            if "id" in item and "method" in item:
                self._send(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "id": item["id"],
                        "error": {"code": -32601, "message": "Client method not supported"},
                    },
                )

    def _document_notification(self, method: str, params: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
        if not method.startswith("textDocument/"):
            return None
        document = params.get("textDocument")
        if not isinstance(document, dict) or not isinstance(document.get("uri"), str):
            return None
        uri = str(document["uri"])
        path = uri_to_path(uri, self.workspace)
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ToolError("lsp_document_unavailable", f"Cannot read LSP document: {path}") from exc
        if len(payload) > _MAX_DOCUMENT_BYTES:
            raise ToolError("lsp_document_too_large", "LSP document exceeds the bounded didOpen size.")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("lsp_document_encoding", "LSP documents must be UTF-8 for one-shot requests.") from exc
        return (
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": uri,
                        "languageId": _language_id(path),
                        "version": 1,
                        "text": text,
                    }
                },
            },
            uri,
        )

    def request(self, method: str, params: dict[str, Any]) -> Any:
        executable = shutil.which(self.command[0])
        if executable is None:
            raise ToolError("lsp_server_not_found", f"Language server executable not found: {self.command[0]}")
        if not self.workspace.is_dir():
            raise ToolError("lsp_workspace_not_found", f"Language server workspace not found: {self.workspace}")
        process = subprocess.Popen(
            [executable, *self.command[1:]],
            cwd=self.workspace,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_creation_flags(),
        )
        assert process.stdout is not None and process.stderr is not None
        responses: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        stderr_tail: list[bytes] = []
        reader = threading.Thread(
            target=_reader,
            args=(process.stdout, responses),
            kwargs={"max_message_bytes": self.max_message_bytes, "max_total_bytes": self.max_total_bytes},
            name="lsp-stdout-reader",
            daemon=True,
        )
        stderr_reader = threading.Thread(
            target=_stderr_reader,
            args=(process.stderr, stderr_tail),
            name="lsp-stderr-reader",
            daemon=True,
        )
        reader.start()
        stderr_reader.start()
        deadline = time.monotonic() + self.timeout_sec
        opened_uri: str | None = None
        target: dict[str, Any] | None = None
        try:
            self._send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"processId": None, "rootUri": self.workspace.as_uri(), "capabilities": {}},
                },
            )
            initialize = self._wait_response(process, responses, 1, deadline)
            if "error" in initialize:
                raise ToolError("lsp_request_failed", str(initialize["error"]))
            self.capabilities = dict((initialize.get("result") or {}).get("capabilities") or {})
            self._send(process, {"jsonrpc": "2.0", "method": "initialized", "params": {}})

            document = self._document_notification(method, params)
            if document is not None:
                notification, opened_uri = document
                self._send(process, notification)

            self._send(process, {"jsonrpc": "2.0", "id": 2, "method": method, "params": params})
            target = self._wait_response(process, responses, 2, deadline)

            if opened_uri is not None:
                self._send(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "method": "textDocument/didClose",
                        "params": {"textDocument": {"uri": opened_uri}},
                    },
                )

            self._send(process, {"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": None})
            shutdown = self._wait_response(process, responses, 3, deadline)
            if "error" in shutdown:
                raise ToolError("lsp_request_failed", str(shutdown["error"]))
            self._send(process, {"jsonrpc": "2.0", "method": "exit", "params": {}})
            if process.stdin is not None:
                process.stdin.close()
            remaining = max(deadline - time.monotonic(), 0.01)
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                raise ToolError("lsp_timeout", "Language server did not exit before the timeout.") from exc

            if target is None:
                raise ToolError("lsp_protocol_error", "Language server omitted the requested response.")
            if "error" in target:
                raise ToolError("lsp_request_failed", str(target["error"]))
            return target.get("result")
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError("lsp_protocol_error", str(exc)) from exc
        finally:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            reader.join(timeout=1)
            stderr_reader.join(timeout=1)
            if target is None and stderr_tail:
                detail = stderr_tail[-1].decode("utf-8", errors="replace").strip()
                if detail:
                    self.capabilities.setdefault("_last_stderr", detail[-1_000:])
