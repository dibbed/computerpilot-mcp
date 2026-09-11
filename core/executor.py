"""Subprocess execution with optional output limits and disk-backed capture."""

from __future__ import annotations

import atexit
import io
import locale
import os
import subprocess
import tempfile
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Literal, cast

import psutil

from core.artifacts import Delivery, deliver_stream
from core.errors import ToolError

OutputMode = Literal["head", "tail", "both"]
SPOOL_MEMORY_BYTES = 1_048_576


class OutputCapture:
    """Thread-safe capture with optional limits and a 1 MiB memory spool."""

    def __init__(self, limit: int | None, mode: OutputMode) -> None:
        self.limit = max(limit, 0) if limit is not None else None
        self.mode = mode
        self.total = 0
        self._head = bytearray()
        self._tail = bytearray()
        self._spool: tempfile.SpooledTemporaryFile[bytes] | None = (
            tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY_BYTES, mode="w+b") if self.limit is None else None
        )
        self._closed = False
        self._lock = threading.Lock()

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        with self._lock:
            if self._closed:
                return
            self.total += len(chunk)
            if self.limit is None:
                assert self._spool is not None
                self._spool.write(chunk)
                return
            if self.limit == 0:
                return
            if self.mode == "head":
                remaining = self.limit - len(self._head)
                if remaining > 0:
                    self._head.extend(chunk[:remaining])
                return
            if self.mode == "tail":
                self._tail.extend(chunk)
                if len(self._tail) > self.limit:
                    del self._tail[: len(self._tail) - self.limit]
                return
            head_limit = self.limit // 2
            tail_limit = self.limit - head_limit
            remaining = head_limit - len(self._head)
            if remaining > 0:
                self._head.extend(chunk[:remaining])
            self._tail.extend(chunk)
            if len(self._tail) > tail_limit:
                del self._tail[: len(self._tail) - tail_limit]

    def result(self, encoding: str, *, since_byte: int | None = None,
               delivery: Delivery = "inline", final: bool = True) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise RuntimeError("Output capture is closed.")
            if self.limit is None:
                assert self._spool is not None
                try:
                    return deliver_stream(cast(BinaryIO, self._spool), encoding=encoding, total=self.total,
                                          offset=since_byte or 0, delivery=delivery, final=final)
                finally:
                    self._spool.seek(0, os.SEEK_END)
            elif since_byte is not None:
                raise ToolError("cursor_requires_full_capture", "since_byte requires capture_limit=null.")
            elif self.mode == "head":
                payload = bytes(self._head)
            elif self.mode == "tail":
                payload = bytes(self._tail)
            elif self.total <= self.limit:
                overlap = max(len(self._head) + len(self._tail) - self.total, 0)
                payload = bytes(self._head + self._tail[overlap:])
            else:
                marker = b"\n...<truncated>...\n"
                if self.limit <= len(marker):
                    payload = marker[: self.limit]
                else:
                    content_limit = self.limit - len(marker)
                    head_limit = content_limit // 2
                    tail_limit = content_limit - head_limit
                    tail = self._tail[-tail_limit:] if tail_limit else b""
                    payload = bytes(self._head[:head_limit]) + marker + bytes(tail)
            total = self.total
        result = deliver_stream(io.BytesIO(payload), encoding=encoding, total=len(payload), delivery=delivery)
        result.update(total_bytes=total, truncated=self.limit is not None and total > self.limit)
        return result

    @property
    def spooled_to_disk(self) -> bool:
        with self._lock:
            return bool(self._spool is not None and getattr(self._spool, "_rolled", False))

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._spool is not None:
                self._spool.close()


def _read_pipe(pipe: BinaryIO | None, capture: OutputCapture) -> None:
    if pipe is None:
        return
    try:
        read_available = getattr(pipe, "read1", pipe.read)
        while True:
            chunk = read_available(65_536)
            if not chunk:
                break
            capture.feed(chunk)
    finally:
        pipe.close()


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW


def _process_env(extra: dict[str, str] | None) -> dict[str, str]:
    env = os.environ.copy()
    if extra:
        env.update({str(key): str(value) for key, value in extra.items()})
    return env


def terminate_process_tree(pid: int, *, force: bool, include_children: bool = True) -> dict[str, Any]:
    """Terminate one process tree and report exactly which PIDs were affected."""

    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess as exc:
        raise ToolError("process_not_found", f"Process {pid} does not exist.") from exc
    processes = parent.children(recursive=True) if include_children else []
    processes.append(parent)
    targeted = [process.pid for process in processes]
    for process in reversed(processes):
        try:
            process.kill() if force else process.terminate()
        except psutil.NoSuchProcess:
            pass
    gone, alive = psutil.wait_procs(processes, timeout=3.0)
    if alive and not force:
        for process in alive:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
        gone_after, alive = psutil.wait_procs(alive, timeout=2.0)
        gone.extend(gone_after)
    return {
        "targeted_pids": targeted,
        "terminated_pids": sorted({process.pid for process in gone}),
        "alive_pids": sorted({process.pid for process in alive}),
    }


def run_bounded(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_sec: float,
    stdout_limit: int | None = None,
    stderr_limit: int | None = None,
    output_mode: OutputMode = "tail",
    env: dict[str, str] | None = None,
    stdin_text: str | None = None,
    encoding: str | None = None,
    delivery: Delivery = "inline",
) -> dict[str, Any]:
    """Execute without shell expansion and optionally limit each output stream."""

    if not command or not command[0]:
        raise ToolError("invalid_command", "An executable is required.")
    if not cwd.is_dir():
        raise ToolError("invalid_cwd", f"Working directory does not exist: {cwd}")
    chosen_encoding = encoding or locale.getpreferredencoding(False) or "utf-8"
    stdout = OutputCapture(stdout_limit, output_mode)
    stderr = OutputCapture(stderr_limit, output_mode)
    try:
        started = time.perf_counter()
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd),
            env=_process_env(env),
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_creation_flags(),
        )
        stdout_thread = threading.Thread(target=_read_pipe, args=(process.stdout, stdout), daemon=True)
        stderr_thread = threading.Thread(target=_read_pipe, args=(process.stderr, stderr), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        if stdin_text is not None and process.stdin is not None:
            try:
                process.stdin.write(stdin_text.encode(chosen_encoding, errors="replace"))
                process.stdin.close()
            except BrokenPipeError:
                pass
        timed_out = False
        try:
            exit_code = process.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate_process_tree(process.pid, force=True)
            exit_code = process.wait(timeout=5.0)
        stdout_thread.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)
        return {
            "ok": exit_code == 0 and not timed_out,
            "pid": process.pid,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_ms": round((time.perf_counter() - started) * 1_000, 1),
            "stdout": stdout.result(chosen_encoding, delivery=delivery),
            "stderr": stderr.result(chosen_encoding, delivery=delivery),
        }
    finally:
        stdout.close()
        stderr.close()


@dataclass(slots=True)
class BackgroundRecord:
    process: subprocess.Popen[bytes]
    stdout: OutputCapture
    stderr: OutputCapture
    stdout_thread: threading.Thread
    stderr_thread: threading.Thread
    started_at: str
    cwd: str
    executable: str
    encoding: str


_BACKGROUND: dict[int, BackgroundRecord] = {}
_BACKGROUND_LOCK = threading.Lock()


def start_background(
    command: Sequence[str],
    *,
    cwd: Path,
    capture_limit: int | None = None,
    output_mode: OutputMode,
    env: dict[str, str] | None = None,
    encoding: str | None = None,
) -> dict[str, Any]:
    """Start a process with optional output limits and disk-backed capture."""

    if not command or not command[0]:
        raise ToolError("invalid_command", "An executable is required.")
    if not cwd.is_dir():
        raise ToolError("invalid_cwd", f"Working directory does not exist: {cwd}")
    chosen_encoding = encoding or locale.getpreferredencoding(False) or "utf-8"
    stdout = OutputCapture(capture_limit, output_mode)
    stderr = OutputCapture(capture_limit, output_mode)
    try:
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd),
            env=_process_env(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_creation_flags(),
        )
    except Exception:
        stdout.close()
        stderr.close()
        raise
    stdout_thread = threading.Thread(target=_read_pipe, args=(process.stdout, stdout), daemon=True)
    stderr_thread = threading.Thread(target=_read_pipe, args=(process.stderr, stderr), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    record = BackgroundRecord(
        process=process,
        stdout=stdout,
        stderr=stderr,
        stdout_thread=stdout_thread,
        stderr_thread=stderr_thread,
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        cwd=str(cwd),
        executable=str(command[0]),
        encoding=chosen_encoding,
    )
    with _BACKGROUND_LOCK:
        _BACKGROUND[process.pid] = record
        finished = [pid for pid, item in _BACKGROUND.items() if item.process.poll() is not None]
        for old_pid in finished[:-50]:
            old_record = _BACKGROUND.pop(old_pid, None)
            if old_record is not None:
                _close_background_record(old_record)
    return {
        "ok": True,
        "pid": process.pid,
        "running": True,
        "started_at": record.started_at,
        "cwd": record.cwd,
        "executable": record.executable,
        "capture_limit": capture_limit,
    }


def background_output(pid: int, *, since_byte: int | None = None,
                      stderr_since_byte: int | None = None, delivery: Delivery = "inline") -> dict[str, Any]:
    with _BACKGROUND_LOCK:
        record = _BACKGROUND.get(pid)
    if record is None:
        raise ToolError(
            "output_unavailable",
            f"PID {pid} was not started by this MCP server session.",
            hint="Use process_info for external processes.",
        )
    exit_code = record.process.poll()
    if exit_code is not None:
        record.stdout_thread.join(timeout=0.5)
        record.stderr_thread.join(timeout=0.5)
    return {
        "ok": True,
        "pid": pid,
        "running": exit_code is None,
        "exit_code": exit_code,
        "started_at": record.started_at,
        "stdout": record.stdout.result(record.encoding, since_byte=since_byte, delivery=delivery,
                                       final=not record.stdout_thread.is_alive()),
        "stderr": record.stderr.result(record.encoding, since_byte=stderr_since_byte, delivery=delivery,
                                       final=not record.stderr_thread.is_alive()),
    }


def _close_background_record(record: BackgroundRecord) -> None:
    record.stdout_thread.join(timeout=0.5)
    record.stderr_thread.join(timeout=0.5)
    record.stdout.close()
    record.stderr.close()


def close_background_captures() -> None:
    """Release every spool retained for background-process output."""

    with _BACKGROUND_LOCK:
        records = list(_BACKGROUND.values())
        _BACKGROUND.clear()
    for record in records:
        _close_background_record(record)


atexit.register(close_background_captures)
