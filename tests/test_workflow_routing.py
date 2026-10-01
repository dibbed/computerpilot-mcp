from __future__ import annotations

from pathlib import Path

import pytest
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
