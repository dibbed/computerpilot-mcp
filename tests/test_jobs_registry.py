from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp import Client
from mcp.server import MCPServer

from core.jobs import JobStore
from tools.jobs import registry


def test_submit_job_forwards_optional_queue_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeStore:
        def submit(
            self,
            command: list[str],
            cwd: Path,
            timeout_sec: float,
            request_key: str,
            encoding: str = "utf-8",
            *,
            queue_timeout_sec: float | None = None,
        ) -> dict[str, object]:
            captured.update(
                command=command,
                cwd=cwd,
                timeout_sec=timeout_sec,
                request_key=request_key,
                encoding=encoding,
                queue_timeout_sec=queue_timeout_sec,
            )
            return {"ok": True, "job_id": "0" * 32, "status": "queued", "version": 1}

    monkeypatch.setattr(registry, "JobStore", FakeStore)
    async def scenario() -> None:
        server = MCPServer("jobs")
        registry.register(server)
        async with Client(server) as client:
            result = await client.call_tool(
                "submit_job",
                {
                    "executable": "python",
                    "idempotency_key": "queue-key",
                    "args": ["-V"],
                    "cwd": str(tmp_path),
                    "timeout_sec": 12,
                    "queue_timeout_sec": 3,
                },
            )
        assert result.structured_content and result.structured_content["ok"] is True

    asyncio.run(scenario())
    assert captured["command"] == ["python", "-V"]
    assert captured["timeout_sec"] == 12
    assert captured["queue_timeout_sec"] == 3
    assert captured["request_key"] == "queue-key"


def test_registration_reuses_one_store_per_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stores: list[JobStore] = []

    def create_store() -> JobStore:
        store = JobStore(tmp_path / str(len(stores)) / "jobs.sqlite3")
        stores.append(store)
        return store

    monkeypatch.setattr(registry, "JobStore", create_store)

    async def scenario() -> None:
        servers = [MCPServer("first"), MCPServer("second")]
        for server in servers:
            registry.register(server)
        async with Client(servers[0]) as client:
            results = await asyncio.gather(*(client.call_tool("list_jobs", {}) for _ in range(24)))
        assert all(result.structured_content and result.structured_content["total_count"] == 0 for result in results)
        assert len(stores) == 2
        assert stores[0].path != stores[1].path

    asyncio.run(scenario())
