from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from mcp import Client

from core import artifacts, jobs
from core.registry import create_server


def test_jobs_and_artifacts_across_mcp_clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "SETTINGS", replace(jobs.SETTINGS, state_dir=tmp_path))
    monkeypatch.setattr(artifacts, "SETTINGS", replace(artifacts.SETTINGS, state_dir=tmp_path))

    async def scenario() -> None:
        args = {"executable": sys.executable, "args": ["-c", "print('hello')"],
                "cwd": str(tmp_path), "idempotency_key": "mcp-retry"}
        async with Client(create_server()) as client:
            response = await client.call_tool("submit_job", args)
            assert response.structured_content
            job_id = response.structured_content["job_id"]
        async with Client(create_server()) as client:
            response = await client.call_tool("submit_job", args)
            assert response.structured_content and response.structured_content["deduplicated"]
            assert response.structured_content["job_id"] == job_id
            for _ in range(100):
                response = await client.call_tool("job_status", {"job_id": job_id})
                assert response.structured_content
                if response.structured_content["status"] == "succeeded":
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("Job failed to finish")
            response = await client.call_tool("job_output", {"job_id": job_id, "delivery": "file"})
            assert response.structured_content
            assert Path(response.structured_content["stdout"]["path"]).read_text().strip() == "hello"
            source = tmp_path / "example.txt"
            source.write_text("full file content", encoding="utf-8")
            response = await client.call_tool("read_file", {"path": str(source), "delivery": "file"})
            assert response.structured_content
            assert "content" not in response.structured_content
            assert Path(response.structured_content["artifact"]["path"]).read_text() == "full file content"

    asyncio.run(scenario())
