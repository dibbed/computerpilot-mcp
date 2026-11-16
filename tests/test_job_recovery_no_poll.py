"""Recovery after a client never polls a durable background job."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest
from mcp import Client
from mcp.server import MCPServer

from core.jobs import JobStore
from tools.jobs.registry import register

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}


def test_failed_job_is_recoverable_after_client_never_polls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Use a real spawned worker; deliberately do NOT call MCP job_wait.
    path = tmp_path / "jobs.sqlite3"
    command = [sys.executable, "-c", "import sys; print('durable-failed-result'); sys.exit(1)"]
    store = JobStore(path)
    submission = store.submit(command, tmp_path, 25, "failed-without-poll")
    job_id = str(submission["job_id"])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = store.get(job_id)
        if state["status"] in TERMINAL:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("Durable worker never reached a terminal state")

    # A new MCP client observes the same retained failure after the old turn is gone.
    async def resume() -> None:
        server = MCPServer("resumed-session")
        register(server, store_getter=lambda: JobStore(path))
        async with Client(server) as client:
            listed = await client.call_tool("list_jobs", {"max_items": 5})
            assert listed.structured_content
            assert any(row["job_id"] == job_id for row in listed.structured_content["items"])
            result = await client.call_tool("job_status", {"job_id": job_id})
            assert result.structured_content
            assert result.structured_content["status"] == "failed"
            assert result.structured_content["exit_code"] == 1
            output = await client.call_tool("job_output", {"job_id": job_id, "delivery": "inline"})
            assert output.structured_content
            assert "durable-failed-result" in output.structured_content["stdout"]["text"]

    asyncio.run(resume())
    reopened = JobStore(path)
    same = reopened.submit(command, tmp_path, 25, "failed-without-poll")
    assert same["deduplicated"] is True
    assert same["job_id"] == job_id
    assert reopened.list(0, 10)["total_count"] == 1
