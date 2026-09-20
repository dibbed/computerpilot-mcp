"""Bounded one-shot Language Server Protocol client."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from core.errors import ToolError
from core.executor import _creation_flags


def position_to_offset(text: str, position: dict[str, int]) -> int:
    lines = text.splitlines(keepends=True)
    line = position["line"]
    if line < 0 or line >= len(lines):
        raise ToolError("lsp_position_invalid", "LSP line is outside the document.")
    target = position["character"]
    units = 0
    for index, character in enumerate(lines[line]):
        if units >= target:
            return sum(len(item) for item in lines[:line]) + index
        units += len(character.encode("utf-16-le")) // 2
        if units > target:
            raise ToolError("lsp_position_invalid", "LSP position splits a UTF-16 surrogate pair.")
    if units == target:
        return sum(len(item) for item in lines[: line + 1])
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
        length = int(length_header.split(":", 1)[1].strip())
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


class LspClient:
    def __init__(self, command: list[str], workspace: Path, *, timeout_sec: float = 30, max_message_bytes: int = 4_000_000) -> None:
        if not command or not command[0]:
            raise ValueError("A language server command is required.")
        self.command = command
        self.workspace = workspace.resolve(strict=False)
        self.timeout_sec = timeout_sec
        self.max_message_bytes = max_message_bytes
        self.capabilities: dict[str, Any] = {}

    def request(self, method: str, params: dict[str, Any]) -> Any:
        executable = shutil.which(self.command[0])
        if executable is None:
            raise ToolError("lsp_server_not_found", f"Language server executable not found: {self.command[0]}")
        root_uri = self.workspace.as_uri()
        messages: list[dict[str, Any]] = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"processId": None, "rootUri": root_uri, "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
            {"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": None},
            {"jsonrpc": "2.0", "method": "exit", "params": {}},
        ]
        process = subprocess.Popen(
            [executable, *self.command[1:]],
            cwd=self.workspace,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_creation_flags(),
        )
        try:
            stdout, stderr = process.communicate(b"".join(_frame(item) for item in messages), timeout=self.timeout_sec)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise ToolError("lsp_timeout", "Language server did not respond before the timeout.") from exc
        responses = _parse_frames(stdout, self.max_message_bytes)
        initialize = next((item for item in responses if item.get("id") == 1), None)
        target = next((item for item in responses if item.get("id") == 2), None)
        if initialize is None or target is None:
            detail = stderr.decode("utf-8", errors="replace")[-1_000:]
            raise ToolError("lsp_protocol_error", detail or "Language server omitted a required response.")
        self.capabilities = dict((initialize.get("result") or {}).get("capabilities") or {})
        if "error" in target:
            raise ToolError("lsp_request_failed", str(target["error"]))
        return target.get("result")
