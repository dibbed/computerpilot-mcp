from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp import Client
from mcp.server import MCPServer

from core.jobs import JobStore
from tools.jobs import registry


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
