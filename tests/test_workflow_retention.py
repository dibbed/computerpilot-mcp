from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from mcp import Client

from core import heartbeat, registry, resource_health, workflow_retention
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


def test_workflow_history_cleanup_skips_linked_state_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    store = WorkflowStore(outside / "workflows.sqlite3")
    old = str(store.create(_definition(), initial_state=WorkflowState.COMPLETED)["workflow_id"])
    store.create(_definition(), initial_state=WorkflowState.COMPLETED)
    link = tmp_path / ".agent_state"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("Directory links are unavailable")

    result = cleanup_workflow_history(
        link / "workflows.sqlite3", WorkflowHistoryPolicy(0, 1, 1),
    )
    assert result.removed_rows == 0
    assert store.get(old)["state"] == "completed"


def test_retention_rechecks_lease_acquired_after_eligibility_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "workflows.sqlite3"
    store = WorkflowStore(path)
    protected = str(store.create(_definition(), initial_state=WorkflowState.COMPLETED)["workflow_id"])
    store.create(_definition(), initial_state=WorkflowState.COMPLETED)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE workflows SET updated_at='2020-01-01T00:00:00.000+00:00' WHERE workflow_id=?", (protected,))

    real_connect = sqlite3.connect
    inserted = False

    class InjectLease(sqlite3.Connection):
        def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
            nonlocal inserted
            if sql == "BEGIN IMMEDIATE" and not inserted:
                inserted = True
                with real_connect(path) as other:
                    other.execute(
                        "INSERT INTO workflow_leases VALUES (?,?,?,?,?,?,?)",
                        (
                            protected, "another-worker", "token", "2026-09-29T00:00:00+00:00",
                            "2026-09-29T00:00:00+00:00", "2030-01-01T00:00:00+00:00", 1,
                        ),
                    )
            return super().execute(sql, parameters)

    monkeypatch.setattr(workflow_retention.sqlite3, "connect", lambda *args, **kwargs: real_connect(*args, factory=InjectLease, **kwargs))
    cleanup_workflow_history(
        path, WorkflowHistoryPolicy(max_age_days=1, max_count=0, cleanup_interval_sec=1),
        now=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )

    assert inserted
    assert store.get(protected)["state"] == "completed"


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
