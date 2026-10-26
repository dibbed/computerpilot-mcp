"""One bounded file-read request; native policy and reader remain authoritative."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from mcp.server import MCPServer
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator

from core.config import resolve_path
from core.errors import ToolError
from core.lifecycle import RUNTIME_LIFECYCLE
from core.resource_claims import coordination_claim, managed_resource_claims, path_claim
from core.response_models import NativeFailure, WireRootModel, validate_tool_output
from core.state_paths import unlinked_directory_tree, unlinked_regular_file
from core.timings import timing_span
from core.workflow_store import WorkflowStore
from tools.filesystem import service

MAX_RESPONSE_BYTES = 192 * 1024
MAX_TEXT_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 8 * 1024 * 1024


class ReadItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: Annotated[str, Field(pattern="^[A-Za-z0-9_-]{1,32}$")]
    kind: Literal["read_file"] = "read_file"
    path: Annotated[str, Field(min_length=1, max_length=32_767)]
    start_line: Annotated[StrictInt, Field(ge=1, le=10_000_000)] = 1
    end_line: Annotated[StrictInt, Field(ge=1, le=10_000_000)] | None = None
    offset: Annotated[StrictInt, Field(ge=0, le=2 * 1024 * 1024)] = 0
    max_chars: Annotated[StrictInt, Field(ge=1, le=16_384)] = 4096
    encoding: Literal["auto", "utf-8", "utf-8-sig", "utf-16", "utf-32"] = "auto"

    @model_validator(mode="after")
    def valid_window(self) -> ReadItem:
        if self.end_line is not None and self.end_line < self.start_line:
            raise ValueError("Invalid line window.")
        return self


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    root: Annotated[str, Field(min_length=1, max_length=32_767)]
    items: Annotated[list[ReadItem], Field(min_length=1, max_length=16)]
    max_parallel: Annotated[StrictInt, Field(ge=1, le=4)] = 1
    timeout_sec: Annotated[float, Field(gt=0, le=60, allow_inf_nan=False)] = 10

    @model_validator(mode="after")
    def valid_budget(self) -> BatchRequest:
        if len({item.id for item in self.items}) != len(self.items) or sum(item.max_chars for item in self.items) > MAX_TEXT_BYTES:
            raise ValueError("Duplicate identity or excessive output budget.")
        return self


class ReadOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["success", "error", "not_started", "deadline", "cancelled"]
    elapsed_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    complete: bool
    truncated: bool
    content: Annotated[str, Field(max_length=16_384)] | None = None
    encoding: Annotated[str, Field(max_length=40)] | None = None
    error: Annotated[str, Field(max_length=40)] | None = None
    read_sha256: Annotated[str, Field(pattern="^[0-9a-f]{64}$")] | None = None
    source_bytes: Annotated[int, Field(ge=0, le=2 * 1024 * 1024)] | None = None
    next_offset: int | None = None


class BatchSuccess(BaseModel):
    ok: bool
    items: Annotated[list[ReadOutcome], Field(min_length=1, max_length=16)]
    complete: bool
    truncated: bool


class BatchResult(WireRootModel[BatchSuccess | NativeFailure]):
    pass


def _error(item: ReadItem, code: str, elapsed: float = 0) -> dict[str, Any]:
    status = {"batch_deadline": "deadline", "batch_cancelled": "cancelled",
              "batch_not_started": "not_started", "batch_draining": "not_started"}.get(code, "error")
    return {"id": item.id, "status": status, "error": code, "elapsed_ms": elapsed, "complete": False, "truncated": False}


def _bounded_content(content: str, budget: int) -> str:
    content = content.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
    low, high = 0, len(content)
    while low < high:
        middle = (low + high + 1) // 2
        if len(json.dumps(content[:middle], ensure_ascii=False).encode("utf-8")) <= 2 * budget + 2:
            low = middle
        else:
            high = middle - 1
    return content[:low]


class BatchReader:
    """Four admitted responses and four actual reads shared by one server."""

    def __init__(self, server: MCPServer) -> None:
        self.server = server
        self._lock = threading.Lock()
        self._batches = 0
        self._reads = 0
        self._read_slots = threading.BoundedSemaphore(4)
        self._claim_store_lock = threading.Lock()
        self._claim_store: WorkflowStore | None = None

    def _store(self) -> WorkflowStore:
        # Reuse the existing durable claim authority; initializing it for each
        # parallel read would serialize schema checks behind a migration lock.
        with self._claim_store_lock:
            if self._claim_store is None:
                from core.config import SETTINGS

                self._claim_store = WorkflowStore(SETTINGS.workflow_db, recover=False)
            return self._claim_store

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {"active_batches": self._batches, "active_reads": self._reads,
                    "reserved_response_bytes": self._batches * MAX_RESPONSE_BYTES}

    async def _eligible(self) -> None:
        allowed = getattr(self.server, "native_allowlist", None)
        names = {"read_file", "native_read_batch"}
        if allowed is not None and not names <= allowed:
            raise ToolError("batch_hidden_tool", "The required native tools are unavailable.")
        tools = {tool.name: tool for tool in await MCPServer.list_tools(self.server)}
        for name in names:
            tool = tools.get(name)
            annotations = tool.annotations if tool is not None else None
            if annotations is None or annotations.read_only_hint is not True or annotations.open_world_hint is not False:
                raise ToolError("batch_hidden_tool", "The required native tools are unavailable.")

    @staticmethod
    async def _thread(call: Any, cancelled: threading.Event) -> Any:
        task = asyncio.create_task(asyncio.to_thread(call))
        interrupted = False
        try:
            while True:
                try:
                    value = await asyncio.shield(task)
                    break
                except asyncio.CancelledError:
                    if task.done() and task.cancelled():
                        raise
                    interrupted = True
                    cancelled.set()
        except BaseException:
            if interrupted:
                raise asyncio.CancelledError from None
            raise
        if interrupted:
            raise asyncio.CancelledError
        return value

    @staticmethod
    def _preflight(request: BatchRequest, deadline: float, cancelled: threading.Event) -> tuple[Path, tuple[int, int], list[Any]]:
        if not service.local_read_path(Path(request.root)):
            raise ToolError("batch_path_denied", "Only local roots are accepted.")
        try:
            root = resolve_path(request.root).resolve(strict=True)
        except (OSError, ValueError):
            raise ToolError("batch_path_denied", "Only local roots are accepted.") from None
        if not root.is_dir() or not unlinked_directory_tree(root):
            raise ToolError("batch_path_denied", "Only local roots are accepted.")
        info = root.stat()
        plan: list[Any] = []
        total = 0
        for item in request.items:
            if cancelled.is_set() or time.monotonic() >= deadline:
                plan.append("batch_cancelled" if cancelled.is_set() else "batch_deadline")
                continue
            try:
                path = root / item.path
                if not service.local_read_path(path) or not unlinked_regular_file(path) or not unlinked_directory_tree(path.parent):
                    raise ValueError
                target = path.resolve(strict=True)
                if not target.is_relative_to(root):
                    raise ValueError
                size = target.stat().st_size
                if size > 2 * 1024 * 1024:
                    plan.append("batch_source_large")
                    continue
                total += size
                plan.append((target, size))
            except (OSError, ValueError):
                plan.append("batch_path_denied")
        if total > MAX_SOURCE_BYTES:
            raise ToolError("batch_source_budget", "The batch source byte budget is exceeded.")
        return root, (info.st_dev, info.st_ino), plan

    def _read(self, path: Path, item: ReadItem, limits: service.ReadWindowLimits) -> dict[str, Any]:
        limits.check()
        with managed_resource_claims(
            (coordination_claim(), path_claim(path, "read")), category="native.read_batch",
            timeout_sec=max(0.001, limits.deadline - time.monotonic()), store=self._store(),
        ):
            limits.check()
            return service.read_window(str(path), start_line=item.start_line, end_line=item.end_line,
                                       offset=item.offset, max_chars=item.max_chars, encoding=item.encoding, limits=limits)

    async def execute(self, root: str, items: Any, max_parallel: int = 1, timeout_sec: float = 10) -> BatchResult:
        entered = time.monotonic()
        try:
            request = BatchRequest.model_validate({"root": root, "items": items, "max_parallel": max_parallel, "timeout_sec": timeout_sec})
        except ValidationError:
            raise ToolError("batch_invalid", "The batch input is invalid or exceeds its budget.") from None
        await self._eligible()
        if RUNTIME_LIFECYCLE.snapshot()["state"] != "RUNNING":
            raise ToolError("batch_draining", "The runtime is draining.")
        with self._lock:
            if self._batches >= 4:
                raise ToolError("batch_busy", "The bounded batch capacity is occupied.")
            self._batches += 1
        cancelled = threading.Event()
        deadline = entered + request.timeout_sec
        try:
            root_path, identity, plan = await self._thread(lambda: self._preflight(request, deadline, cancelled), cancelled)
            outcomes = [_error(item, entry if isinstance(entry, str) else "batch_not_started")
                        for item, entry in zip(request.items, plan, strict=True)]
            indices = iter(range(len(plan)))

            async def consume() -> None:
                for index in indices:
                    if isinstance(plan[index], str):
                        continue
                    item = request.items[index]
                    path, size = plan[index]
                    limits = service.ReadWindowLimits(root_path, identity, size, deadline, cancelled)
                    started = time.monotonic()
                    acquired = False
                    try:
                        limits.check()
                        await self._eligible()
                        if RUNTIME_LIFECYCLE.snapshot()["state"] != "RUNNING":
                            outcomes[index] = _error(item, "batch_draining")
                            continue
                        while not self._read_slots.acquire(blocking=False):
                            limits.check()
                            await asyncio.sleep(0.005)
                        acquired = True
                        with self._lock:
                            self._reads += 1
                        with timing_span("batch_read", metadata={"item_index": index}):
                            value = await self._thread(partial(self._read, path, item, limits), cancelled)
                        limits.check()
                        text = _bounded_content(value["content"], item.max_chars)
                        truncated = bool(value["truncated"] or text != value["content"])
                        outcomes[index] = {
                            "id": item.id, "status": "success", "elapsed_ms": (time.monotonic() - started) * 1000,
                            "content": text, "encoding": value["encoding"], "complete": not truncated, "truncated": truncated,
                            "read_sha256": value["read_sha256"], "source_bytes": value["source_bytes"],
                            "next_offset": item.offset + len(text) if truncated else None,
                        }
                    except Exception as exc:
                        code = exc.code if isinstance(exc, ToolError) else "batch_read_failed"
                        if code not in {
                            "batch_deadline", "batch_cancelled", "batch_source_changed", "batch_path_denied", "batch_hidden_tool",
                        }:
                            code = "batch_scope_denied" if code.startswith("resource_") else "batch_read_failed"
                        outcomes[index] = _error(item, code, (time.monotonic() - started) * 1000)
                    finally:
                        if acquired:
                            with self._lock:
                                self._reads -= 1
                            self._read_slots.release()

            tasks = [asyncio.create_task(consume()) for _ in range(min(request.max_parallel, len(plan)))]
            group = asyncio.gather(*tasks, return_exceptions=True)
            interrupted = False
            while True:
                try:
                    finished = await asyncio.shield(group)
                    break
                except asyncio.CancelledError:
                    interrupted = True
                    cancelled.set()
                    for task in tasks:
                        task.cancel()
            if interrupted:
                raise asyncio.CancelledError
            for result in finished:
                if isinstance(result, BaseException):
                    raise result
            payload = {"ok": all(row["status"] == "success" for row in outcomes), "items": outcomes,
                       "complete": all(row["complete"] for row in outcomes),
                       "truncated": any(row["truncated"] for row in outcomes)}
            if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_RESPONSE_BYTES:
                raise ToolError("batch_output_budget", "The bounded batch response budget is exceeded.")
            return cast(BatchResult, validate_tool_output(BatchResult, payload, operation="native_read_batch"))
        finally:
            cancelled.set()
            with self._lock:
                self._batches -= 1
