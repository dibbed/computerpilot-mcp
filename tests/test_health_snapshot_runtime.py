from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from core.health_snapshot import collect_server_health


def test_health_metrics_collection_does_not_block_event_loop() -> None:
    entered = threading.Event()

    class Server:
        async def list_tools(self) -> list[Any]:
            return []

    class Browser:
        async def stats(self) -> dict[str, int]:
            return {}

    class Workflows:
        def health_summary(self) -> dict[str, int]:
            return {
                "unresolved_operation_count": 0, "workflow_db_bytes": 0,
                "workflow_total": 0, "workflow_operation_total": 0,
                "workflow_event_total": 0, "active_workflow_leases": 0,
                "queued_workflows": 0, "running_workflows": 0,
                "uncertain_workflows": 0, "unresolved_workflow_operations": 0,
            }

        def list(self, **kwargs: Any) -> dict[str, Any]:
            return {"items": [], "total_count": 0}

    class Recovery:
        def summary(self, **kwargs: Any) -> dict[str, int]:
            return {"uncertain_count": 0, "pending_count": 0}

    def slow_disk_and_sqlite_metrics(_: dict[str, int]) -> dict[str, Any]:
        entered.set()
        time.sleep(0.35)
        return {"budgets": {}, "resource_pressure": "normal"}

    async def scenario() -> None:
        task = asyncio.create_task(collect_server_health(
            Server(), browser_manager=Browser(), resource_collector=slow_disk_and_sqlite_metrics,
            recovery=Recovery(), workflow_store_factory=lambda _: Workflows(),
        ))
        try:
            started = time.perf_counter()
            await asyncio.sleep(0.02)
            assert entered.is_set()
            assert time.perf_counter() - started < 0.2
        finally:
            await task

    asyncio.run(scenario())


def test_concurrent_health_requests_serialize_shared_store_reads() -> None:
    active = 0
    peak = 0
    guard = threading.Lock()

    class Server:
        async def list_tools(self) -> list[Any]:
            return []

    class Browser:
        async def stats(self) -> dict[str, int]:
            return {}

    class Workflows:
        def health_summary(self) -> dict[str, int]:
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with guard:
                active -= 1
            return {
                "unresolved_operation_count": 0, "workflow_db_bytes": 0,
                "workflow_total": 0, "workflow_operation_total": 0,
                "workflow_event_total": 0, "active_workflow_leases": 0,
                "queued_workflows": 0, "running_workflows": 0,
                "uncertain_workflows": 0, "unresolved_workflow_operations": 0,
            }

        def list(self, **kwargs: Any) -> dict[str, Any]:
            return {}

    class Recovery:
        def summary(self, **kwargs: Any) -> dict[str, int]:
            return {"uncertain_count": 0, "pending_count": 0}

    async def scenario() -> None:
        await asyncio.gather(*(
            collect_server_health(
                Server(), browser_manager=Browser(), resource_collector=lambda _: {"budgets": {}},
                recovery=Recovery(), workflow_store_factory=lambda _: Workflows(),
            ) for _ in range(5)
        ))

    asyncio.run(scenario())
    assert peak == 1

def test_health_exposes_bounded_execution_router_snapshot_without_extra_io() -> None:
    class Server:
        async def list_tools(self) -> list[Any]:
            return []

    class Browser:
        async def stats(self) -> dict[str, int]:
            return {}

    class Workflows:
        def health_summary(self) -> dict[str, int]:
            return {
                "unresolved_operation_count": 0, "workflow_db_bytes": 0,
                "workflow_total": 0, "workflow_operation_total": 0,
                "workflow_event_total": 0, "active_workflow_leases": 0,
                "queued_workflows": 0, "running_workflows": 0,
                "uncertain_workflows": 0, "unresolved_workflow_operations": 0,
            }

        def list(self, **kwargs: Any) -> dict[str, Any]:
            return {"items": [], "total_count": 0}

    class Recovery:
        def summary(self, **kwargs: Any) -> dict[str, int]:
            return {"uncertain_count": 0, "pending_count": 0}

    class Metrics:
        calls = 0

        def snapshot(self) -> dict[str, Any]:
            self.calls += 1
            return {
                "decision_count": 3,
                "no_route_count": 1,
                "rejected_candidate_count": 4,
                "fallback_count": 1,
                "fallback_rate": 1 / 3,
                "route_distribution": {"native.git": 2},
                "usage": {"native": 2, "semantic": 0, "raw": 0, "visual": 0, "other": 0},
                "latency_ms": {"average": 0.2, "max": 0.5},
                "recent_decisions": [{"intent": "git.read", "selected_route": "native.git"}],
                "recent_limit": 50,
            }

    metrics = Metrics()

    async def scenario() -> dict[str, Any]:
        return await collect_server_health(
            Server(),
            browser_manager=Browser(),
            resource_collector=lambda _: {"budgets": {}, "resource_pressure": "normal"},
            recovery=Recovery(),
            workflow_store_factory=lambda _: Workflows(),
            router_metrics=metrics,
        )

    result = asyncio.run(scenario())
    router = result["execution_router"]
    assert metrics.calls == 1
    assert router["enabled"] is True
    assert router["policy_version"] == "deterministic-v1"
    assert router["decision_count"] == 3
    assert router["rejected_candidate_count"] == 4
    assert router["recent_decisions"] == [{"intent": "git.read", "selected_route": "native.git"}]
