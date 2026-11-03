"""Process-local identity and explicit cancellation for in-flight tool operations.

Cancellation over stdio rides the MCP request lifetime: the SDK cancels the
handler task and the terminal tools reap their owned process tree. Streamable
HTTP is served statelessly, so the server never observes a client disconnect and
cannot cancel the request that way. For HTTP callers the supported contract is
``cancel_operation`` with the client-chosen ``operation_id`` supplied to the
terminal tool. The identifier is a process-local bearer capability, not
authentication: anyone who can reach the same MCP server and learns the id can
request cancellation. It is not persisted and is unknown after a restart.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import anyio
import anyio.to_thread

from core.errors import ToolError

OperationState = Literal["running", "cancelled", "completed", "failed"]

_OPERATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_TERMINAL_RETENTION_SEC = 300.0
_TERMINAL_MAX_ENTRIES = 256


def new_operation_id() -> str:
    return f"op-{secrets.token_hex(16)}"


def validate_operation_id(operation_id: str) -> str:
    if not isinstance(operation_id, str) or not _OPERATION_ID_PATTERN.fullmatch(operation_id):
        raise ToolError(
            "invalid_operation_id",
            "operation_id must be 8..128 characters of A-Z, a-z, 0-9, '.', '_', ':' or '-', starting alphanumeric.",
        )
    return operation_id


@dataclass(slots=True)
class OperationHandle:
    operation_id: str
    tool: str
    started_at: float = field(default_factory=time.monotonic)
    state: OperationState = "running"
    finished_at: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "tool": self.tool,
            "state": self.state,
            "cancel_requested": self.cancel_event.is_set(),
            "started_at": round(self.started_at, 3),
        }


class OperationRegistry:
    """Bounded process-local registry of in-flight operations."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._operations: dict[str, OperationHandle] = {}

    def _purge_locked(self, now: float) -> None:
        terminal = [
            (handle.finished_at or handle.started_at, operation_id)
            for operation_id, handle in self._operations.items()
            if handle.state != "running"
        ]
        terminal.sort()
        for _, operation_id in terminal:
            handle = self._operations.get(operation_id)
            if handle is not None and handle.finished_at is not None and now - handle.finished_at > _TERMINAL_RETENTION_SEC:
                self._operations.pop(operation_id, None)
        overflow = len(self._operations) - _TERMINAL_MAX_ENTRIES
        if overflow > 0:
            for _, operation_id in terminal[:overflow]:
                self._operations.pop(operation_id, None)

    def reserve(self, operation_id: str | None, tool: str) -> OperationHandle:
        resolved = validate_operation_id(operation_id) if operation_id is not None else new_operation_id()
        now = time.monotonic()
        with self._lock:
            self._purge_locked(now)
            existing = self._operations.get(resolved)
            if existing is not None and existing.state == "running":
                raise ToolError(
                    "operation_id_in_use",
                    "operation_id is already used by an in-flight operation.",
                    hint="Choose a fresh identifier for each concurrent call.",
                )
            handle = OperationHandle(resolved, tool)
            self._operations[resolved] = handle
            return handle

    def finish(self, handle: OperationHandle, state: OperationState) -> None:
        with self._lock:
            current = self._operations.get(handle.operation_id)
            if current is not handle:
                return
            if handle.state == "cancelled" and state != "cancelled":
                state = "cancelled"
            handle.state = state
            handle.finished_at = time.monotonic()

    def request_cancel(self, operation_id: str) -> dict[str, Any]:
        resolved = validate_operation_id(operation_id)
        with self._lock:
            handle = self._operations.get(resolved)
            if handle is None:
                return {
                    "ok": False,
                    "operation_id": resolved,
                    "cancelled": False,
                    "state": "not_found",
                    "reason": "operation_not_found",
                }
            if handle.state != "running":
                return {
                    "ok": True,
                    "operation_id": resolved,
                    "cancelled": False,
                    "state": handle.state,
                    "reason": "operation_finished",
                    **handle.public(),
                }
            already = handle.cancel_event.is_set()
            handle.cancel_event.set()
            return {
                "ok": True,
                "operation_id": resolved,
                "cancelled": True,
                "already_requested": already,
                **handle.public(),
                "state": "cancelling",
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            running = [handle.public() for handle in self._operations.values() if handle.state == "running"]
        return {"tracked": len(self._operations), "running": len(running)}


OPERATIONS = OperationRegistry()


async def run_cancellable_operation(
    operation_id: str | None,
    tool: str,
    action: Callable[[threading.Event], dict[str, Any]],
) -> dict[str, Any]:
    """Run one blocking owned operation off-loop and reap it when the request is cancelled.

    The blocking worker owns the process tree. When the awaiting MCP request is
    cancelled the SDK cancels this task; the worker is abandoned only after it
    has been signalled, so the tree is reaped and the resource claim is released
    by the worker itself instead of lingering until the command timeout.
    """

    handle = OPERATIONS.reserve(operation_id, tool)
    cancel = handle.cancel_event
    outcome: OperationState = "failed"

    def worker() -> dict[str, Any]:
        return action(cancel)

    try:
        result = await anyio.to_thread.run_sync(worker, abandon_on_cancel=True)
        outcome = "completed"
        if isinstance(result, dict):
            result.setdefault("operation_id", handle.operation_id)
        return result
    except ToolError as exc:
        outcome = "cancelled" if exc.code == "command_cancelled" else "failed"
        raise
    except anyio.get_cancelled_exc_class():
        # The owner (client cancellation or server shutdown) is gone: signal the
        # worker so it terminates the owned process tree promptly.
        cancel.set()
        outcome = "cancelled"
        raise
    finally:
        OPERATIONS.finish(handle, outcome)
