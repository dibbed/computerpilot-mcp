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
