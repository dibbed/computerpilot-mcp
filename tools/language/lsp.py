"""Bounded one-shot Language Server Protocol client."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from core.errors import ToolError
from tools.language import transport
from tools.language.transport import _frame as _frame
from tools.language.transport import _parse_frames as _parse_frames

_MAX_DOCUMENT_BYTES = 2 * 1024 * 1024


def position_to_offset(text: str, position: dict[str, int]) -> int:
    line = position.get("line")
    target = position.get("character")
    if type(line) is not int or type(target) is not int or line < 0 or target < 0:
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
        if not 0 < max_message_bytes <= 4_000_000 or not max_message_bytes <= max_total_bytes <= 8_000_000:
            raise ValueError("LSP response bounds are invalid.")
        self.command = command
        self.workspace = workspace.resolve(strict=False)
        self.timeout_sec = timeout_sec
        self.max_message_bytes = max_message_bytes
        self.max_total_bytes = max_total_bytes
        self.capabilities: dict[str, Any] = {}

    def _document_notification(self, method: str, params: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
        if not method.startswith("textDocument/"):
            return None
        document = params.get("textDocument")
        if not isinstance(document, dict) or not isinstance(document.get("uri"), str):
            return None
        uri = str(document["uri"])
        path = uri_to_path(uri, self.workspace)
        try:
            with path.open("rb") as handle:
                payload = handle.read(_MAX_DOCUMENT_BYTES + 1)
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
        document = self._document_notification(method, params)
        process = transport._open_command_transport([executable, *self.command[1:]], self.workspace)
        assert process.stdout is not None and process.stderr is not None
        channel = transport.LspTransport(process, max_message_bytes=self.max_message_bytes, max_request_bytes=self.max_total_bytes)
        cancel = threading.Event()
        deadline = time.monotonic() + self.timeout_sec
        opened_uri: str | None = None
        target: dict[str, Any] | None = None
        try:
            channel.send(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"processId": None, "rootUri": self.workspace.as_uri(), "capabilities": {}},
                },
            )
            initialize = channel.receive(deadline=deadline, cancel=cancel)
            if "error" in initialize:
                raise ToolError("lsp_request_failed", "Language server rejected initialization.")
            result = initialize.get("result")
            if not isinstance(result, dict):
                raise ToolError("lsp_protocol_error", "Language server returned invalid initialization capabilities.")
            self.capabilities = dict(result.get("capabilities") or {})
            channel.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})

            if document is not None:
                notification, opened_uri = document
                channel.send(notification)

            channel.send({"jsonrpc": "2.0", "id": 2, "method": method, "params": params})
            target = channel.receive(deadline=deadline, cancel=cancel)
            if target.get("complete") is False:
                raise ToolError("lsp_partial_result_unsupported", "Partial language results require the bounded aggregation interface.")

            if opened_uri is not None:
                channel.send(
                    {
                        "jsonrpc": "2.0",
                        "method": "textDocument/didClose",
                        "params": {"textDocument": {"uri": opened_uri}},
                    },
                )

            channel.send({"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": None})
            shutdown = channel.receive(deadline=deadline, cancel=cancel)
            if "error" in shutdown:
                raise ToolError("lsp_request_failed", "Language server rejected the shutdown request.")
            channel.send({"jsonrpc": "2.0", "method": "exit", "params": {}})
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
                raise ToolError("lsp_request_failed", "Language server rejected the request.")
            return target.get("result")
        except ToolError:
            raise
        except Exception:
            raise ToolError("lsp_protocol_error", "Language server protocol failed.") from None
        finally:
            if not channel.close():
                raise ToolError("lsp_cleanup_incomplete", "Language server cleanup could not be confirmed.") from None
