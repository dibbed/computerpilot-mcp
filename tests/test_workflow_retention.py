from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from mcp import Client

from core import heartbeat, registry, resource_health
from core.config import SETTINGS
from core.registry import create_server
from core.workflow_retention import WorkflowHistoryPolicy, cleanup_workflow_history
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowState, WorkflowStore


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition("history", (StepDefinition("check", "check_file", {"path": "unused"}),))


def test_workflow_retention_prunes_only_safe_terminal_history(tmp_path: Path) -> None:
    path = tmp_path / "workflows.sqlite3"
    store = WorkflowStore(path)
    terminal_ids = [
        str(store.create(_definition(), initial_state=WorkflowState.COMPLETED)["workflow_id"])
        for _ in range(5)
    ]
    protected = str(store.create(_definition(), initial_state=WorkflowState.CANCELLED)["workflow_id"])
    with sqlite3.connect(path) as connection:
        operation_id = connection.execute(
            "SELECT operation_id FROM workflow_operations WHERE workflow_id = ?",
            (protected,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE workflow_operations SET state = 'uncertain' WHERE operation_id = ?",
            (operation_id,),
        )

    result = cleanup_workflow_history(
        path,
        WorkflowHistoryPolicy(max_age_days=0, max_count=2, cleanup_interval_sec=1),
        now=datetime.now(timezone.utc),
    )

    assert result.removed_rows == 3
    assert result.removed_for_count == 3
    assert result.protected_unresolved_rows == 1
    assert store.get(protected)["state"] == "cancelled"
    survivors = {item["workflow_id"] for item in store.list(limit=100)["items"]}
    assert protected in survivors
    assert len(survivors.intersection(terminal_ids)) == 2
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_operations WHERE workflow_id NOT IN (SELECT workflow_id FROM workflows)"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_events WHERE workflow_id NOT IN (SELECT workflow_id FROM workflows)"
        ).fetchone()[0] == 0


def test_workflow_health_exposes_bounded_state_metrics(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    workflow = store.create(_definition(), initial_state=WorkflowState.QUEUED)
    health = store.health_summary()

    assert health["workflow_total"] == 1
    assert health["operation_total"] == 1
    assert health["workflow_operation_total"] == 1
    assert health["event_total"] >= 1
    assert health["workflow_event_total"] == health["event_total"]
    assert health["queued_workflows"] == 1
    assert health["running_workflows"] == 0
    assert health["unresolved_workflow_operations"] == health["unresolved_operation_count"] == 0
    assert health["active_workflow_leases"] == health["active_lease_count"] == 0
    assert health["workflow_db_bytes"] > 0
    assert workflow["workflow_id"]


def test_server_health_marks_critical_resource_pressure_unhealthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = replace(SETTINGS, state_dir=tmp_path, memory_dir=tmp_path / "memory")
    monkeypatch.setattr(registry, "SETTINGS", settings)
    monkeypatch.setattr(resource_health, "SETTINGS", settings)
    monkeypatch.setattr(heartbeat, "SETTINGS", settings)

    async def fake_stats() -> dict[str, object]:
        return {
            "sessions": 0,
            "browser_instances": 0,
            "contexts": 0,
            "pools": [],
        }

    monkeypatch.setattr(registry.BROWSER_MANAGER, "stats", fake_stats)
    monkeypatch.setattr(
        registry,
        "collect_resource_metrics",
        lambda _: {
            "resource_pressure": "critical",
            "budgets": {},
            "rss_mb": 1.0,
        },
    )

    async def scenario() -> None:
        async with Client(create_server()) as client:
            response = await client.call_tool("server_health", {})
            assert response.structured_content
            health = response.structured_content
            assert health["ok"] is True
            assert health["health_status"] == "unhealthy"
            assert "resource_pressure" in health["degraded_reasons"]

    asyncio.run(scenario())


def test_server_health_marks_unresolved_workflow_as_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = replace(SETTINGS, state_dir=tmp_path, memory_dir=tmp_path / "memory")
    monkeypatch.setattr(registry, "SETTINGS", settings)
    monkeypatch.setattr(resource_health, "SETTINGS", settings)
    monkeypatch.setattr(heartbeat, "SETTINGS", settings)

    store = WorkflowStore(settings.workflow_db)
    workflow = store.create(_definition(), initial_state=WorkflowState.UNCERTAIN)
    with sqlite3.connect(settings.workflow_db) as connection:
        connection.execute(
            "UPDATE workflow_operations SET state = 'uncertain' WHERE workflow_id = ?",
            (workflow["workflow_id"],),
        )

    async def scenario() -> None:
        async with Client(create_server()) as client:
            response = await client.call_tool("server_health", {})
            assert response.structured_content
            health = response.structured_content
            assert health["ok"] is True
            assert health["health_status"] == "degraded"
            assert "unresolved_uncertain_operations" in health["degraded_reasons"]
            assert health["unresolved_workflow_operations"] == 1
            assert health["workflow_total"] == health["workflow_health"]["workflow_total"]
            assert health["workflow_operation_total"] == health["workflow_health"]["workflow_operation_total"]
            assert health["workflow_event_total"] == health["workflow_health"]["workflow_event_total"]
            assert health["active_workflow_leases"] == health["workflow_health"]["active_workflow_leases"]
            assert health["workflow_health"]["unresolved_operation_count"] == 1

    asyncio.run(scenario())
