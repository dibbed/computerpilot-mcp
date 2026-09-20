"""Workflow, step, and operation lifecycle states and transition guards."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

from core.errors import ToolError


class WorkflowState(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    PAUSED = "paused"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    RECONCILING = "reconciling"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class StepState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING = "waiting"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    RECONCILING = "reconciling"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class OperationState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    RECONCILING = "reconciling"
    ACKNOWLEDGED = "acknowledged"
    UNRESOLVABLE = "unresolvable"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class StepDefinition:
    name: str
    action: str
    arguments: dict[str, Any] = field(default_factory=dict)
    timeout_sec: float = 300
    max_retries: int = 0
    postcondition: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    name: str
    steps: tuple[StepDefinition, ...]
    description: str = ""


WORKFLOW_TRANSITIONS: Mapping[WorkflowState, frozenset[WorkflowState]] = {
    WorkflowState.CREATED: frozenset({WorkflowState.QUEUED, WorkflowState.CANCELLED}),
    WorkflowState.QUEUED: frozenset({WorkflowState.RUNNING, WorkflowState.CANCELLED}),
    WorkflowState.RUNNING: frozenset(
        {
            WorkflowState.WAITING,
            WorkflowState.PAUSED,
            WorkflowState.FAILED,
            WorkflowState.UNCERTAIN,
            WorkflowState.COMPLETED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.WAITING: frozenset(
        {
            WorkflowState.RUNNING,
            WorkflowState.PAUSED,
            WorkflowState.FAILED,
            WorkflowState.UNCERTAIN,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.PAUSED: frozenset({WorkflowState.QUEUED, WorkflowState.CANCELLED}),
    WorkflowState.FAILED: frozenset({WorkflowState.QUEUED, WorkflowState.CANCELLED}),
    WorkflowState.UNCERTAIN: frozenset({WorkflowState.RECONCILING, WorkflowState.CANCELLED}),
    WorkflowState.RECONCILING: frozenset(
        {
            WorkflowState.PAUSED,
            WorkflowState.FAILED,
            WorkflowState.UNCERTAIN,
            WorkflowState.COMPLETED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.COMPLETED: frozenset(),
    WorkflowState.CANCELLED: frozenset(),
}

STEP_TRANSITIONS: Mapping[StepState, frozenset[StepState]] = {
    StepState.CREATED: frozenset({StepState.RUNNING, StepState.CANCELLED}),
    StepState.RUNNING: frozenset(
        {StepState.WAITING, StepState.FAILED, StepState.UNCERTAIN, StepState.COMPLETED, StepState.CANCELLED}
    ),
    StepState.WAITING: frozenset({StepState.RUNNING, StepState.FAILED, StepState.UNCERTAIN, StepState.CANCELLED}),
    StepState.UNCERTAIN: frozenset({StepState.RECONCILING, StepState.CANCELLED}),
    StepState.RECONCILING: frozenset({StepState.FAILED, StepState.UNCERTAIN, StepState.COMPLETED}),
    StepState.FAILED: frozenset({StepState.RUNNING, StepState.CANCELLED}),
    StepState.COMPLETED: frozenset(),
    StepState.CANCELLED: frozenset(),
}

OPERATION_TRANSITIONS: Mapping[OperationState, frozenset[OperationState]] = {
    OperationState.CREATED: frozenset({OperationState.RUNNING, OperationState.CANCELLED}),
    OperationState.RUNNING: frozenset(
        {
            OperationState.WAITING,
            OperationState.SUCCEEDED,
            OperationState.FAILED,
            OperationState.UNCERTAIN,
            OperationState.CANCELLED,
        }
    ),
    OperationState.WAITING: frozenset(
        {
            OperationState.RUNNING,
            OperationState.SUCCEEDED,
            OperationState.FAILED,
            OperationState.UNCERTAIN,
            OperationState.CANCELLED,
        }
    ),
    OperationState.UNCERTAIN: frozenset(
        {
            OperationState.RECONCILING,
            OperationState.ACKNOWLEDGED,
            OperationState.UNRESOLVABLE,
            OperationState.CANCELLED,
        }
    ),
    OperationState.RECONCILING: frozenset(
        {OperationState.SUCCEEDED, OperationState.FAILED, OperationState.UNCERTAIN, OperationState.UNRESOLVABLE}
    ),
    OperationState.FAILED: frozenset({OperationState.RUNNING, OperationState.CANCELLED}),
    OperationState.SUCCEEDED: frozenset(),
    OperationState.ACKNOWLEDGED: frozenset(),
    OperationState.UNRESOLVABLE: frozenset(),
    OperationState.CANCELLED: frozenset(),
}

StateT = TypeVar("StateT", bound=Enum)


def _require_transition(
    entity: str,
    current: StateT,
    target: StateT,
    allowed: Mapping[StateT, frozenset[StateT]],
) -> None:
    if target not in allowed[current]:
        raise ToolError(
            f"invalid_{entity}_transition",
            f"Cannot transition {entity} from {current.value!r} to {target.value!r}.",
        )


def validate_workflow_transition(current: WorkflowState, target: WorkflowState) -> None:
    _require_transition("workflow", current, target, WORKFLOW_TRANSITIONS)


def validate_step_transition(current: StepState, target: StepState) -> None:
    _require_transition("step", current, target, STEP_TRANSITIONS)


def validate_operation_transition(current: OperationState, target: OperationState) -> None:
    _require_transition("operation", current, target, OPERATION_TRANSITIONS)
