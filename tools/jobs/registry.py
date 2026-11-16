"""Durable jobs for builds, tests, analysis, and retry-safe command submission."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal, cast, get_args

from mcp.server import MCPServer
from pydantic import BaseModel, ConfigDict, Field

from core.artifacts import default_delivery
from core.audit import audit_action
from core.config import resolve_path
from core.errors import ToolError
from core.jobs import (
    JOB_PROGRESS_CHUNK_BYTES,
    JOB_WAIT_HEARTBEAT_SEC,
    MAX_JOB_PROGRESS_CHUNK_BYTES,
    MAX_JOB_WAIT_SEC,
    JobStore,
    lazy_job_store,
)
from core.resource_claims import ResourceClaim, path_claim
from core.response_models import JobStatus, JobStatusResult, validate_tool_output
from core.timings import record_timing, timings_enabled
from core.tooling import DESTRUCTIVE, OPEN_WORLD_WRITE, READ_ONLY, compact_errors

JobId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
MAX_BATCH_PROGRESS_TOTAL_BYTES = 128 * 1024


class JobResourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=32767)
    mode: Literal["read", "write"] = "write"
    scope: Literal["exact", "subtree"] = "exact"


class BatchJobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    executable: str = Field(min_length=1, max_length=32767)
    idempotency_key: str = Field(min_length=1, max_length=200)
    args: list[str] = Field(default_factory=list, max_length=200)
    cwd: str = Field(default=".", max_length=32767)
    timeout_sec: float = Field(default=3600, gt=0, le=86400)
    encoding: str = Field(default="utf-8", min_length=1, max_length=40)
    queue_timeout_sec: float | None = Field(default=None, gt=0, le=86400)
    execution_mode: Literal["exclusive", "declared"] = "exclusive"
    resources: list[JobResourceSpec] | None = Field(default=None, max_length=32)


class JobSnapshotSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    stdout_since_byte: int = Field(default=0, ge=0)
    stderr_since_byte: int = Field(default=0, ge=0)
    after_version: int = Field(default=0, ge=0)


def _declared_resources(
    cwd: str, mode: Literal["exclusive", "declared"], resources: list[JobResourceSpec] | None,
) -> tuple[ResourceClaim, ...] | None:
    if mode == "exclusive":
        if resources is not None:
            raise ToolError("job_resource_mode_invalid", "Resources require execution_mode='declared'.")
        return None
    if not resources:
        raise ToolError("job_resource_mode_invalid", "Declared parallel execution requires explicit resource claims.")
    working = resolve_path(cwd)
    return tuple(path_claim(resolve_path(str(
        Path(resource.path) if Path(resource.path).is_absolute() else working / resource.path
    )), resource.mode, scope=resource.scope) for resource in resources)


def _queue_wait_seconds(state: dict[str, Any]) -> float:
    """Human-readable queue age, not an execution or admission timeout."""
    created = state.get("created")
    if isinstance(created, bool) or not isinstance(created, (int, float)) or not math.isfinite(created):
        return 0.0
    return round(max(time.time() - float(created), 0.0), 3)


def register(mcp: MCPServer, *, store_getter: Callable[[], JobStore] | None = None) -> None:
    get_store = store_getter or lazy_job_store(lambda: JobStore())
    output_default = default_delivery()

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("submit_job")
    def submit_job(
        executable: Annotated[str, Field(min_length=1, max_length=32767)],
        idempotency_key: Annotated[str, Field(min_length=1, max_length=200)],
        args: Annotated[list[str] | None, Field(max_length=200)] = None,
        cwd: Annotated[str, Field(max_length=32767)] = ".",
        timeout_sec: Annotated[float, Field(gt=0, le=86400)] = 3600,
        encoding: Annotated[str, Field(min_length=1, max_length=40)] = "utf-8",
        queue_timeout_sec: Annotated[float | None, Field(gt=0, le=86400)] = None,
        execution_mode: Literal["exclusive", "declared"] = "exclusive",
        resources: Annotated[list[JobResourceSpec] | None, Field(max_length=32)] = None,
    ) -> dict[str, Any]:
        """Durable job; default exclusive. Accepted is not success; check final job_status. Parallel jobs require declared resources."""
        working = resolve_path(cwd)
        declared = _declared_resources(str(working), execution_mode, resources)
        audit_action("submit_job", target=working, details={"executable": executable, "execution_mode": execution_mode})
        submit_options: dict[str, Any] = {"queue_timeout_sec": queue_timeout_sec}
        if declared is not None:
            submit_options["parallel_resources"] = declared
        result = get_store().submit(
            [executable, *(args or [])], working, timeout_sec, idempotency_key, encoding, **submit_options,
        )
        try:
            audit_action(
                "submit_job", outcome="completed",
                details={"job_id": result["job_id"], "deduplicated": result["deduplicated"]},
            )
        except Exception:
            pass
        return result

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("submit_jobs")
    def submit_jobs(
        jobs: Annotated[list[BatchJobSpec], Field(min_length=1, max_length=16)],
    ) -> dict[str, Any]:
        """Submit up to 16 independent durable jobs in one request; each has its own ID and output."""
        keys = [job.idempotency_key for job in jobs]
        if len(set(keys)) != len(keys):
            raise ToolError("duplicate_job_request_key", "Each batch item requires a distinct idempotency key.")
        # Resolve and validate every resource declaration before the first submission.
        prepared = [
            (job, resolve_path(job.cwd), _declared_resources(job.cwd, job.execution_mode, job.resources))
            for job in jobs
        ]
        audit_action("submit_jobs", details={"job_count": len(jobs), "declared_count": sum(
            item.execution_mode == "declared" for item in jobs
        )})
        store = get_store()
        items: list[dict[str, Any]] = []
        for index, (job, working, declared) in enumerate(prepared):
            options: dict[str, Any] = {"queue_timeout_sec": job.queue_timeout_sec}
            if declared is not None:
                options["parallel_resources"] = declared
            try:
                result = store.submit(
                    [job.executable, *job.args], working, job.timeout_sec,
                    job.idempotency_key, job.encoding, **options,
                )
            except (ToolError, ValueError) as exc:
                items.append({
                    "ok": False, "index": index, "idempotency_key": job.idempotency_key,
                    "error": exc.message if isinstance(exc, ToolError) else str(exc),
                    "error_code": exc.code if isinstance(exc, ToolError) else "invalid_job",
                })
            else:
                items.append({"ok": True, "index": index, "idempotency_key": job.idempotency_key, **result})
        accepted = sum(bool(item["ok"]) for item in items)
        return {
            "ok": accepted == len(items), "accepted": accepted,
            "failed": len(items) - accepted, "items": items,
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("jobs_snapshot")
    def jobs_snapshot(
        jobs: Annotated[list[JobSnapshotSpec], Field(min_length=1, max_length=16)],
        progress_bytes: Annotated[int, Field(ge=16, le=MAX_JOB_PROGRESS_CHUNK_BYTES)] = JOB_PROGRESS_CHUNK_BYTES,
    ) -> dict[str, Any]:
        """Read up to 16 job states and independent bounded stdout/stderr chunks with byte cursors."""
        ids = [job.job_id for job in jobs]
        if len(ids) != len(set(ids)):
            raise ToolError("duplicate_job_id", "Snapshot job IDs must be distinct.")
        store = get_store()
        blocked_ids = store.resource_waiting_ids(ids)
        # Limit the aggregate tool response, not just each individual stream.
        # At 16 jobs the former per-stream cap could return ~2 MiB at once.
        per_stream_limit = min(progress_bytes, MAX_BATCH_PROGRESS_TOTAL_BYTES // (2 * len(jobs)))
        items: list[dict[str, Any]] = []
        for job in jobs:
            try:
                state = store.get(job.job_id)
                if int(state["version"]) < job.after_version:
                    raise ToolError("job_version_ahead", "Requested version exceeds the retained job version.")
                progress = store.progress(
                    job.job_id,
                    stdout_since_byte=job.stdout_since_byte,
                    stderr_since_byte=job.stderr_since_byte,
                    max_bytes=per_stream_limit,
                )
            except (ToolError, ValueError) as exc:
                items.append({
                    "ok": False, "job_id": job.job_id,
                    "error": exc.message if isinstance(exc, ToolError) else str(exc),
                    "error_code": exc.code if isinstance(exc, ToolError) else "invalid_cursor",
                })
            else:
                items.append({
                    "ok": True, **state,
                    **({"queue_phase": "waiting_resources" if job.job_id in blocked_ids else "awaiting_worker",
                        "queue_wait_seconds": _queue_wait_seconds(state)}
                       if state["status"] == "queued" else {}),
                    "changed": int(state["version"]) > job.after_version,
                    **progress,
                })
        return {
            "ok": all(item["ok"] for item in items),
            "all_terminal": all(
                item["ok"] and item["status"] not in {"queued", "running", "orphaned"} for item in items
            ),
            "progress_bytes_per_stream": per_stream_limit,
            "items": items,
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("job_status")
    def job_status(job_id: JobId) -> JobStatusResult:
        """Read a durable job status by ID after reconnect or server restart. Prefer job_wait to poll."""
        store = get_store()
        result = {"ok": True, **store.get(job_id)}
        if result.get("status") == "queued":
            result["queue_phase"] = (
                "waiting_resources" if job_id in store.resource_waiting_ids([job_id]) else "awaiting_worker"
            )
        if result.get("status") == "queued":
            result["queue_wait_seconds"] = _queue_wait_seconds(result)
        if result.get("status") not in get_args(JobStatus):
            raise ToolError("output_version_unsupported", "The stored job status version is unsupported.")
        return cast(JobStatusResult, validate_tool_output(JobStatusResult, result, operation="job_status"))

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("job_wait")
    def job_wait(
        job_id: JobId,
        after_version: Annotated[int, Field(ge=0)],
        timeout: Annotated[float, Field(ge=0, le=MAX_JOB_WAIT_SEC)] = 30,
        heartbeat_sec: Annotated[float, Field(gt=0, le=30)] = JOB_WAIT_HEARTBEAT_SEC,
        stdout_since_byte: Annotated[int, Field(ge=0)] = 0,
        stderr_since_byte: Annotated[int, Field(ge=0)] = 0,
        progress_bytes: Annotated[int, Field(ge=16, le=MAX_JOB_PROGRESS_CHUNK_BYTES)] = JOB_PROGRESS_CHUNK_BYTES,
    ) -> dict[str, Any]:
        """Wait for job state with bounded silence, plus incremental stdout/stderr progress and resumable cursors."""
        started = time.monotonic()
        store = get_store()
        state = store.wait(job_id, after_version, 0)
        progress = store.progress(
            job_id,
            stdout_since_byte=stdout_since_byte,
            stderr_since_byte=stderr_since_byte,
            max_bytes=progress_bytes,
        )
        progressed = (
            int(progress["stdout"]["bytes"]) > 0
            or int(progress["stderr"]["bytes"]) > 0
        )
        heartbeat = False
        if not state["changed"] and not progressed and timeout > 0:
            effective_timeout = min(timeout, heartbeat_sec)
            state = store.wait(job_id, after_version, effective_timeout)
            progress = store.progress(
                job_id,
                stdout_since_byte=stdout_since_byte,
                stderr_since_byte=stderr_since_byte,
                max_bytes=progress_bytes,
            )
            progressed = (
                int(progress["stdout"]["bytes"]) > 0
                or int(progress["stderr"]["bytes"]) > 0
            )
            heartbeat = bool(state["timed_out"] and timeout > effective_timeout and not progressed)

        if state["timed_out"] and (heartbeat or progressed):
            state = {**state, "timed_out": False}
        if timings_enabled():
            record_timing(
                "job_wait", (time.monotonic() - started) * 1_000,
                metadata={"job_id": job_id, "job_correlation_id": store.trace_metadata(job_id).get("correlation_id")},
            )
        return {
            **state,
            "heartbeat": heartbeat,
            "progressed": progressed,
            "waited_seconds": round(time.monotonic() - started, 3),
            **progress,
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("list_jobs")
    def list_jobs(offset: Annotated[int, Field(ge=0)] = 0,
                  max_items: Annotated[int, Field(ge=1, le=500)] = 50) -> dict[str, Any]:
        """List persistent jobs, newest first, with pagination."""
        return get_store().list(offset, max_items)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("job_output")
    def job_output(job_id: JobId, since_byte: Annotated[int, Field(ge=0)] = 0,
                   stderr_since_byte: Annotated[int, Field(ge=0)] = 0,
                   delivery: Literal["inline", "file", "auto"] = output_default) -> dict[str, Any]:
        """Read full or incremental durable output; file/auto returns a snapshot path and SHA-256."""
        return get_store().output(job_id, since_byte, stderr_since_byte, delivery)

    @mcp.tool(annotations=DESTRUCTIVE, structured_output=True)
    @compact_errors("cancel_job")
    def cancel_job(job_id: JobId) -> dict[str, Any]:
        """Request cancellation of a job and its command process tree."""
        audit_action("cancel_job", target=job_id, durable=True)
        return get_store().cancel(job_id)
