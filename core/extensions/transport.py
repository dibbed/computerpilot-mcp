"""Bounded transport framing, preparser safety, and sanitized stderr drain for MCP extensions."""

from __future__ import annotations

import collections
import re
import subprocess
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import IO

import anyio
from mcp import types
from mcp.shared.message import SessionMessage

from core.process_ownership import OwnedProcess, spawn_owned_process

# Section 35 Provisional Experiment Defaults
MAX_FRAME_BYTES: int = 2 * 1024 * 1024  # 2 MiB max frame size
MAX_JSON_DEPTH: int = 32  # max structural nesting depth
MAX_STDERR_BYTES: int = 4 * 1024  # 4 KiB retained sanitized stderr tail
CHUNK_SIZE: int = 64 * 1024  # 64 KiB binary chunk read buffer
MAX_PENDING_NOTIFICATIONS: int = 16  # max coalesced notification backlog
MAX_PENDING_ERRORS: int = 8  # max recent error records

# Sanitization regexes for child stderr drain
_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_SECRET_CANARY_RE = re.compile(
    r"(?i)(synthetic[-_]canary[-_\w]*|bearer\s+[a-zA-Z0-9_\-\.=]+|token[:=\s]+[a-zA-Z0-9_\-\.]{8,}|"
    r"secret[:=\s]+[a-zA-Z0-9_\-\.]{8,}|password[:=\s]+[^\s,;&]{6,})"
)


class TransportFramingError(ValueError):
    """Base class for transport framing and structural decoding errors."""


class FrameOversizeError(TransportFramingError):
    """Raised when incoming frame bytes exceed the maximum allowed frame size."""


class FrameDepthExceededError(TransportFramingError):
    """Raised when JSON structural depth exceeds the maximum allowed nesting depth."""


class FrameEncodingError(TransportFramingError):
    """Raised when frame bytes cannot be strictly decoded as valid UTF-8."""


class FrameMalformedError(TransportFramingError):
    """Raised when frame cannot be parsed into a valid JSON-RPC message."""


class WriteTimeoutError(TransportFramingError):
    """Raised when writing a message to the subprocess stdin exceeds the write deadline."""


def scan_json_depth(text: str, max_depth: int = MAX_JSON_DEPTH) -> int:
    """Scan JSON structural nesting depth without full recursive parsing.

    Tracks string literals with proper backslash escapes so that brackets and
    braces within string values (including base64 strings and escaped quotes)
    do not increment structural depth.

    Raises:
        FrameDepthExceededError: If nesting depth exceeds max_depth at any point.
    """
    depth = 0
    max_observed = 0
    in_string = False
    escape = False

    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char in "{[":
                depth += 1
                if depth > max_observed:
                    max_observed = depth
                if depth > max_depth:
                    raise FrameDepthExceededError(
                        f"JSON structural depth {depth} exceeds ceiling of {max_depth}"
                    )
            elif char in "}]":
                if depth > 0:
                    depth -= 1
    return max_observed


class BoundedByteFrameReader:
    """Stateful byte buffer for bounded stdio line framing with pre-parsing safety.

    Enforces:
    - Byte capacity check BEFORE appending chunks to prevent unconstrained memory growth.
    - Immediate failure on newline-less oversize streams (no_newline).
    - Strict UTF-8 decoding on full newline-delimited frames (preserving multibyte UTF-8 across chunks).
    - Structural JSON depth scan before SDK parsing.
    """

    def __init__(
        self,
        max_frame_bytes: int = MAX_FRAME_BYTES,
        max_json_depth: int = MAX_JSON_DEPTH,
    ) -> None:
        self.max_frame_bytes = max_frame_bytes
        self.max_json_depth = max_json_depth
        self._buffer = bytearray()

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)

    def feed_chunk(self, chunk: bytes) -> list[str]:
        """Feed a raw byte chunk and return any completed, validated frame strings.

        Raises:
            FrameOversizeError: If pending bytes exceed max_frame_bytes before newline.
            FrameEncodingError: If a frame fails strict UTF-8 decoding.
            FrameDepthExceededError: If a frame's structural depth exceeds max_json_depth.
        """
        if not chunk:
            return []

        # Check pending size: if current buffer has no newline and adding chunk exceeds limit
        newline_idx = chunk.find(b"\n")
        if newline_idx == -1:
            if len(self._buffer) + len(chunk) > self.max_frame_bytes:
                raise FrameOversizeError(
                    f"Pending frame bytes {len(self._buffer) + len(chunk)} exceeds limit {self.max_frame_bytes}"
                )
            self._buffer.extend(chunk)
            return []

        first_line_len = len(self._buffer) + newline_idx
        if first_line_len > self.max_frame_bytes:
            raise FrameOversizeError(
                f"Frame length {first_line_len} exceeds limit {self.max_frame_bytes}"
            )
        self._buffer.extend(chunk)

        completed: list[str] = []
        while True:
            nl = self._buffer.find(b"\n")
            if nl == -1:
                if len(self._buffer) > self.max_frame_bytes:
                    raise FrameOversizeError(
                        f"Pending frame bytes {len(self._buffer)} exceeds limit {self.max_frame_bytes}"
                    )
                break

            line_bytes = bytes(self._buffer[:nl])
            del self._buffer[:nl + 1]

            if line_bytes.endswith(b"\r"):
                line_bytes = line_bytes[:-1]

            if len(line_bytes) > self.max_frame_bytes:
                raise FrameOversizeError(
                    f"Frame length {len(line_bytes)} exceeds limit {self.max_frame_bytes}"
                )

            if not line_bytes.strip():
                continue

            try:
                line_str = line_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise FrameEncodingError(
                    f"Frame is not valid UTF-8: {exc}"
                ) from exc

            scan_json_depth(line_str, max_depth=self.max_json_depth)
            completed.append(line_str)

        return completed


def parse_session_message(line: str) -> SessionMessage | Exception:
    """Validate and parse a verified JSON-RPC line into an SDK SessionMessage.

    Returns the SessionMessage on success, or the validation/JSON exception on failure.
    """
    try:
        message = types.jsonrpc_message_adapter.validate_json(line, by_name=False)
        return SessionMessage(message)
    except Exception as exc:
        return exc


class BoundedStderrDrain:
    """Continuous drain buffer for child stderr with byte caps and secret sanitization.

    Retains up to MAX_STDERR_BYTES (4 KiB) of the most recent sanitized output.
    Redacts synthetic canaries and potential tokens to prevent credential leakage.
    """

    def __init__(self, max_bytes: int = MAX_STDERR_BYTES) -> None:
        self.max_bytes = max_bytes
        self._buffer = bytearray()
        self._total_bytes_seen = 0

    @property
    def total_bytes_seen(self) -> int:
        return self._total_bytes_seen

    def feed_chunk(self, chunk: bytes) -> None:
        """Feed a raw stderr chunk and maintain the bounded tail buffer."""
        if not chunk:
            return
        self._total_bytes_seen += len(chunk)
        self._buffer.extend(chunk)
        if len(self._buffer) > self.max_bytes:
            excess = len(self._buffer) - self.max_bytes
            del self._buffer[:excess]

    def get_tail(self) -> str:
        """Return the retained stderr tail, strictly decoded and sanitized."""
        raw_text = self._buffer.decode("utf-8", errors="replace")
        sanitized = self.sanitize_text(raw_text)
        sanitized_bytes = sanitized.encode("utf-8")
        if len(sanitized_bytes) > self.max_bytes:
            sanitized_bytes = sanitized_bytes[-self.max_bytes:]
            sanitized = sanitized_bytes.decode("utf-8", errors="ignore")
        return sanitized

    def get_sanitized_summary(self, max_chars: int = 200) -> str:
        """Return a compact, sanitized single-line summary suitable for error messages."""
        tail = self.get_tail().strip()
        if not tail:
            return ""
        lines = [line.strip() for line in tail.splitlines() if line.strip()]
        last_line = lines[-1] if lines else ""
        if len(last_line) > max_chars:
            return last_line[:max_chars] + "..."
        return last_line

    @staticmethod
    def sanitize_text(text: str) -> str:
        """Sanitize text by removing ANSI codes, redacting secrets, and cleaning control characters."""
        text = _ANSI_ESCAPE_RE.sub("", text)
        text = _SECRET_CANARY_RE.sub("[REDACTED_SECRET]", text)
        cleaned_chars: list[str] = []
        for ch in text:
            if ch in ("\n", "\r", "\t") or ord(ch) >= 32:
                cleaned_chars.append(ch)
            else:
                cleaned_chars.append("?")
        return "".join(cleaned_chars)


class NotificationFloodCoalescer:
    """Bounded queue and flood coalescer for incoming notifications and errors.

    Prevents memory ballooning during notification floods by coalescing duplicate
    tool-list or catalog change notifications up to MAX_PENDING_NOTIFICATIONS.
    """

    def __init__(
        self,
        max_notifications: int = MAX_PENDING_NOTIFICATIONS,
        max_errors: int = MAX_PENDING_ERRORS,
    ) -> None:
        self.max_notifications = max_notifications
        self.max_errors = max_errors
        self._notifications: collections.deque[SessionMessage] = collections.deque(maxlen=max_notifications)
        self._errors: collections.deque[Exception] = collections.deque(maxlen=max_errors)
        self._coalesced_methods: set[str] = set()

    @property
    def pending_notification_count(self) -> int:
        return len(self._notifications)

    @property
    def pending_error_count(self) -> int:
        return len(self._errors)

    def push_message(self, message: SessionMessage | Exception) -> bool:
        """Push an incoming message or exception into the bounded queues.

        Coalesces duplicate list_changed notifications to prevent flood inflation.
        Returns True if accepted/queued, False if dropped due to coalescing.
        """
        if isinstance(message, Exception):
            self._errors.append(message)
            return True

        inner = getattr(message, "message", message)
        method = getattr(inner, "method", None)

        if method and method in ("notifications/tools/list_changed", "notifications/resources/list_changed"):
            if method in self._coalesced_methods:
                return False  # Coalesce duplicate pending notification
            self._coalesced_methods.add(method)
            self._notifications.append(message)
            return True

        self._notifications.append(message)
        return True

    def pop_notification(self) -> SessionMessage | None:
        """Pop the next pending notification and clear its coalescing marker if appropriate."""
        if not self._notifications:
            return None
        msg = self._notifications.popleft()
        inner = getattr(msg, "message", msg)
        method = getattr(inner, "method", None)
        if method and method in self._coalesced_methods:
            # Recheck if another notification with same method is still in queue
            remaining_methods = {
                getattr(getattr(m, "message", m), "method", None)
                for m in self._notifications
            }
            if method not in remaining_methods:
                self._coalesced_methods.discard(method)
        return msg

    def pop_error(self) -> Exception | None:
        """Pop the next pending error."""
        if not self._errors:
            return None
        return self._errors.popleft()


@asynccontextmanager
async def bounded_stdio_transport(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    max_frame_bytes: int = MAX_FRAME_BYTES,
    max_json_depth: int = MAX_JSON_DEPTH,
    write_timeout: float = 10.0,
    shutdown_timeout: float = 5.0,
    stderr_drain: BoundedStderrDrain | None = None,
    creationflags: int = 0,
) -> AsyncGenerator[
    tuple[
        anyio.streams.memory.MemoryObjectReceiveStream[SessionMessage | Exception],
        anyio.streams.memory.MemoryObjectSendStream[SessionMessage],
    ],
    None,
]:
    """Bounded, fail-closed stdio transport adapter for MCP subprocesses.

    Spawns the child process strictly under native process-tree ownership
    (Windows kill-on-close Job Object or POSIX process group) before any code executes.
    Yields (read_stream, write_stream) compatible with MCP SDK ClientSession.
    Enforces frame size limits, strict UTF-8 decoding, JSON structural depth ceilings,
    write deadlines on stdin, and continuous sanitized stderr draining.
    """
    read_stream_writer, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream[SessionMessage](0)

    drain = stderr_drain or BoundedStderrDrain()
    frame_reader = BoundedByteFrameReader(
        max_frame_bytes=max_frame_bytes, max_json_depth=max_json_depth
    )

    owned: OwnedProcess = spawn_owned_process(
        command=command,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
        require_native=True,
    )

    proc = owned.process
    assert proc.stdin and proc.stdout and proc.stderr

    shutting_down = False
    writer_done = anyio.Event()

    def sync_read_chunk(pipe: IO[bytes] | None) -> bytes:
        if not pipe:
            return b""
        try:
            if hasattr(pipe, "read1"):
                return pipe.read1(CHUNK_SIZE)
            return pipe.read(CHUNK_SIZE)
        except (ValueError, OSError):
            return b""

    def sync_write(data: bytes) -> None:
        assert proc.stdin
        proc.stdin.write(data)
        proc.stdin.flush()

    async def stdout_worker() -> None:
        try:
            while not shutting_down:
                chunk = await anyio.to_thread.run_sync(sync_read_chunk, proc.stdout)
                if not chunk:
                    break
                try:
                    lines = frame_reader.feed_chunk(chunk)
                    for line in lines:
                        msg = parse_session_message(line)
                        await read_stream_writer.send(msg)
                except Exception as exc:
                    await read_stream_writer.send(exc)
                    break
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            pass
        finally:
            with anyio.CancelScope(shield=True):
                await read_stream_writer.aclose()

    async def stderr_worker() -> None:
        try:
            while not shutting_down:
                chunk = await anyio.to_thread.run_sync(sync_read_chunk, proc.stderr)
                if not chunk:
                    break
                drain.feed_chunk(chunk)
        except (ValueError, OSError):
            pass

    async def stdin_worker() -> None:
        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    if shutting_down:
                        break
                    json_str = session_message.message.model_dump_json(
                        by_alias=True, exclude_unset=True
                    )
                    data = (json_str + "\n").encode("utf-8")
                    try:
                        with anyio.fail_after(write_timeout):
                            await anyio.to_thread.run_sync(sync_write, data)
                    except TimeoutError as exc:
                        raise WriteTimeoutError(
                            f"Write to subprocess stdin exceeded timeout of {write_timeout}s"
                        ) from exc
        except (anyio.ClosedResourceError, anyio.BrokenResourceError, OSError):
            pass
        finally:
            writer_done.set()

    async def shutdown() -> None:
        nonlocal shutting_down
        shutting_down = True
        read_stream.close()
        write_stream.close()

        with anyio.move_on_after(shutdown_timeout):
            await writer_done.wait()

        # Terminate entire process tree natively via Job Object / POSIX group
        owned.terminate_tree(force=True)

        # Close all pipes to immediately unblock any thread reading stdout/stderr
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except (ValueError, OSError):
                    pass

        owned.close_ownership()
        await read_stream_writer.aclose()
        await write_stream_reader.aclose()

    async with anyio.create_task_group() as tg:
        tg.start_soon(stdout_worker)
        tg.start_soon(stderr_worker)
        tg.start_soon(stdin_worker)
        try:
            yield read_stream, write_stream
        finally:
            with anyio.CancelScope(shield=True):
                await shutdown()
            tg.cancel_scope.cancel()

