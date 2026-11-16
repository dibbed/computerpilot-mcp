"""Read-only queue-phase timing at the public job MCP boundary."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.server import MCPServer

from core.jobs import JobStore
from tools.jobs.registry import register


def test_queued_job_exposes_bounded_elapsed_wait_in_status_and_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import job_scheduler

    store = JobStore(tmp_path / "jobs.sqlite3")
    monkeypatch.setattr(job_scheduler, "ensure_job_scheduler", lambda store: None)
    submission = store.submit([sys.executable, "-V"], tmp_path, 30, "queue-diagnostic")
    job_id = str(submission["job_id"])
    assert submission["status"] == "queued"

    async def scenario() -> None:
        server = MCPServer("job-queue-timing")
        register(server, store_getter=lambda: store)
        async with Client(server) as client:
            result = await client.call_tool("job_status", {"job_id": job_id})
            status = result.structured_content
            assert status is not None
            assert status["queue_phase"] == "awaiting_worker"
            assert 0 <= status["queue_wait_seconds"] < 20
            snapshot = await client.call_tool(
                "jobs_snapshot", {"jobs": [{"job_id": job_id, "after_version": 0}]}
            )
            entry = snapshot.structured_content["items"][0]
            assert entry["queue_phase"] == "awaiting_worker"
            assert 0 <= entry["queue_wait_seconds"] < 20
            assert entry["status"] == "queued"

    asyncio.run(scenario())
