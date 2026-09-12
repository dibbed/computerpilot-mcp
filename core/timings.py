"""Opt-in structured performance timing with metadata-only JSONL records."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext

from core.config import SETTINGS

_TRUE_VALUES = {"1", "true", "yes", "on"}
_CURRENT_TOOL: ContextVar[str | None] = ContextVar("mcp_current_tool", default=None)
_WRITE_LOCK = threading.Lock()


def timings_enabled() -> bool:
    return os.getenv("MCP_TIMINGS", "").strip().casefold() in _TRUE_VALUES


def timing_file() -> Path:
    configured = os.getenv("MCP_TIMINGS_FILE")
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return SETTINGS.state_dir / "timings.jsonl"


def current_tool_name() -> str | None:
    return _CURRENT_TOOL.get()


def _safe_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None or isinstance(value, (bool, int, float)):
            safe[str(key)] = value
        elif isinstance(value, str):
            safe[str(key)] = value[:200]
    return safe


def record_timing(
    phase: str,
    duration_ms: float,
    *,
    tool: str | None = None,
    method: str | None = None,
    ok: bool = True,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """Append one timing record without allowing observability failures to affect tools."""

    if not timings_enabled():
        return
    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "phase": phase,
        "duration_ms": round(max(duration_ms, 0.0), 3),
        "ok": ok,
    }
    resolved_tool = tool if tool is not None else current_tool_name()
    if resolved_tool:
        record["tool"] = resolved_tool
    if method:
        record["method"] = method
    record.update(_safe_metadata(metadata))
    try:
        target = timing_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _WRITE_LOCK:
            with target.open("a", encoding="utf-8", newline="") as handle:
                handle.write(payload)
    except OSError:
        return


@contextmanager
def timing_span(phase: str, *, metadata: Mapping[str, Any] | None = None) -> Iterator[None]:
    """Measure one internal phase when timing is enabled."""

    if not timings_enabled():
        yield
        return
    started = time.perf_counter()
    try:
        yield
    except BaseException:
        record_timing(phase, (time.perf_counter() - started) * 1_000, ok=False, metadata=metadata)
        raise
    else:
        record_timing(phase, (time.perf_counter() - started) * 1_000, metadata=metadata)


@contextmanager
def tool_timing(operation: str) -> Iterator[None]:
    """Attach a tool name to nested spans and measure the tool body."""

    token = _CURRENT_TOOL.set(operation)
    try:
        with timing_span("tool_body"):
            yield
    finally:
        _CURRENT_TOOL.reset(token)


class ToolRequestTimingMiddleware:
    """Measure the MCP tools/call pipeline before wire serialization."""

    async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
        if ctx.method != "tools/call" or not timings_enabled():
            return await call_next(ctx)
        tool_name: str | None = None
        if isinstance(ctx.params, Mapping):
            raw_name = ctx.params.get("name")
            if isinstance(raw_name, str):
                tool_name = raw_name
        started = time.perf_counter()
        try:
            result = await call_next(ctx)
        except BaseException:
            record_timing(
                "request_pipeline",
                (time.perf_counter() - started) * 1_000,
                tool=tool_name,
                method=ctx.method,
                ok=False,
            )
            raise
        record_timing(
            "request_pipeline",
            (time.perf_counter() - started) * 1_000,
            tool=tool_name,
            method=ctx.method,
        )
        return result
