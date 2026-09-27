"""Small metadata-only tracker for MCP tool request activity.

The supervisor and the MCP server run in different processes in tunnel mode.
This module publishes a tiny generation-scoped JSON snapshot so the supervisor
can distinguish "no request reached the MCP" from "a tool is still running".
No tool arguments, results, file contents, or secrets are persisted.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = 1


class ToolActivityTracker:
    """Track bounded request metadata and optionally publish it atomically."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._path: Path | None = None
        self._generation_id: str | None = None
        self._pid = os.getpid()
        self._next_token = 0
        self._active_started: dict[int, float] = {}
        self._total_calls = 0
        self._last_tool_name: str | None = None
        self._last_received_at: float | None = None
        self._last_completed_at: float | None = None
        self._last_duration_ms: float | None = None
        self._last_ok: bool | None = None
        self._updated_at = time.time()

    def configure(self, path: Path | None, generation_id: str | None = None) -> None:
        """Reset process-local activity state for one runtime generation."""

        with self._lock:
            self._path = path
            self._generation_id = generation_id
            self._pid = os.getpid()
            self._next_token = 0
            self._active_started.clear()
            self._total_calls = 0
            self._last_tool_name = None
            self._last_received_at = None
            self._last_completed_at = None
            self._last_duration_ms = None
            self._last_ok = None
            self._updated_at = time.time()
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
            self._publish_locked()

    def configure_from_env(self) -> None:
        raw_path = os.environ.get("MCP_TOOL_ACTIVITY_FILE", "").strip()
        generation_id = os.environ.get("MCP_RUNTIME_GENERATION_ID", "").strip() or None
        self.configure(Path(raw_path) if raw_path else None, generation_id)

    def _snapshot_locked(self) -> dict[str, Any]:
        oldest = min(self._active_started.values()) if self._active_started else None
        return {
            "schema_version": _SCHEMA_VERSION,
            "pid": self._pid,
            "generation_id": self._generation_id,
            "active_calls": len(self._active_started),
            "total_calls": self._total_calls,
            "last_tool_name": self._last_tool_name,
            "last_received_at": self._last_received_at,
            "last_completed_at": self._last_completed_at,
            "last_duration_ms": self._last_duration_ms,
            "last_ok": self._last_ok,
            "oldest_active_started_at": oldest,
            "updated_at": self._updated_at,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def _publish_locked(self) -> None:
        path = self._path
        if path is None:
            return
        payload = json.dumps(self._snapshot_locked(), separators=(",", ":"), sort_keys=True)
        temporary = path.with_name(f".{path.name}.{self._pid}.tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            # Observability is best-effort and must never break a tool call.
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def begin(self, operation: str) -> tuple[int, float]:
        now = time.time()
        started = time.perf_counter()
        with self._lock:
            self._next_token += 1
            token = self._next_token
            self._active_started[token] = now
            self._total_calls += 1
            self._last_tool_name = operation
            self._last_received_at = now
            self._updated_at = now
            self._publish_locked()
        return token, started

    def finish(self, token: int, started: float, *, ok: bool) -> None:
        now = time.time()
        duration_ms = max((time.perf_counter() - started) * 1_000, 0.0)
        with self._lock:
            self._active_started.pop(token, None)
            self._last_completed_at = now
            self._last_duration_ms = round(duration_ms, 3)
            self._last_ok = ok
            self._updated_at = now
            self._publish_locked()

    @contextmanager
    def track(self, operation: str) -> Iterator[None]:
        token, started = self.begin(operation)
        try:
            yield
        except BaseException:
            self.finish(token, started, ok=False)
            raise
        else:
            self.finish(token, started, ok=True)


TOOL_ACTIVITY = ToolActivityTracker()
