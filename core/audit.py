"""Bounded metadata-only audit writer with batching, rotation, and retention."""

from __future__ import annotations

import atexit
import json
import os
import queue
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.config import SETTINGS, Settings

_LOCK_WAIT_SEC = 5.0
_SYNC_FALLBACK_WAIT_SEC = 0.05
_DURABLE_WAIT_SEC = 10.0
_WRITE_RETRIES = 3
_WRITE_RETRY_SEC = 0.01


@dataclass(frozen=True, slots=True)
class AuditPolicy:
    """Runtime audit batching and retention policy."""

    batch_size: int
    flush_interval_sec: float
    queue_max: int
    max_file_bytes: int
    keep_files: int


@dataclass(slots=True)
class _QueuedRecord:
    payload: bytes | None = None
    durable: bool = False
    barrier: bool = False
    stop: bool = False
    completed: threading.Event | None = None


def _policy_from_settings(settings: Settings) -> AuditPolicy:
    return AuditPolicy(
        batch_size=settings.audit_batch_size,
        flush_interval_sec=settings.audit_flush_ms / 1_000,
        queue_max=settings.audit_queue_max,
        max_file_bytes=settings.audit_max_file_bytes,
        keep_files=settings.audit_keep_files,
    )


@contextmanager
def _interprocess_lock(path: Path) -> Iterator[None]:
    """Serialize append/rotation across MCP, supervisor, and durable-worker processes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            deadline = time.monotonic() + _LOCK_WAIT_SEC
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out locking audit file {path}.") from None
                    time.sleep(0.01)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


class AuditWriter:
    """Process-local bounded queue feeding cross-process-safe audit files."""

    def __init__(self, path: Path, policy: AuditPolicy) -> None:
        self.path = path
        self.policy = policy
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._queue: queue.Queue[_QueuedRecord] = queue.Queue(maxsize=policy.queue_max)
        self._thread_lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._stop_completed: threading.Event | None = None
        self._failure_lock = threading.Lock()
        self._failure: BaseException | None = None
        self._prune_excess_rotations()

    def _set_failure(self, exc: BaseException) -> None:
        with self._failure_lock:
            if self._failure is None:
                self._failure = exc

    def _raise_failure(self) -> None:
        with self._failure_lock:
            failure = self._failure
        if failure is not None:
            raise RuntimeError(f"Audit writer failed: {failure}") from failure

    def _rotation_path(self, index: int) -> Path:
        return self.path.with_name(f"{self.path.stem}.{index}{self.path.suffix}")

    def _prune_excess_rotations_locked(self) -> None:
        pattern = re.compile(
            rf"^{re.escape(self.path.stem)}\.(\d+){re.escape(self.path.suffix)}$",
            re.IGNORECASE,
        )
        for candidate in self.path.parent.glob(f"{self.path.stem}.*{self.path.suffix}"):
            match = pattern.fullmatch(candidate.name)
            if match and int(match.group(1)) > self.policy.keep_files:
                candidate.unlink(missing_ok=True)

    def _prune_excess_rotations(self) -> None:
        with _interprocess_lock(self.path):
            self._prune_excess_rotations_locked()

    def _rotate_locked(self, incoming_bytes: int) -> None:
        try:
            current_size = self.path.stat().st_size
        except FileNotFoundError:
            current_size = 0
        if current_size == 0 or current_size + incoming_bytes <= self.policy.max_file_bytes:
            self._prune_excess_rotations_locked()
            return

        oldest = self._rotation_path(self.policy.keep_files)
        oldest.unlink(missing_ok=True)
        for index in range(self.policy.keep_files - 1, 0, -1):
            source = self._rotation_path(index)
            if source.exists():
                os.replace(source, self._rotation_path(index + 1))
        if self.path.exists():
            os.replace(self.path, self._rotation_path(1))
        self._prune_excess_rotations_locked()

    def _fsync_retained_locked(self) -> None:
        candidates = [self.path, *(self._rotation_path(index) for index in range(1, self.policy.keep_files + 1))]
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                with candidate.open("ab", buffering=0) as handle:
                    os.fsync(handle.fileno())
            except FileNotFoundError:
                continue

    def _append_payloads(self, payloads: list[bytes], *, durable: bool) -> None:
        """Append complete JSONL records without letting one batch bypass rotation limits."""

        last_error: OSError | TimeoutError | None = None
        for attempt in range(_WRITE_RETRIES):
            try:
                with _interprocess_lock(self.path):
                    if payloads:
                        try:
                            current_size = self.path.stat().st_size
                        except FileNotFoundError:
                            current_size = 0
                        handle = None
                        pending: list[bytes] = []
                        pending_bytes = 0

                        def flush_pending() -> None:
                            nonlocal handle, current_size, pending, pending_bytes
                            if not pending:
                                return
                            if handle is None:
                                handle = self.path.open("ab", buffering=0)
                            view = memoryview(b"".join(pending))
                            while view:
                                written = handle.write(view)
                                if not written:
                                    raise OSError("Short audit write.")
                                view = view[written:]
                            current_size += pending_bytes
                            pending = []
                            pending_bytes = 0

                        try:
                            for payload in payloads:
                                effective_size = current_size + pending_bytes
                                if effective_size > 0 and effective_size + len(payload) > self.policy.max_file_bytes:
                                    flush_pending()
                                    if handle is not None:
                                        handle.close()
                                        handle = None
                                    self._rotate_locked(len(payload))
                                    try:
                                        current_size = self.path.stat().st_size
                                    except FileNotFoundError:
                                        current_size = 0
                                pending.append(payload)
                                pending_bytes += len(payload)
                            flush_pending()
                            if durable and handle is not None:
                                os.fsync(handle.fileno())
                        finally:
                            if handle is not None:
                                handle.close()
                    elif durable:
                        self._fsync_retained_locked()
                return
            except (OSError, TimeoutError) as exc:
                last_error = exc
                if attempt < _WRITE_RETRIES - 1:
                    time.sleep(_WRITE_RETRY_SEC * (attempt + 1))
        assert last_error is not None
        raise last_error

    def _finish_items(self, items: list[_QueuedRecord]) -> None:
        for item in items:
            self._queue.task_done()
            if item.completed is not None:
                item.completed.set()

    def _flush_batch(self, items: list[_QueuedRecord]) -> bool:
        if not items:
            return True
        try:
            self._append_payloads(
                [item.payload for item in items if item.payload is not None],
                durable=any(item.durable for item in items),
            )
        except BaseException as exc:
            self._set_failure(exc)
            self._finish_items(items)
            return False
        self._finish_items(items)
        return True

    def _handle_control(self, item: _QueuedRecord) -> bool:
        try:
            if item.barrier:
                self._append_payloads([], durable=True)
        except BaseException as exc:
            self._set_failure(exc)
        finally:
            self._queue.task_done()
            if item.completed is not None:
                item.completed.set()
        return item.stop

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item.barrier or item.stop:
                if self._handle_control(item):
                    return
                if self._failure is not None:
                    return
                continue

            batch = [item]
            deadline = time.monotonic() + self.policy.flush_interval_sec
            control: _QueuedRecord | None = None
            while len(batch) < self.policy.batch_size and not any(record.durable for record in batch):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    next_item = self._queue.get(timeout=remaining)
                except queue.Empty:
                    break
                if next_item.barrier or next_item.stop:
                    control = next_item
                    break
                batch.append(next_item)

            if not self._flush_batch(batch):
                if control is not None:
                    self._queue.task_done()
                    if control.completed is not None:
                        control.completed.set()
                return
            if control is not None and self._handle_control(control):
                return

    def _ensure_thread(self) -> None:
        with self._thread_lock:
            if self._closed:
                raise RuntimeError("Audit writer is closed.")
            if self._thread is not None and self._thread.is_alive():
                return
            self._raise_failure()
            self._thread = threading.Thread(target=self._run, name="mcp-audit-writer", daemon=True)
            self._thread.start()

    def submit(self, payload: bytes, *, durable: bool = False) -> None:
        """Queue one record; fall back synchronously without racing writer shutdown."""

        completed = threading.Event() if durable else None
        item = _QueuedRecord(payload=payload, durable=durable, completed=completed)
        with self._thread_lock:
            self._raise_failure()
            self._ensure_thread()
            try:
                self._queue.put(item, timeout=_SYNC_FALLBACK_WAIT_SEC)
            except queue.Full:
                self._append_payloads([payload], durable=durable)
                return

        if completed is not None:
            if not completed.wait(_DURABLE_WAIT_SEC):
                raise TimeoutError("Timed out waiting for durable audit flush.")
            self._raise_failure()

    def flush_now(self) -> None:
        """Flush all previously accepted records and fsync every retained audit segment."""

        completed = threading.Event()
        with self._thread_lock:
            self._raise_failure()
            thread = self._thread
            if thread is None:
                return
            if self._closed:
                stop_completed = self._stop_completed
                if stop_completed is None:
                    raise RuntimeError("Audit writer is closed.")
                completed = stop_completed
            else:
                self._queue.put(_QueuedRecord(barrier=True, completed=completed), timeout=_DURABLE_WAIT_SEC)
        if not completed.wait(_DURABLE_WAIT_SEC):
            raise TimeoutError("Timed out flushing the audit writer.")
        self._raise_failure()

    def close(self) -> None:
        """Drain queued records and stop the writer thread, surfacing bounded shutdown failure."""

        with self._thread_lock:
            self._closed = True
            thread = self._thread
            if thread is None:
                return
            self._raise_failure()
            completed = self._stop_completed
            if completed is None:
                completed = threading.Event()
                self._stop_completed = completed
                try:
                    self._queue.put(
                        _QueuedRecord(barrier=True, stop=True, completed=completed),
                        timeout=_DURABLE_WAIT_SEC,
                    )
                except BaseException:
                    if self._stop_completed is completed:
                        self._stop_completed = None
                    raise
        if not completed.wait(_DURABLE_WAIT_SEC):
            if not thread.is_alive():
                self._raise_failure()
            raise TimeoutError("Timed out draining the audit writer during close.")
        thread.join(timeout=_DURABLE_WAIT_SEC)
        if thread.is_alive():
            raise TimeoutError("Timed out stopping the audit writer thread.")
        self._raise_failure()
        with self._thread_lock:
            if self._thread is thread:
                self._thread = None
            if self._stop_completed is completed:
                self._stop_completed = None


_WRITER_LOCK = threading.Lock()
_WRITER: AuditWriter | None = None
_WRITER_KEY: tuple[object, ...] | None = None


def _writer_key(settings: Settings) -> tuple[object, ...]:
    return (
        str(settings.audit_log),
        settings.audit_batch_size,
        settings.audit_flush_ms,
        settings.audit_queue_max,
        settings.audit_max_file_bytes,
        settings.audit_keep_files,
    )


def _get_writer() -> AuditWriter:
    global _WRITER, _WRITER_KEY
    key = _writer_key(SETTINGS)
    with _WRITER_LOCK:
        if _WRITER is not None and _WRITER_KEY == key:
            return _WRITER
        if _WRITER is not None:
            _WRITER.close()
        _WRITER = AuditWriter(SETTINGS.audit_log, _policy_from_settings(SETTINGS))
        _WRITER_KEY = key
        return _WRITER


def audit_action(
    operation: str,
    *,
    target: str | Path | None = None,
    details: dict[str, Any] | None = None,
    outcome: str = "attempted",
    durable: bool = False,
) -> None:
    """Record metadata only; never store file contents, secrets, or typed text."""

    record = {
        "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "operation": operation,
        "outcome": outcome,
        "pid": os.getpid(),
    }
    if target is not None:
        record["target"] = str(target)
    if details:
        record["details"] = details
    payload = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    _get_writer().submit(payload, durable=durable)


def flush_audit() -> None:
    """Flush queued audit metadata at lifecycle/shutdown boundaries."""

    with _WRITER_LOCK:
        writer = _WRITER
    if writer is not None:
        writer.flush_now()


def close_audit_writer() -> None:
    """Best-effort process-exit drain for the process-local writer."""

    global _WRITER, _WRITER_KEY
    with _WRITER_LOCK:
        writer = _WRITER
        _WRITER = None
        _WRITER_KEY = None
    if writer is None:
        return
    try:
        writer.close()
    except Exception:
        # Interpreter shutdown must not be held open indefinitely. Destructive
        # records use durable=True and are already fsync'd before returning.
        pass


atexit.register(close_audit_writer)
