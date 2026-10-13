from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import anyio
import pytest
from mcp import types
from test_extension_security import admitted_call

from core.extensions.manager import ExtensionLifecycleManager
from core.extensions.models import LIMITS, ExtensionEnvironment, ExtensionState, ExtensionToolDescriptor, MutationClass, ToolPolicy


def test_memory_snapshot_matches_live_catalog_and_retirement_without_provider_rpc() -> None:
    manager, _, session = admitted_call({"type": "object"}, types.CallToolResult(content=[]))
    before = manager.health_snapshot()
    assert before["configured_count"] == before["healthy_count"] == before["live_count"] == 1
    assert before["discovered_tool_count"] == before["eligible_tool_count"] == 1
    row = before["items"][0]
    assert row["state"] == ExtensionState.HEALTHY.value and row["routable"]
    assert datetime.fromisoformat(row["last_changed_at"]).tzinfo is not None
    assert row["last_success_at"] is not None
    assert row["state_age_sec"] >= 0
    manager.registry.retire_extension_catalog("security-fixture")
    after = manager.health_snapshot()
    assert after["catalog_generation"] > before["catalog_generation"]
    assert after["discovered_tool_count"] == after["eligible_tool_count"] == 0
    assert not after["items"][0]["routable"]
    assert before["discovered_tool_count"] == 1
    session.send_request.assert_not_called()


def test_disabled_is_neutral_and_standalone_mutation_is_not_eligible() -> None:
    from dataclasses import replace

    manager, _, session = admitted_call({"type": "object"}, types.CallToolResult(content=[]))
    descriptor = manager.list_tools()[0]
    descriptor = replace(descriptor, catalog_generation=descriptor.catalog_generation + 1,
                         policy=descriptor.policy.model_copy(update={"mutation_class": MutationClass.REMOTE_MUTATION}))
    manager.registry.publish_extension_catalog(descriptor.extension_id, descriptor.catalog_generation, (descriptor,))
    assert manager.health_snapshot(unresolved_external_count=0)["eligible_tool_count"] == 0
    anyio.run(manager.disable_extension, descriptor.extension_id)
    disabled = manager.health_snapshot()
    assert disabled["disabled_count"] == 1
    assert disabled["failed_count"] == disabled["degraded_count"] == disabled["eligible_tool_count"] == 0
    session.send_request.assert_not_called()


def test_health_canary_and_unknown_errors_never_cross_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    canary = "private-health-credential-6b12"
    ref = "HEALTH_CREDENTIAL_REF"
    monkeypatch.setenv(ref, canary)
    manager, _, session = admitted_call({"type": "object"}, types.CallToolResult(content=[]))
    owner = manager.get_owner("security-fixture")
    assert owner is not None
    owner.definition = owner.definition.model_copy(update={
        "display_name": canary, "provider": canary,
        "environment": ExtensionEnvironment.model_validate_json(json.dumps({
            "references": {"TOKEN": {"kind": "env", "name": ref}},
        })),
    })
    owner.state_machine.last_error = canary
    snapshot = manager.health_snapshot()
    encoded = json.dumps(snapshot)
    assert canary not in encoded and ref not in encoded
    assert snapshot["items"][0]["last_error_code"] == "external_failure"
    assert not {"arguments", "command", "working_directory", "environment", "schema"} & snapshot["items"][0].keys()
    session.send_request.assert_not_called()


def test_maximum_catalog_health_is_finite_and_contains_counts_instead_of_schemas() -> None:
    template, _, _ = admitted_call({"type": "object"}, types.CallToolResult(content=[]))
    sample = template.get_owner("security-fixture")
    assert sample is not None
    manager = ExtensionLifecycleManager()
    sessions: list[Any] = []
    for index in range(4):
        identity = f"fixture-{index}"
        owner = manager.register_extension(sample.definition.model_copy(update={"extension_id": identity}))
        owner.state_machine.transition(ExtensionState.STARTING)
        tools = tuple(ExtensionToolDescriptor.create(
            extension_id=identity, provider="fixture", remote_name=f"read_{tool_index}",
            description="Fixture read",
            input_schema={"type": "object"}, catalog_generation=owner.generation,
            policy=ToolPolicy(remote_name=f"read_{tool_index}", domain="system", allow_call=True,
                              mutation_class=MutationClass.READ_ONLY, profiles=("full",)),
        ) for tool_index in range(128))
        manager.registry.publish_extension_catalog(identity, owner.generation, tools)
        owner.state_machine.transition(ExtensionState.HEALTHY)
        session = MagicMock()
        sessions.append(session)
        owner.register_session(session, "session")
    snapshot = manager.health_snapshot()
    assert snapshot["configured_count"] == snapshot["live_count"] == 4
    assert snapshot["discovered_tool_count"] == snapshot["eligible_tool_count"] == LIMITS.total_tools
    assert len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) <= LIMITS.health_bytes
    assert len(snapshot["items"]) == 4
    for session in sessions:
        session.send_request.assert_not_called()


def test_shared_collector_uses_canonical_extension_counts_without_rpc() -> None:
    from core.health_snapshot import collect_server_health

    manager, _, session = admitted_call({"type": "object"}, types.CallToolResult(content=[]))

    class Server:
        extension_manager = manager

        async def list_tools(self) -> list[Any]:
            return []

    class Browser:
        async def stats(self) -> dict[str, int]:
            return {}

    class Workflows:
        def health_summary(self) -> dict[str, int]:
            return dict.fromkeys(("unresolved_operation_count", "workflow_db_bytes", "workflow_total", "operation_total",
                                  "workflow_operation_total", "workflow_event_total", "active_workflow_leases", "queued_workflows",
                                  "running_workflows", "uncertain_workflows", "unresolved_workflow_operations"), 0)

        def list(self, **kwargs: Any) -> dict[str, Any]:
            return {"items": [], "total_count": 0}

    class Recovery:
        def summary(self, **kwargs: Any) -> dict[str, int]:
            return {"uncertain_count": 0, "pending_count": 0}

    async def scenario() -> None:
        result = await collect_server_health(Server(), browser_manager=Browser(), recovery=Recovery(),
                                             resource_collector=lambda _: {"budgets": {}}, workflow_store_factory=lambda _: Workflows())
        direct = manager.health_snapshot(unresolved_external_count=0)
        for key in ("configured_count", "live_count", "healthy_count", "discovered_tool_count", "eligible_tool_count",
                    "catalog_generation"):
            assert result["extensions"][key] == direct[key]
        assert result["health_status"] == "healthy"
        session.send_request.assert_not_called()

    anyio.run(scenario)


def test_catalog_retirement_during_native_collection_keeps_total_and_generation_coherent() -> None:
    from test_extension_bridge import bridge

    from core.health_snapshot import collect_server_health

    server, manager, session = bridge()
    store = MagicMock()
    store.health_summary.return_value = dict.fromkeys((
        "unresolved_operation_count", "workflow_db_bytes", "workflow_total", "workflow_operation_total", "workflow_event_total",
        "active_workflow_leases", "queued_workflows", "running_workflows", "uncertain_workflows", "unresolved_workflow_operations",
    ), 0)
    store.list.return_value = {"items": [], "total_count": 0}
    recovery = MagicMock()
    recovery.summary.return_value = {"uncertain_count": 0, "pending_count": 0}

    class Browser:
        async def stats(self) -> dict[str, int]:
            manager.registry.retire_extension_catalog("security-fixture")
            return {}

    async def scenario() -> None:
        assert len(await server.list_tools()) == 2
        result = await collect_server_health(server, browser_manager=Browser(), recovery=recovery,
                                             resource_collector=lambda _: {"budgets": {}}, workflow_store_factory=lambda _: store)
        assert result["tool_count"] == len(await server.list_tools()) == 1
        assert result["extensions"]["eligible_tool_count"] == 0
        assert result["extensions"]["catalog_generation"] == manager.registry.manager_generation
        session.send_request.assert_not_called()

    anyio.run(scenario)


def test_complete_native_and_extension_payload_budget_and_stale_generation() -> None:
    import time

    from core.health_snapshot import MAX_RUNTIME_HEALTH_BYTES, bound_runtime_health, validate_runtime_health

    manager, _, session = admitted_call({"type": "object"}, types.CallToolResult(content=[]))
    now = time.monotonic()
    payload = {
        "ok": True, "health_status": "degraded", "tool_count": 150, "snapshot_schema_version": 1,
        "runtime_generation": "a" * 32, "collected_monotonic": now, "collected_at": "2026-10-13T00:00:00+00:00",
        "extensions": manager.health_snapshot(), "native_noise": "x" * (2 * MAX_RUNTIME_HEALTH_BYTES),
        "operation_recovery": {"uncertain_count": 5, "pending_count": 3, "uncertain": [{"note": "y" * 20_000}] * 20},
    }
    bounded = bound_runtime_health(payload)
    assert len(json.dumps(bounded, ensure_ascii=False).encode("utf-8")) <= MAX_RUNTIME_HEALTH_BYTES
    assert bounded["snapshot_truncated"]
    assert bound_runtime_health(bounded)["snapshot_truncated"]
    assert bounded["tool_count"] == 150
    assert bounded["operation_recovery"]["uncertain_count"] == 5
    assert bounded["extensions"]["eligible_tool_count"] == 1
    fresh = validate_runtime_health(bounded, "a" * 32, now=now + 1)
    assert fresh["snapshot_status"] == "fresh" and fresh["health_status"] == "degraded"
    stale = validate_runtime_health(bounded, "a" * 32, now=now + 31)
    assert stale["snapshot_status"] == stale["health_status"] == "stale"
    assert stale["extensions"]["eligible_tool_count"] == stale["extensions"]["healthy_count"] == 0
    assert not stale["extensions"]["items"][0]["routable"]
    mismatch = validate_runtime_health(bounded, "b" * 32, now=now + 1)
    assert mismatch["snapshot_status"] == "stale"
    assert mismatch["snapshot_reason"] == "generation_mismatch"
    assert validate_runtime_health(bounded, "a" * 32, now=now - 1)["snapshot_status"] == "stale"
    assert validate_runtime_health({**bounded, "collected_monotonic": 10**400}, "a" * 32)["snapshot_status"] == "stale"
    assert validate_runtime_health({**bounded, "snapshot_schema_version": True}, "a" * 32)["snapshot_status"] == "stale"
    session.send_request.assert_not_called()


def test_supervisor_snapshot_rejects_corrupt_missing_oversize_and_prior_generation(tmp_path: Any) -> None:
    import time

    from core.health_snapshot import MAX_RUNTIME_HEALTH_BYTES
    from scripts.supervisor import Supervisor

    supervisor = Supervisor([], readiness_url=None, state_dir=tmp_path)
    path = tmp_path / f"health-{'a' * 32}.json"
    supervisor.current_runtime_health_path = path
    payload = {"snapshot_schema_version": 1, "runtime_generation": "a" * 32, "collected_monotonic": time.monotonic(),
               "health_status": "healthy", "extensions": {"items": [], "eligible_tool_count": 1, "healthy_count": 1}}
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert supervisor.runtime_health_snapshot()["snapshot_status"] == "fresh"
    payload["runtime_generation"] = "b" * 32
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert supervisor.runtime_health_snapshot()["snapshot_status"] == "stale"
    path.write_text("{broken", encoding="utf-8")
    assert supervisor.runtime_health_snapshot()["snapshot_status"] == "unavailable"
    path.write_bytes(b"x" * (MAX_RUNTIME_HEALTH_BYTES + 1))
    assert supervisor.runtime_health_snapshot()["snapshot_status"] == "unavailable"
    supervisor.current_runtime_health_path = None
    assert supervisor.runtime_health_snapshot()["snapshot_status"] == "unavailable"


def test_heartbeat_publishes_one_complete_bounded_snapshot(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import time
    from dataclasses import replace

    from core import heartbeat
    from core.health_snapshot import MAX_RUNTIME_HEALTH_BYTES, validate_runtime_health

    path = tmp_path / f"health-{'a' * 32}.json"
    monkeypatch.setenv("MCP_RUNTIME_HEALTH_FILE", str(path))
    monkeypatch.setenv("MCP_RUNTIME_GENERATION_ID", "a" * 32)
    for name in ("MCP_LIFECYCLE_CONTROL_FILE", "MCP_LIFECYCLE_STATUS_FILE", "MCP_HEARTBEAT_FILE", "MCP_TOOL_ACTIVITY_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(heartbeat, "SETTINGS", replace(heartbeat.SETTINGS, state_dir=tmp_path / "state", extensions_enabled=False))
    for name in ("schedule_artifact_retention", "schedule_job_history_retention", "schedule_workflow_history_retention"):
        monkeypatch.setattr(heartbeat, name, lambda **kwargs: None)
    monkeypatch.setattr(heartbeat.BACKUP_RETENTION, "schedule", lambda: None)
    calls = 0

    async def collect(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"ok": True, "health_status": "healthy", "tool_count": 150, "snapshot_schema_version": 1,
                "runtime_generation": "a" * 32, "collected_monotonic": time.monotonic(), "extensions": {"items": []},
                "large_native_history": "x" * (2 * MAX_RUNTIME_HEALTH_BYTES)}

    monkeypatch.setattr(heartbeat, "collect_server_health", collect)

    class Server:
        extension_manager = None

    async def scenario() -> None:
        async with heartbeat.lifespan(Server()):
            with anyio.fail_after(3):
                while not path.exists():
                    await asyncio.sleep(0.01)
            assert path.stat().st_size <= MAX_RUNTIME_HEALTH_BYTES
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            assert snapshot["snapshot_truncated"]
            assert snapshot["tool_count"] == 150
            assert validate_runtime_health(snapshot, "a" * 32)["snapshot_status"] == "fresh"
    anyio.run(scenario)
    assert calls == 1
