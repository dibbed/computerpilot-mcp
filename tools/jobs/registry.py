"""Durable jobs for builds, tests, analysis, and retry-safe command submission."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from core.artifacts import default_delivery
from core.audit import audit_action
from core.config import resolve_path
from core.job_scheduler import ensure_job_scheduler
from core.jobs import JobStore
from core.tooling import DESTRUCTIVE, OPEN_WORLD_WRITE, READ_ONLY, compact_errors

JobId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


def register(mcp: MCPServer) -> None:
    store = JobStore()
    ensure_job_scheduler(store)
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
    ) -> dict[str, Any]:
        """Submit a retry-safe durable command. Execution timeout starts after launch; optional queue timeout bounds pre-launch waiting."""
        audit_action("submit_job", target=resolve_path(cwd), details={"executable": executable})
        return store.submit(
            [executable, *(args or [])],
            resolve_path(cwd),
            timeout_sec,
            idempotency_key,
            encoding,
            queue_timeout_sec=queue_timeout_sec,
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("job_status")
    def job_status(job_id: JobId) -> dict[str, Any]:
        """Read a durable job status by ID after reconnect or server restart."""
        return {"ok": True, **store.get(job_id)}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("list_jobs")
    def list_jobs(offset: Annotated[int, Field(ge=0)] = 0,
                  max_items: Annotated[int, Field(ge=1, le=500)] = 50) -> dict[str, Any]:
        """List persistent jobs, newest first, with pagination."""
        return store.list(offset, max_items)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("job_output")
    def job_output(job_id: JobId, since_byte: Annotated[int, Field(ge=0)] = 0,
                   stderr_since_byte: Annotated[int, Field(ge=0)] = 0,
                   delivery: Literal["inline", "file", "auto"] = output_default) -> dict[str, Any]:
        """Read full or incremental durable output; file/auto returns a snapshot path and SHA-256."""
        return store.output(job_id, since_byte, stderr_since_byte, delivery)

    @mcp.tool(annotations=DESTRUCTIVE, structured_output=True)
    @compact_errors("cancel_job")
    def cancel_job(job_id: JobId) -> dict[str, Any]:
        """Request cancellation of a job and its command process tree."""
        audit_action("cancel_job", target=job_id)
        return store.cancel(job_id)
