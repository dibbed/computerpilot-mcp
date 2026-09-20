from __future__ import annotations

from pathlib import Path

import pytest

from core.errors import ToolError
from core.workflow_models import (
    OperationState,
    StepState,
    WorkflowState,
    validate_operation_transition,
    validate_step_transition,
    validate_workflow_transition,
)
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowStore


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (WorkflowState.CREATED, WorkflowState.QUEUED),
        (WorkflowState.QUEUED, WorkflowState.RUNNING),
        (WorkflowState.RUNNING, WorkflowState.UNCERTAIN),
        (WorkflowState.UNCERTAIN, WorkflowState.RECONCILING),
        (WorkflowState.RECONCILING, WorkflowState.COMPLETED),
        (WorkflowState.FAILED, WorkflowState.QUEUED),
    ],
)
def test_allowed_workflow_transitions(source: WorkflowState, target: WorkflowState) -> None:
    validate_workflow_transition(source, target)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (WorkflowState.CREATED, WorkflowState.RUNNING),
        (WorkflowState.COMPLETED, WorkflowState.RUNNING),
        (WorkflowState.CANCELLED, WorkflowState.QUEUED),
        (WorkflowState.FAILED, WorkflowState.COMPLETED),
        (WorkflowState.UNCERTAIN, WorkflowState.RUNNING),
        (WorkflowState.RUNNING, WorkflowState.RUNNING),
    ],
)
def test_illegal_workflow_transitions_are_rejected(source: WorkflowState, target: WorkflowState) -> None:
    with pytest.raises(ToolError, match="Cannot transition workflow"):
        validate_workflow_transition(source, target)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (StepState.CREATED, StepState.RUNNING),
        (StepState.RUNNING, StepState.WAITING),
        (StepState.UNCERTAIN, StepState.RECONCILING),
        (StepState.RECONCILING, StepState.COMPLETED),
        (StepState.FAILED, StepState.RUNNING),
    ],
)
def test_allowed_step_transitions(source: StepState, target: StepState) -> None:
    validate_step_transition(source, target)


@pytest.mark.parametrize("state", [StepState.COMPLETED, StepState.CANCELLED])
def test_terminal_step_states_have_no_outgoing_transitions(state: StepState) -> None:
    with pytest.raises(ToolError, match="Cannot transition step"):
        validate_step_transition(state, StepState.RUNNING)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (OperationState.CREATED, OperationState.RUNNING),
        (OperationState.RUNNING, OperationState.SUCCEEDED),
        (OperationState.WAITING, OperationState.RUNNING),
        (OperationState.UNCERTAIN, OperationState.RECONCILING),
        (OperationState.UNCERTAIN, OperationState.ACKNOWLEDGED),
        (OperationState.RECONCILING, OperationState.UNRESOLVABLE),
        (OperationState.FAILED, OperationState.RUNNING),
    ],
)
def test_allowed_operation_transitions(source: OperationState, target: OperationState) -> None:
    validate_operation_transition(source, target)


@pytest.mark.parametrize(
    "state",
    [
        OperationState.SUCCEEDED,
        OperationState.ACKNOWLEDGED,
        OperationState.UNRESOLVABLE,
        OperationState.CANCELLED,
    ],
)
def test_terminal_operation_states_have_no_outgoing_transitions(state: OperationState) -> None:
    with pytest.raises(ToolError, match="Cannot transition operation"):
        validate_operation_transition(state, OperationState.RUNNING)


def test_store_validates_transition_against_persisted_state(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(WorkflowDefinition("check", (StepDefinition("file", "check_file"),)))

    with pytest.raises(ToolError, match="Cannot transition workflow"):
        store.transition(workflow["workflow_id"], workflow["version"], WorkflowState.RUNNING)

    unchanged = store.get(workflow["workflow_id"])
    assert unchanged["state"] == WorkflowState.CREATED.value
    assert unchanged["version"] == workflow["version"]


def test_running_workflow_advances_without_same_state_transition(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition(
            "checks",
            (
                StepDefinition("first", "check_file"),
                StepDefinition("second", "check_file"),
            ),
        ),
        initial_state=WorkflowState.QUEUED,
    )
    running = store.transition(workflow["workflow_id"], workflow["version"], WorkflowState.RUNNING)

    advanced = store.advance_running(workflow["workflow_id"], running["version"], current_step=1)

    assert advanced["state"] == WorkflowState.RUNNING.value
    assert advanced["current_step"] == 1
    assert advanced["version"] == running["version"] + 1
