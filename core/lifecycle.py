"""Runtime mutation lifecycle and private supervisor drain-file handshake."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from core.errors import ToolError

LifecycleState = Literal["RUNNING", "DRAINING", "STOPPING"]
CONTROL_SCHEMA_VERSION = 1
_STATUS_SCHEMA_VERSION = 1
_MAX_CONTROL_BYTES = 16_384


class RuntimeLifecycle:
    """Track active mutating tools and coordinate bounded supervisor drain requests."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state: LifecycleState = "RUNNING"
        self._active_mutations = 0
        self._request_id: str | None = None
        self._deadline: float | None = None
        self._control_path: Path | None = None
        self._status_path: Path | None = None
        self._pid = os.getpid()
        self._managed = False

    def configure(self, control_path: Path | None, status_path: Path | None) -> None:
        """Reset lifecycle state for one freshly launched MCP runtime."""
        with self._lock:
            self._state = "RUNNING"
            self._active_mutations = 0
            self._request_id = None
            self._deadline = None
            self._control_path = control_path
            self._status_path = status_path
            self._pid = os.getpid()
            self._managed = control_path is not None and status_path is not None
            if control_path is not None:
                control_path.parent.mkdir(parents=True, exist_ok=True)
            if status_path is not None:
                status_path.parent.mkdir(parents=True, exist_ok=True)
            self._publish_locked()

    def configure_from_env(self) -> None:
        control = os.environ.get("MCP_LIFECYCLE_CONTROL_FILE")
        status = os.environ.get("MCP_LIFECYCLE_STATUS_FILE")
        self.configure(Path(control) if control else None, Path(status) if status else None)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        return {
            "schema_version": _STATUS_SCHEMA_VERSION,
            "pid": self._pid,
            "state": self._state,
            "active_mutations": self._active_mutations,
            "request_id": self._request_id,
            "deadline": self._deadline,
            "managed": self._managed,
            "updated_at": time.time(),
        }

    def _publish_locked(self) -> None:
        path = self._status_path
        if path is None:
            return
        payload = json.dumps(self._snapshot_locked(), separators=(",", ":"), sort_keys=True)
        temporary = path.with_name(f".{path.name}.{self._pid}.tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _read_control(self) -> dict[str, Any] | None:
        path = self._control_path
        if path is None:
            return None
        try:
            if path.stat().st_size > _MAX_CONTROL_BYTES:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(payload, dict) or payload.get("schema_version") != CONTROL_SCHEMA_VERSION:
            return None
        return payload

    def poll_control(self) -> LifecycleState:
        """Apply the latest private control request and publish an acknowledgement."""
        request = self._read_control()
        if request is None:
            with self._lock:
                return self._state
        command = request.get("command")
        request_id = request.get("request_id")
        deadline = request.get("deadline")
        if command not in {"drain", "stop"} or not isinstance(request_id, str) or not request_id:
            with self._lock:
                return self._state
        parsed_deadline = float(deadline) if isinstance(deadline, (int, float)) else None
        with self._lock:
            self._request_id = request_id
            self._deadline = parsed_deadline
            if command == "stop":
                self._state = "STOPPING"
            elif self._state == "RUNNING":
                self._state = "DRAINING"
            # Publish on every valid control poll, not only state changes. On
            # Windows an atomic replace can transiently lose to a concurrent
            # status reader; repeated publication prevents a lost drain ack.
            self._publish_locked()
            return self._state

    def mark_stopping(self) -> None:
        with self._lock:
            if not self._managed:
                return
            if self._state != "STOPPING":
                self._state = "STOPPING"
                self._publish_locked()

    def enter_mutation(self, operation: str) -> None:
        # Read the control file before taking the admission decision. A concurrent
        # request arriving just after this read is still safe because the watcher
        # must acknowledge DRAINING before the supervisor considers the runtime drained.
        self.poll_control()
        with self._lock:
            if self._state != "RUNNING":
                code = "runtime_draining" if self._state == "DRAINING" else "runtime_stopping"
                raise ToolError(
                    code,
                    f"Runtime is {self._state.lower()}; mutating operation '{operation}' was not started.",
                    hint="Retry after the supervisor restart completes.",
                )
            self._active_mutations += 1
            self._publish_locked()

    def exit_mutation(self) -> None:
        with self._lock:
            if self._active_mutations <= 0:
                raise RuntimeError("Mutation lifecycle counter underflow.")
            self._active_mutations -= 1
            self._publish_locked()
        # Observe a request that may have arrived while the mutation was running.
        self.poll_control()

    @contextmanager
    def mutation(self, operation: str) -> Iterator[None]:
        with self._lock:
            managed = self._managed
        if not managed:
            yield
            return
        self.enter_mutation(operation)
        try:
            yield
        finally:
            self.exit_mutation()


RUNTIME_LIFECYCLE = RuntimeLifecycle()


def lifecycle_control_request(command: Literal["drain", "stop"], request_id: str, deadline: float) -> dict[str, Any]:
    """Build the small private control-file payload shared with the supervisor."""
    return {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "command": command,
        "request_id": request_id,
        "deadline": deadline,
    }
