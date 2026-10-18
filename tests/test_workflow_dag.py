from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.errors import ToolError
from core.workflow_actions import validate_workflow_definition
from core.workflow_executor import NATIVE_ONLY_ROUTING, WorkflowExecutor
from core.workflow_models import StepDefinition, WorkflowDefinition, WorkflowState, workflow_topological_order
from core.workflow_store import WorkflowStore


def _step(step_id: str, *depends_on: str) -> StepDefinition:
    return StepDefinition(
        name=step_id,
        action="check_file",
        arguments={"path": f"{step_id}.txt", "exists": True},
        step_id=step_id,
        depends_on=depends_on,
    )


def test_dag_validation_accepts_diamond_and_topological_order_is_stable() -> None:
    definition = WorkflowDefinition(
        "diamond",
        (
            _step("root"),
            _step("left", "root"),
            _step("right", "root"),
            _step("join", "left", "right"),
        ),
        execution_mode="dag",
    )
    validate_workflow_definition(definition)
    assert workflow_topological_order(definition) == (0, 1, 2, 3)


@pytest.mark.parametrize(
    ("steps", "code"),
    [
        ((_step("a"), _step("a")), "duplicate_workflow_step_id"),
        ((_step("a", "missing"),), "unknown_workflow_dependency"),
        ((_step("a", "a"),), "self_workflow_dependency"),
        ((_step("a", "b"), _step("b", "a")), "cyclic_workflow_dependencies"),
    ],
)
def test_invalid_dag_graphs_fail_before_persistence(steps: tuple[StepDefinition, ...], code: str) -> None:
    definition = WorkflowDefinition("invalid", steps, execution_mode="dag")
    with pytest.raises(ToolError) as raised:
        validate_workflow_definition(definition)
    assert raised.value.code == code


def test_dag_rejects_verify_changes_with_ruff_fix_before_persistence(tmp_path: Path) -> None:
    definition = WorkflowDefinition(
        "unsafe-fix",
        (
            StepDefinition(
                name="verify",
                action="verify_changes",
                arguments={"cwd": ".", "ruff_fix": True},
                step_id="verify",
            ),
        ),
        execution_mode="dag",
    )
    with pytest.raises(ToolError) as raised:
        validate_workflow_definition(definition)
    assert raised.value.code == "workflow_parallel_fix_unavailable"
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    with pytest.raises(ToolError) as raised:
        store.create(definition)
    assert raised.value.code == "workflow_parallel_fix_unavailable"
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflows").fetchone()[0] == 0


def test_sequential_verification_preserves_ruff_fix() -> None:
    validate_workflow_definition(WorkflowDefinition("legacy-fix", (
        StepDefinition(name="verify", action="verify_changes", arguments={"cwd": ".", "ruff_fix": True}),
    )))


def test_sequential_mode_rejects_explicit_dag_metadata() -> None:
    definition = WorkflowDefinition("invalid", (_step("a"),))
    with pytest.raises(ToolError) as raised:
        validate_workflow_definition(definition)
    assert raised.value.code == "workflow_dag_metadata_requires_dag_mode"


def test_store_persists_and_projects_dag_dependencies(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    definition = WorkflowDefinition(
        "diamond",
        (
            _step("root"),
            _step("left", "root"),
            _step("right", "root"),
            _step("join", "left", "right"),
        ),
        execution_mode="dag",
    )
    created = store.create(definition)
    reloaded = store.execution_definition(created["workflow_id"])
    assert reloaded == definition
    status = store.get(created["workflow_id"])
    assert status["execution_mode"] == "dag"
    assert status["topological_order"] == [0, 1, 2, 3]
    assert status["steps"][0]["readiness"] == "ready"
    assert status["steps"][3]["depends_on"] == ["left", "right"]


def test_dag_execute_is_gated_before_state_transition_or_dispatch(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    definition = WorkflowDefinition("dag", (_step("root"),), execution_mode="dag")
    created = store.create(definition, initial_state=WorkflowState.QUEUED)
    executor = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING)
    with pytest.raises(ToolError) as raised:
        executor.execute(created["workflow_id"], owner_id="owner")
    assert raised.value.code == "workflow_dag_execution_unavailable"
    assert store.get(created["workflow_id"])["state"] == "queued"
