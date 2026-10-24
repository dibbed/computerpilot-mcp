from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer

from core.jobs import JobStore
from tools.jobs import registry


@pytest.fixture(autouse=True)
def isolated_observability(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import audit

    monkeypatch.setattr(audit, "SETTINGS", replace(audit.SETTINGS, state_dir=tmp_path / "audit"))
    monkeypatch.setenv("MCP_TIMINGS_FILE", str(tmp_path / "timings.jsonl"))


def test_job_tools_share_lazy_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import registry as server_registry

    created: list[object] = []
    submitted: list[object] = []

    class FakeStore:
        def list(self, offset: int, max_items: int) -> dict[str, Any]:
            return {"ok": True, "total_count": 0, "items": []}

        def submit(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            submitted.append(self)
            return {"ok": True, "job_id": "0" * 32, "status": "queued"}

    def factory() -> Any:
        store = FakeStore()
        created.append(store)
        return store

    monkeypatch.setattr(server_registry, "JobStore", factory)

    async def scenario() -> None:
        server = server_registry.create_server()
        await server.list_tools()
        assert created == []
        await asyncio.gather(*(server.call_tool("list_jobs", {}) for _ in range(24)))
        await server.call_tool("start_validation_watch", {"repo": str(tmp_path)})
        assert len(created) == 1
        assert submitted == created

    asyncio.run(scenario())


def test_lifespan_recovers_queued_jobs_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import heartbeat, job_scheduler
    from core.jobs import lazy_job_store

    store = JobStore(tmp_path / "jobs.sqlite3")
    with monkeypatch.context() as quiet:
        quiet.setattr(job_scheduler, "ensure_job_scheduler", lambda store: None)
        queued = store.submit(["python", "-V"], tmp_path, 5, "restart-recovery")
    launched: list[str] = []

    def launch(self: Any, job_id: str, token: str) -> None:
        launched.append(job_id)
        self.store.fail_worker_launch(job_id, token, "fixture launch observed")

    monkeypatch.setattr(job_scheduler.JobScheduler, "_launch_worker", launch)
    monkeypatch.setattr(heartbeat, "ensure_runtime_dirs", lambda: None)
    monkeypatch.setattr(heartbeat.BACKUP_RETENTION, "schedule", lambda: None)
    monkeypatch.setattr(heartbeat.SEARCH_SNAPSHOTS, "cleanup", lambda: None)
    for name in ("schedule_artifact_retention", "schedule_job_history_retention", "schedule_workflow_history_retention"):
        monkeypatch.setattr(heartbeat, name, lambda **kwargs: None)
    for key in ("MCP_HEARTBEAT_FILE", "MCP_RUNTIME_HEALTH_FILE", "MCP_LIFECYCLE_CONTROL_FILE", "MCP_LIFECYCLE_STATUS_FILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(heartbeat, "SETTINGS", replace(heartbeat.SETTINGS, state_dir=tmp_path))
    constructions: list[JobStore] = []

    def reopen() -> JobStore:
        result = JobStore(store.path)
        constructions.append(result)
        return result

    server = SimpleNamespace(job_store_getter=lazy_job_store(reopen), runtime_started=False)

    async def scenario() -> None:
        for _ in range(2):
            async with heartbeat.lifespan(server):
                assert server.runtime_started is True
            assert server.runtime_started is False

    asyncio.run(scenario())
    assert launched == [queued["job_id"]]
    assert len(constructions) == 1


def test_lifespan_store_failure_keeps_activation_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import heartbeat

    def fail() -> JobStore:
        raise RuntimeError("schema unsupported")

    monkeypatch.setattr(heartbeat, "ensure_runtime_dirs", lambda: None)
    server = SimpleNamespace(job_store_getter=fail, runtime_started=False)

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="schema unsupported"):
            async with heartbeat.lifespan(server):
                pytest.fail("startup failure must not yield")

    asyncio.run(scenario())
    assert server.runtime_started is False


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
        assert stores == []
        async with Client(servers[0]) as client:
            results = await asyncio.gather(*(client.call_tool("list_jobs", {}) for _ in range(24)))
        assert all(result.structured_content and result.structured_content["total_count"] == 0 for result in results)
        assert len(stores) == 1
        async with Client(servers[1]) as client:
            await client.call_tool("list_jobs", {})
        assert len(stores) == 2
        assert stores[0].path != stores[1].path

    asyncio.run(scenario())
