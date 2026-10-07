from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from mcp import Client
from pydantic import ValidationError

from core.workflow_models import StepDefinition, WorkflowDefinition, WorkflowState
from core.workflow_store import WorkflowStore
from tools.workflows.registry import StepInput


def _routed_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        "routed-status",
        (
            StepDefinition(
                "status",
                "git_status",
                {"repo": "."},
                execution_intent={"name": "git.read"},
            ),
        ),
    )


def _running_store(tmp_path: Path) -> tuple[WorkflowStore, dict[str, object], str]:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(_routed_definition(), initial_state=WorkflowState.QUEUED)
    lease = store.acquire_lease(str(workflow["workflow_id"]), "test-owner", 30)
    current = store.get(str(workflow["workflow_id"]))
    store.transition(
        str(workflow["workflow_id"]),
        int(current["version"]),
        WorkflowState.RUNNING,
        lease_token=lease.lease_token,
    )
    return store, workflow, lease.lease_token


def test_step_input_accepts_bounded_execution_intent_and_rejects_extra_fields() -> None:
    step = StepInput(
        name="status",
        action="git_status",
        arguments={"repo": "."},
        execution_intent={"name": "git.read", "preferred_tool": "git_status"},  # type: ignore[arg-type]
    )
    dumped = step.model_dump()
    assert dumped["execution_intent"] == {
        "name": "git.read",
        "destructive": False,
        "semantic_ambiguous": False,
        "semantic_stale": False,
        "requires_macro_preservation": False,
        "allow_raw_desktop": False,
        "preferred_tool": "git_status",
    }

    with pytest.raises(ValidationError):
        StepInput(
            name="status",
            action="git_status",
            arguments={"repo": "."},
            execution_intent={"name": "git.read", "secret": "must-not-persist"},  # type: ignore[arg-type]
        )


def test_materialized_operation_persists_execution_intent(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(_routed_definition(), initial_state=WorkflowState.QUEUED)
    operation = store.list_operations(str(workflow["workflow_id"]))["items"][0]

    assert operation["execution_intent"] == {"name": "git.read"}
    assert operation["selected_route"] is None
    assert operation["router_policy_version"] is None
    assert operation["route_decision"] is None
    assert operation["fallback_history"] == []


def test_route_decision_is_persisted_and_route_changes_append_bounded_history(tmp_path: Path) -> None:
    store, workflow, lease_token = _running_store(tmp_path)
    operation = store.list_operations(str(workflow["workflow_id"]))["items"][0]
    operation_id = str(operation["operation_id"])

    first = store.record_route_decision(
        operation_id,
        lease_token=lease_token,
        selected_route="native.git",
        router_policy_version="deterministic-v1",
        decision={
            "selected_route": "native.git",
            "representative_tool": "git_status",
            "fallback_level": 0,
        },
    )
    assert first["selected_route"] == "native.git"
    assert first["router_policy_version"] == "deterministic-v1"
    assert first["route_decision"]["representative_tool"] == "git_status"
    assert first["fallback_history"] == []

    second = store.record_route_decision(
        operation_id,
        lease_token=lease_token,
        selected_route="native.process",
        router_policy_version="deterministic-v1",
        decision={
            "selected_route": "native.process",
            "representative_tool": "run_process",
            "fallback_level": 1,
        },
    )
    assert second["selected_route"] == "native.process"
    assert second["fallback_history"][-1]["selected_route"] == "native.git"
    assert second["fallback_history"][-1]["router_policy_version"] == "deterministic-v1"

    for index in range(30):
        route = "native.git" if index % 2 == 0 else "native.process"
        store.record_route_decision(
            operation_id,
            lease_token=lease_token,
            selected_route=route,
            router_policy_version="deterministic-v1",
            decision={"selected_route": route, "fallback_level": index % 2},
        )
    final = store.get_operation(operation_id)
    assert len(final["fallback_history"]) <= 20
    assert all("selected_route" in item for item in final["fallback_history"])


class _Planner:
    def __init__(self, decisions: list[dict[str, object]]) -> None:
        self.decisions = decisions
        self.calls = 0

    def __call__(self, intent: object) -> dict[str, object]:
        del intent
        index = min(self.calls, len(self.decisions) - 1)
        self.calls += 1
        return dict(self.decisions[index])


def _decision(route: str, tool: str, fallback_level: int = 0) -> dict[str, object]:
    return {
        "ok": True,
        "intent": "git.read",
        "selected_route": route,
        "representative_tool": tool,
        "candidate_count": 1,
        "selection_reason": "test decision",
        "fallback_level": fallback_level,
        "capability_checks": [],
        "risk_class": "low",
        "router_policy_version": "deterministic-v1",
    }


def test_executor_persists_route_before_action_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.workflow_actions import ACTION_HANDLERS, ActionContext
    from core.workflows import WorkflowExecutor

    store = WorkflowStore(tmp_path / "workflows.db")
    created = store.create(_routed_definition(), initial_state=WorkflowState.QUEUED)
    planner = _Planner([_decision("native.git", "git_status")])
    observed: list[dict[str, object]] = []

    def handler(arguments: dict[str, object], timeout: float, context: ActionContext) -> dict[str, object]:
        del arguments, timeout
        operation = store.get_operation(context.operation_id)
        observed.append(operation)
        return {"repo": str(tmp_path), "stdout": "", "exit_code": 0, "dirty": False}

    monkeypatch.setitem(ACTION_HANDLERS, "git_status", handler)
    result = WorkflowExecutor(store, route_planner=planner).execute(
        str(created["workflow_id"]),
        owner_id="route-test",
    )

    assert result["state"] == "completed"
    assert planner.calls == 1
    assert observed
    assert observed[0]["selected_route"] == "native.git"
    assert observed[0]["router_policy_version"] == "deterministic-v1"


def test_route_mismatch_fails_before_action_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.workflow_actions import ACTION_HANDLERS
    from core.workflows import WorkflowExecutor

    store = WorkflowStore(tmp_path / "workflows.db")
    created = store.create(_routed_definition(), initial_state=WorkflowState.QUEUED)
    planner = _Planner([_decision("native.process", "run_process", 1)])
    calls = 0

    def handler(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        nonlocal calls
        del arguments, timeout
        calls += 1
        return {"repo": str(tmp_path), "stdout": "", "exit_code": 0, "dirty": False}

    monkeypatch.setitem(ACTION_HANDLERS, "git_status", handler)
    result = WorkflowExecutor(store, route_planner=planner).execute(
        str(created["workflow_id"]),
        owner_id="route-mismatch-test",
    )
    operation = store.list_operations(str(created["workflow_id"]))["items"][0]

    assert result["state"] == "failed"
    assert calls == 0
    assert operation["selected_route"] == "native.process"
    assert "does not match fixed workflow action route" in str(result["last_error"])


def test_safe_retry_records_route_change_but_mismatch_blocks_second_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.workflow_actions import ACTION_HANDLERS, TransientActionError
    from core.workflows import WorkflowExecutor

    definition = WorkflowDefinition(
        "routed-retry",
        (
            StepDefinition(
                "status",
                "git_status",
                {"repo": "."},
                max_retries=1,
                execution_intent={"name": "git.read"},
            ),
        ),
    )
    store = WorkflowStore(tmp_path / "workflows.db")
    created = store.create(definition, initial_state=WorkflowState.QUEUED)
    planner = _Planner([
        _decision("native.git", "git_status"),
        _decision("native.process", "run_process", 1),
    ])
    calls = 0

    def transient(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        nonlocal calls
        del arguments, timeout
        calls += 1
        raise TransientActionError("capability changed")

    monkeypatch.setitem(ACTION_HANDLERS, "git_status", transient)
    monkeypatch.setattr("core.workflow_executor._retry_backoff_seconds", lambda attempt: 0.0)
    result = WorkflowExecutor(store, route_planner=planner).execute(
        str(created["workflow_id"]),
        owner_id="route-retry-test",
    )
    operation = store.list_operations(str(created["workflow_id"]))["items"][0]

    assert result["state"] == "failed"
    assert planner.calls == 2
    assert calls == 1
    assert operation["selected_route"] == "native.process"
    assert operation["fallback_history"][-1]["selected_route"] == "native.git"


def test_uncertain_operation_is_not_rerouted_during_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.workflow_actions import ACTION_HANDLERS, SideEffectUncertain
    from core.workflow_reconciliation import reconcile_operation
    from core.workflows import WorkflowExecutor

    target = tmp_path / "done.txt"
    target.write_text("done", encoding="utf-8")
    definition = WorkflowDefinition(
        "routed-uncertain",
        (
            StepDefinition(
                "check",
                "check_file",
                {"path": str(target)},
                postcondition={"kind": "file_exists", "expected": {"path": str(target)}},
                execution_intent={"name": "filesystem.read"},
            ),
        ),
    )
    store = WorkflowStore(tmp_path / "workflows.db")
    created = store.create(definition, initial_state=WorkflowState.QUEUED)
    planner = _Planner([
        {
            **_decision("native.filesystem", "read_file"),
            "intent": "filesystem.read",
        }
    ])

    def uncertain(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        del arguments, timeout
        raise SideEffectUncertain("lost result")

    monkeypatch.setitem(ACTION_HANDLERS, "check_file", uncertain)
    result = WorkflowExecutor(store, route_planner=planner).execute(
        str(created["workflow_id"]),
        owner_id="route-uncertain-test",
    )
    operation = store.list_operations(str(created["workflow_id"]))["items"][0]

    assert result["state"] == "uncertain"
    assert planner.calls == 1
    reconciled = reconcile_operation(
        store,
        str(operation["operation_id"]),
        expected_version=int(operation["version"]),
    )
    assert reconciled["operation"]["state"] == "succeeded"
    assert planner.calls == 1


def test_mcp_workflow_execute_uses_registered_server_catalog_for_routing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import registry
    from core.workflow_actions import ACTION_HANDLERS
    from tools.workflows import registry as workflow_registry

    original = registry.SETTINGS
    original_workflow_settings = workflow_registry.SETTINGS
    test_settings = replace(
        original,
        tool_profile="git",
        state_dir=tmp_path / "state",
        execution_router_enabled=True,
        execution_router_policy="deterministic-v1",
    )
    registry.SETTINGS = test_settings
    workflow_registry.SETTINGS = test_settings

    def handler(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        del arguments, timeout
        return {"repo": str(tmp_path), "stdout": "", "exit_code": 0, "dirty": False}

    monkeypatch.setitem(ACTION_HANDLERS, "git_status", handler)

    async def scenario() -> None:
        async with Client(registry.create_server()) as client:
            started = await client.call_tool(
                "workflow_start",
                {
                    "definition": {
                        "name": "routed-mcp",
                        "steps": [
                            {
                                "name": "status",
                                "action": "git_status",
                                "arguments": {"repo": "."},
                                "execution_intent": {"name": "git.read"},
                            }
                        ],
                    }
                },
            )
            assert isinstance(started.structured_content, dict)
            workflow_id = str(started.structured_content["workflow_id"])
            version = int(started.structured_content["version"])
            executed = await client.call_tool(
                "workflow_execute",
                {"workflow_id": workflow_id, "expected_version": version},
            )
            assert isinstance(executed.structured_content, dict)
            assert executed.structured_content["state"] == "completed"
            operations = await client.call_tool(
                "workflow_operations",
                {"workflow_id": workflow_id},
            )
            assert isinstance(operations.structured_content, dict)
            operation = operations.structured_content["items"][0]
            assert operation["selected_route"] == "native.git"
            assert operation["router_policy_version"] == "deterministic-v1"

    try:
        asyncio.run(scenario())
    finally:
        registry.SETTINGS = original
        workflow_registry.SETTINGS = original_workflow_settings
