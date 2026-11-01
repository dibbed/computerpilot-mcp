"""Bounded process-local parallelism for explicitly read-only shell commands.

Exclusive commands and durable jobs still take the host-wide write coordination
claim, so they serialize by design. A caller that explicitly classifies a
command as read-only may run it under a read claim: independent read commands
(and native file reads) overlap, while any writer to the same subtree, or any
exclusive command, still conflicts. This gate bounds how many such commands one
server process runs at once so a burst cannot exhaust the host.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from core.config import SETTINGS
from core.errors import ToolError


class SharedCommandGate:
    """Counting gate that re-reads the configured limit on every acquisition."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active = 0

    @property
    def active(self) -> int:
        with self._condition:
            return self._active

    @contextmanager
    def slot(self, timeout_sec: float, cancel_event: threading.Event | None = None) -> Iterator[None]:
        deadline = time.monotonic() + max(0.001, timeout_sec)
        with self._condition:
            while True:
                if self._active < max(1, SETTINGS.command_shared_concurrency):
                    self._active += 1
                    break
                if cancel_event is not None and cancel_event.is_set():
                    raise ToolError("command_cancelled", "The command was cancelled while waiting to start.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ToolError(
                        "command_concurrency_timeout",
                        "No shared command slot became available before the timeout.",
                        hint="Retry later or raise MCP_COMMAND_SHARED_CONCURRENCY within its bounded maximum.",
                    )
                self._condition.wait(min(0.05, remaining))
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify()


SHARED_COMMANDS = SharedCommandGate()
