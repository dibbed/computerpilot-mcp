"""Owned process transport for language servers.

Every language-server process is spawned under the strongest process-tree
ownership available on this host (Windows Job Object or POSIX process group)
with an explicit minimal environment: only fixed OS variables required for
process startup are copied, ``PATH`` is narrowed to the pinned executable
directories of the reviewed command tuple, and no other parent variable
(``PYTHONPATH``, ``PYTHONHOME``, ``NODE_OPTIONS``, ``NODE_PATH``, shell loader
variables, credentials) is inherited. Environment filtering is not an OS
sandbox: the server still runs with the user's own account privileges.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import threading
import time
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import IO, Any

from core.errors import ToolError
from core.executor import _creation_flags
from core.extensions.transport import FrameDepthExceededError, scan_json_depth
from core.process_ownership import OwnedProcess, spawn_owned_process
from core.windows_job import NativeOwnershipRequiredError
from tools.language.providers import LspProvider, command_for, validate_provider

_OS_ENV_VARS = ("SystemRoot", "WINDIR", "TEMP", "TMP") if os.name == "nt" else ("LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")
_MAX_HEADER_BYTES = 64 * 1024


def _protocol_error() -> ToolError:
    return ToolError("lsp_protocol_error", "Language server returned a malformed protocol message.")


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


def _frame(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode() + body


def _read_message(stream: IO[bytes], max_message_bytes: int) -> tuple[dict[str, Any], int]:
    header_bytes = 0
    length: int | None = None
    while True:
        line = stream.readline(_MAX_HEADER_BYTES - header_bytes + 1)
        if not line:
            if header_bytes:
                raise _protocol_error()
            raise EOFError
        header_bytes += len(line)
        if header_bytes > _MAX_HEADER_BYTES:
            raise _protocol_error()
        if line == b"\r\n":
            break
        if not line.endswith(b"\r\n"):
            raise _protocol_error()
        try:
            name, value = line[:-2].decode("ascii", errors="strict").split(":", 1)
        except (UnicodeError, ValueError):
            raise _protocol_error() from None
        if name.casefold() == "content-length":
            value = value.strip()
            if length is not None or not value or not value.isascii() or not value.isdecimal() or len(value) > 10:
                raise _protocol_error()
            length = int(value)
    if length is None:
        raise _protocol_error()
    if length > max_message_bytes:
        raise ToolError("lsp_message_too_large", "Language server response exceeded the configured message limit.")
    body = bytearray()
    while len(body) < length:
        part = stream.read(length - len(body))
        if not part:
            raise _protocol_error()
        body.extend(part)
    try:
        text = body.decode("utf-8", errors="strict")
        scan_json_depth(text, 32)
        value_json = json.loads(text, parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError, FrameDepthExceededError):
        raise _protocol_error() from None
    if not isinstance(value_json, dict):
        raise _protocol_error()
    if "id" in value_json and (type(value_json["id"]) not in (str, int) or len(str(value_json["id"])) > 128):
        raise _protocol_error()
    if "method" in value_json and (
        not isinstance(value_json["method"], str) or not value_json["method"] or len(value_json["method"]) > 256
    ):
        raise _protocol_error()
    return value_json, header_bytes + length


def _parse_frames(payload: bytes, max_message_bytes: int) -> list[dict[str, Any]]:
    """Finite compatibility parser using the same bounded wire validation."""
    stream = io.BytesIO(payload)
    messages = []
    while stream.tell() < len(payload):
        messages.append(_read_message(stream, max_message_bytes)[0])
    return messages


def _stderr_reader(stream: IO[bytes]) -> None:
    """Discard private stderr in bounded chunks without retaining provider prose."""
    try:
        while stream.read(4096):
            pass
    except (OSError, ValueError):
        pass


class LspTransport:
    """One owner, one reader, bounded framed messages and correlated requests."""

    def __init__(
        self, process: OwnedProcess, *, max_message_bytes: int = 4_000_000, max_request_bytes: int = 8_000_000,
    ) -> None:
        if not 0 < max_message_bytes <= 4_000_000 or not max_message_bytes <= max_request_bytes <= 8_000_000:
            raise ValueError("LSP response bounds are invalid.")
        if process.stdout is None or process.stdin is None or process.stderr is None:
            raise _protocol_error()
        self.process = process
        self.max_message_bytes = max_message_bytes
        self.max_request_bytes = max_request_bytes
        self._owner = threading.get_ident()
        self._condition = threading.Condition()
        self._messages: deque[tuple[dict[str, Any], int]] = deque()
        self._queued_bytes = 0
        self._failure: ToolError | None = None
        self._eof = False
        self._closing = threading.Event()
        self._close_result: bool | None = None
        self._last_id = 0
        self._pending: int | None = None
        self._request_bytes = 0
        self._preparing_request = False
        self._notifications = 0
        self._stale = 0
        self._partial_token: object = None
        self._partial_results: list[object] = []
        self.notification_counts = {"textDocument/publishDiagnostics": 0, "$/progress": 0}
        self.wire_bytes = 0
        self._reader = threading.Thread(target=self._read, name="lsp-stdout-reader", daemon=True)
        self._stderr = threading.Thread(target=_stderr_reader, args=(process.stderr,), name="lsp-stderr-reader", daemon=True)
        try:
            self._reader.start()
            self._stderr.start()
        except RuntimeError:
            if not self.close():
                raise ToolError("lsp_cleanup_incomplete", "Language server cleanup could not be confirmed.") from None
            raise ToolError("lsp_launch_failed", "Language server reader could not be started.") from None

    def _fail(self, error: ToolError) -> None:
        with self._condition:
            if self._failure is None:
                self._failure = error
            self._condition.notify_all()
        try:
            self.process.terminate_tree(force=True)
        except (OSError, ToolError):
            pass

    def _read(self) -> None:
        assert self.process.stdout is not None
        try:
            while not self._closing.is_set():
                message, size = _read_message(self.process.stdout, self.max_message_bytes)
                with self._condition:
                    if "method" not in message and (type(message.get("id")) is not int or message["id"] > self._last_id):
                        raise _protocol_error()
                    if len(self._messages) >= 64 or self._queued_bytes + size > 8_000_000:
                        raise ToolError("lsp_message_too_large", "Language server exceeded the bounded message queue.")
                    self._messages.append((message, size))
                    self._queued_bytes += size
                    self.wire_bytes += size
                    self._condition.notify_all()
        except EOFError:
            with self._condition:
                self._eof = True
                self._condition.notify_all()
        except ToolError as error:
            self._fail(error)
        except Exception:
            self._fail(_protocol_error())

    def _check_owner(self) -> None:
        if threading.get_ident() != self._owner or self._closing.is_set():
            raise _protocol_error()
        with self._condition:
            if self._failure is not None:
                raise self._failure

    def begin_request(self) -> None:
        self._check_owner()
        if self._pending is not None:
            raise _protocol_error()
        self._request_bytes = 0
        self._preparing_request = True

    def send(self, payload: dict[str, object]) -> None:
        self._check_owner()
        if "method" in payload and "id" in payload:
            with self._condition:
                request_id = payload["id"]
                if type(request_id) is not int or request_id <= self._last_id or self._pending is not None:
                    raise _protocol_error()
                self._last_id = request_id
                self._pending = request_id
                if not self._preparing_request:
                    self._request_bytes = 0
                self._preparing_request = False
                self._notifications = 0
                self._stale = 0
                params = payload.get("params")
                self._partial_token = params.get("partialResultToken") if isinstance(params, dict) else None
                self._partial_results = []
        try:
            framed = _frame(payload)
            if len(framed) + self._request_bytes > self.max_request_bytes:
                raise ToolError("lsp_message_too_large", "Language server request exceeded the configured total limit.")
            self._request_bytes += len(framed)
            with self._condition:
                self.wire_bytes += len(framed)
            assert self.process.stdin is not None
            self.process.stdin.write(framed)
            self.process.stdin.flush()
        except ToolError as error:
            self._fail(error)
            raise
        except (OSError, ValueError, TypeError, RecursionError):
            self._fail(_protocol_error())
            raise _protocol_error() from None

    def receive(self, *, deadline: float, cancel: threading.Event) -> dict[str, object]:
        self._check_owner()
        if self._pending is None:
            raise _protocol_error()
        while True:
            if cancel.is_set():
                raise ToolError("lsp_cancelled", "Language server request was cancelled.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ToolError("lsp_timeout", "Language server did not respond before the timeout.")
            with self._condition:
                if self._failure is not None:
                    raise self._failure
                if not self._messages:
                    if self._eof:
                        raise _protocol_error()
                    self._condition.wait(min(remaining, 0.05))
                    continue
                item, size = self._messages.popleft()
                self._queued_bytes -= size
            self._request_bytes += size
            if self._request_bytes > self.max_request_bytes:
                self._fail(ToolError("lsp_message_too_large", "Language server response exceeded the configured total limit."))
                raise self._failure or _protocol_error()
            if "method" in item:
                self._notifications += 1
                if self._notifications > 256:
                    self._fail(_protocol_error())
                    raise _protocol_error()
                if "id" in item:
                    reply: dict[str, object] = {"jsonrpc": "2.0", "id": item["id"]}
                    params = item.get("params")
                    items = params.get("items") if isinstance(params, dict) else None
                    if item["method"] == "workspace/configuration" and isinstance(items, list) and len(items) <= 100:
                        reply["result"] = [None] * len(items)
                    else:
                        reply["error"] = {"code": -32601, "message": "Client method not supported"}
                    self.send(reply)
                elif item["method"] in self.notification_counts:
                    name = item["method"]
                    self.notification_counts[name] = min(self.notification_counts[name] + 1, 256)
                    params = item.get("params")
                    if name == "$/progress" and isinstance(params, dict) and "value" in params:
                        if self._partial_token is not None and params.get("token") == self._partial_token:
                            self._partial_results.append(params["value"])
                        elif not isinstance(params["value"], dict) or params["value"].get("kind") not in {"begin", "report", "end"}:
                            self._fail(_protocol_error())
                            raise _protocol_error()
                continue
            response_id = item.get("id")
            if type(response_id) is not int or response_id > self._pending or len({"result", "error"} & item.keys()) != 1:
                self._fail(_protocol_error())
                raise _protocol_error()
            if response_id < self._pending:
                self._stale += 1
                if self._stale > 256:
                    self._fail(_protocol_error())
                    raise _protocol_error()
                continue
            self._pending = None
            if self._partial_results:
                item["partialResults"] = self._partial_results
                item["complete"] = False
            return item

    def close(self) -> bool:
        if self._close_result is None:
            self._closing.set()
            closed = close_transport(self.process, timeout_sec=0.5)
            for reader in (self._reader, self._stderr):
                if reader.ident is not None:
                    reader.join(1)
            self._close_result = closed and not self._reader.is_alive() and not self._stderr.is_alive()
        return self._close_result


def build_environment(executable_paths: Sequence[Path]) -> dict[str, str]:
    """Build the minimal launch environment; nothing else is inherited."""

    environment: dict[str, str] = {}
    for name in _OS_ENV_VARS:
        value = os.environ.get(name)
        if value:
            environment[name] = value
    directories: list[str] = []
    seen: set[str] = set()
    for executable in executable_paths:
        parent = str(Path(executable).parent)
        key = parent.casefold() if os.name == "nt" else parent
        if key not in seen:
            seen.add(key)
            directories.append(parent)
    environment["PATH"] = os.pathsep.join(directories)
    return environment


def _spawn(command: Sequence[str], root: str | Path, executable_paths: Sequence[Path]) -> OwnedProcess:
    environment = build_environment(executable_paths)
    try:
        return spawn_owned_process(
            command,
            cwd=root,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_creation_flags(),
            require_native=True,
        )
    except NativeOwnershipRequiredError as exc:
        raise ToolError(
            "lsp_ownership_unavailable",
            "Native process-tree ownership is unavailable; refusing to launch a language server.",
        ) from exc
    except FileNotFoundError as exc:
        raise ToolError("lsp_server_not_found", "Language server executable not found.") from exc
    except OSError as exc:
        raise ToolError("lsp_launch_failed", "The language server process could not be started.") from exc


def open_transport(provider: LspProvider, root: str | Path) -> OwnedProcess:
    """Spawn a pinned provider under native ownership.

    Recheck pins and root eligibility at the launch boundary.
    """

    if not provider.languages:
        raise ToolError("lsp_provider_unavailable", "The LSP provider has no eligible language.")
    validate_provider(root, provider.languages[0], providers=(provider,))
    return _spawn(command_for(provider), root, [provider.executable])


def _open_command_transport(command: Sequence[str], root: str | Path) -> OwnedProcess:
    """Private compatibility adapter for already-resolved absolute command tuples.

    This exists for the legacy one-shot client only. It is not a public
    dispatcher: commands must be absolute, and it never resolves names through
    ``PATH`` on its own.
    """

    if not command or not command[0]:
        raise ToolError("lsp_server_not_found", "Language server executable not found.")
    executable = Path(command[0])
    if not executable.is_absolute():
        raise ToolError("lsp_server_not_found", "Language server executable not found.")
    return _spawn(command, root, [executable])


def close_transport(process: OwnedProcess, *, timeout_sec: float) -> bool:
    """Close one owned language-server transport.

    A graceful window is attempted first (stdin EOF, then wait); a process
    that is still alive afterwards is reaped with ``terminate_tree(force=True)``.
    ``close_ownership`` runs only after that cleanup attempt. The return value
    is ``True`` only when process death and ownership release are both proven;
    ``False`` means the caller must quarantine the session and preserve
    evidence instead of reusing or trusting anything about this process.
    """

    ok = True
    try:
        stdin = process.stdin
        if stdin is not None and not stdin.closed:
            try:
                stdin.close()
            except OSError:
                ok = False
        if process.poll() is None:
            try:
                process.wait(timeout=timeout_sec)
            except (subprocess.TimeoutExpired, OSError):
                pass
        if process.poll() is None:
            try:
                process.terminate_tree(force=True)
            except (ToolError, OSError):
                ok = False
            try:
                process.wait(timeout=2.0)
            except (subprocess.TimeoutExpired, OSError):
                ok = False
        if process.poll() is None:
            ok = False
    finally:
        try:
            process.close_ownership()
        except (ToolError, OSError):
            ok = False
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is None or stream.closed:
                continue
            if process.poll() is None:
                # A live reader may hold the stream lock; quarantine without blocking close.
                ok = False
                continue
            try:
                stream.close()
            except OSError:
                ok = False
    return ok
