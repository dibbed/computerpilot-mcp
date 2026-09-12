"""Opt-in structured performance timing with metadata-only JSONL records."""

from __future__ import annotations

import atexit
import functools
import json
import os
import queue
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from pydantic import BaseModel

from core.config import SETTINGS

_TRUE_VALUES = {"1", "true", "yes", "on"}
_CURRENT_TOOL: ContextVar[str | None] = ContextVar("mcp_current_tool", default=None)


@dataclass(slots=True)
class _RequestTimingState:
    request_started: float
    tool: str | None = None
    validation_finished: float | None = None


_REQUEST_STATE: ContextVar[_RequestTimingState | None] = ContextVar("mcp_request_timing_state", default=None)
_WRITER_QUEUE: queue.Queue[tuple[Path, dict[str, Any]] | None] = queue.Queue(maxsize=10_000)
_WRITER_START_LOCK = threading.Lock()
_WRITER_THREAD: threading.Thread | None = None
_SDK_HOOK_LOCK = threading.Lock()
_SDK_HOOKS_INSTALLED = False


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


def _writer_loop() -> None:
    while True:
        first = _WRITER_QUEUE.get()
        if first is None:
            _WRITER_QUEUE.task_done()
            return
        batch: dict[Path, list[dict[str, Any]]] = {first[0]: [first[1]]}
        consumed = 1
        stop_after_batch = False
        time.sleep(0.005)
        while consumed < 256:
            try:
                item = _WRITER_QUEUE.get_nowait()
            except queue.Empty:
                break
            if item is None:
                stop_after_batch = True
                _WRITER_QUEUE.task_done()
                break
            batch.setdefault(item[0], []).append(item[1])
            consumed += 1
        try:
            for target, payloads in batch.items():
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("a", encoding="utf-8", newline="") as handle:
                        handle.write(
                            "".join(
                                json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                                for record in payloads
                            )
                        )
                except OSError:
                    pass
        finally:
            for _ in range(consumed):
                _WRITER_QUEUE.task_done()
        if stop_after_batch:
            return


def _ensure_writer() -> None:
    global _WRITER_THREAD
    thread = _WRITER_THREAD
    if thread is not None and thread.is_alive():
        return
    with _WRITER_START_LOCK:
        thread = _WRITER_THREAD
        if thread is not None and thread.is_alive():
            return
        thread = threading.Thread(target=_writer_loop, name="mcp-timing-writer", daemon=True)
        thread.start()
        _WRITER_THREAD = thread


def flush_timings() -> None:
    """Wait until all queued timing records have been written."""

    _WRITER_QUEUE.join()


def _shutdown_writer() -> None:
    thread = _WRITER_THREAD
    if thread is None or not thread.is_alive():
        return
    flush_timings()
    _WRITER_QUEUE.put(None)
    _WRITER_QUEUE.join()
    thread.join(timeout=2.0)


atexit.register(_shutdown_writer)


def _enqueue_record(target: Path, record: dict[str, Any]) -> None:
    _ensure_writer()
    try:
        _WRITER_QUEUE.put_nowait((target, record))
    except queue.Full:
        # Observability must never block or break a tool call.
        return


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
        _enqueue_record(timing_file(), record)
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
    """Attach a tool name to nested spans and measure dispatch wait plus tool body."""

    state = _REQUEST_STATE.get()
    started = time.perf_counter()
    if state is not None:
        state.tool = operation
        if state.validation_finished is not None:
            record_timing(
                "queue_wait",
                (started - state.validation_finished) * 1_000,
                tool=operation,
            )
    token = _CURRENT_TOOL.set(operation)
    try:
        with timing_span("tool_body"):
            yield
    finally:
        _CURRENT_TOOL.reset(token)


def install_sdk_timing_hooks() -> None:
    """Instrument the pinned MCP SDK's validation and result-conversion seams once."""

    global _SDK_HOOKS_INSTALLED
    if _SDK_HOOKS_INSTALLED:
        return
    with _SDK_HOOK_LOCK:
        if _SDK_HOOKS_INSTALLED:
            return
        from mcp.server.mcpserver.utilities.func_metadata import FuncMetadata

        original_validate = FuncMetadata.validate_arguments
        original_convert = FuncMetadata.convert_result

        @functools.wraps(original_validate)
        def timed_validate(self: Any, arguments_to_validate: dict[str, Any]) -> dict[str, Any]:
            if not timings_enabled():
                return original_validate(self, arguments_to_validate)
            state = _REQUEST_STATE.get()
            started = time.perf_counter()
            try:
                result = original_validate(self, arguments_to_validate)
            except BaseException:
                finished = time.perf_counter()
                if state is not None:
                    state.validation_finished = finished
                record_timing(
                    "validation",
                    (finished - started) * 1_000,
                    tool=state.tool if state else None,
                    ok=False,
                )
                raise
            finished = time.perf_counter()
            if state is not None:
                state.validation_finished = finished
            record_timing(
                "validation",
                (finished - started) * 1_000,
                tool=state.tool if state else None,
            )
            return result

        @functools.wraps(original_convert)
        def timed_convert(self: Any, result: Any) -> Any:
            if not timings_enabled():
                return original_convert(self, result)
            state = _REQUEST_STATE.get()
            started = time.perf_counter()
            try:
                converted = original_convert(self, result)
            except BaseException:
                record_timing(
                    "result_conversion",
                    (time.perf_counter() - started) * 1_000,
                    tool=state.tool if state else None,
                    ok=False,
                )
                raise
            record_timing(
                "result_conversion",
                (time.perf_counter() - started) * 1_000,
                tool=state.tool if state else None,
            )
            return converted

        FuncMetadata.validate_arguments = timed_validate  # type: ignore[method-assign]
        FuncMetadata.convert_result = timed_convert  # type: ignore[method-assign]
        _SDK_HOOKS_INSTALLED = True


def _serialization_probe(result: HandlerResult) -> tuple[float, int] | None:
    """Measure an equivalent response serialization without retaining payload contents."""

    if result is None:
        return None
    started = time.perf_counter()
    if isinstance(result, BaseModel):
        payload = result.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")
    elif isinstance(result, dict):
        payload = json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    else:
        return None
    return (time.perf_counter() - started) * 1_000, len(payload)


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
        state = _RequestTimingState(request_started=started, tool=tool_name)
        token = _REQUEST_STATE.set(state)
        try:
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
            probe = _serialization_probe(result)
            if probe is not None:
                duration_ms, payload_bytes = probe
                record_timing(
                    "serialization",
                    duration_ms,
                    tool=tool_name,
                    method=ctx.method,
                    metadata={"bytes": payload_bytes, "probe": True},
                )
            return result
        finally:
            _REQUEST_STATE.reset(token)
