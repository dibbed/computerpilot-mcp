"""Workflow, step, and operation lifecycle states and transition guards."""

from __future__ import annotations

import heapq
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, TypeVar

from core.errors import ToolError


class WorkflowState(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
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
    execution_intent: dict[str, Any] | None = None
    step_id: str | None = None
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "depends_on", tuple(self.depends_on))


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    name: str
    steps: tuple[StepDefinition, ...]
    description: str = ""
    execution_mode: Literal["sequential", "dag"] = "sequential"


_STEP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def workflow_topological_order(definition: WorkflowDefinition) -> tuple[int, ...]:
    """Validate dependency metadata and return a stable topological step order."""

    steps = definition.steps
    if not steps:
        raise ToolError("empty_workflow", "A workflow must contain at least one step.")
    if len(steps) > 100:
        raise ToolError("workflow_too_large", "A workflow may contain at most 100 steps.")
    if definition.execution_mode == "sequential":
        if any(step.step_id is not None or step.depends_on for step in steps):
            raise ToolError(
                "workflow_dag_metadata_requires_dag_mode",
                "step_id and depends_on require execution_mode='dag'.",
            )
        return tuple(range(len(steps)))
    if definition.execution_mode != "dag":
        raise ToolError("invalid_workflow_execution_mode", "execution_mode must be 'sequential' or 'dag'.")

    by_id: dict[str, int] = {}
    for index, step in enumerate(steps):
        step_id = step.step_id
        if step_id is None or _STEP_ID_RE.fullmatch(step_id) is None:
            raise ToolError(
                "invalid_workflow_step_id",
                "DAG step_id must be 1..100 ASCII letters, digits, '_' or '-'.",
            )
        if step_id in by_id:
            raise ToolError("duplicate_workflow_step_id", f"Duplicate workflow step_id {step_id!r}.")
        by_id[step_id] = index

    indegree = [0] * len(steps)
    children: list[list[int]] = [[] for _ in steps]
    for index, step in enumerate(steps):
        if len(step.depends_on) > 99:
            raise ToolError("too_many_workflow_dependencies", "A workflow step may have at most 99 dependencies.")
        if len(set(step.depends_on)) != len(step.depends_on):
            raise ToolError("duplicate_workflow_dependency", f"Step {step.step_id!r} contains a duplicate dependency.")
        for dependency in step.depends_on:
            if dependency == step.step_id:
                raise ToolError("self_workflow_dependency", f"Step {step.step_id!r} cannot depend on itself.")
            parent = by_id.get(dependency)
            if parent is None:
                raise ToolError(
                    "unknown_workflow_dependency",
                    f"Step {step.step_id!r} depends on unknown step_id {dependency!r}.",
                )
            indegree[index] += 1
            children[parent].append(index)

    ready = [index for index, degree in enumerate(indegree) if degree == 0]
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        index = heapq.heappop(ready)
        order.append(index)
        for child in children[index]:
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(ready, child)
    if len(order) != len(steps):
        cyclic_ids = [str(steps[index].step_id) for index, degree in enumerate(indegree) if degree > 0][:10]
        raise ToolError(
            "cyclic_workflow_dependencies",
            f"Workflow dependency graph contains a cycle involving: {', '.join(cyclic_ids)}.",
        )
    return tuple(order)


@dataclass(frozen=True, slots=True)
class WorkflowLease:
    workflow_id: str
    owner_id: str
    lease_token: str
    acquired_at: str
    heartbeat_at: str
    expires_at: str
    version: int


WORKFLOW_TRANSITIONS: Mapping[WorkflowState, frozenset[WorkflowState]] = {
    WorkflowState.CREATED: frozenset({WorkflowState.QUEUED, WorkflowState.CANCELLED}),
    WorkflowState.QUEUED: frozenset({WorkflowState.RUNNING, WorkflowState.CANCELLED}),
    WorkflowState.RUNNING: frozenset(
        {
            WorkflowState.WAITING,
            WorkflowState.CANCELLING,
            WorkflowState.PAUSED,
            WorkflowState.FAILED,
            WorkflowState.UNCERTAIN,
            WorkflowState.COMPLETED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.CANCELLING: frozenset(
        {WorkflowState.CANCELLED, WorkflowState.FAILED, WorkflowState.UNCERTAIN}
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
        {
            OperationState.SUCCEEDED,
            OperationState.FAILED,
            OperationState.UNCERTAIN,
            OperationState.ACKNOWLEDGED,
            OperationState.UNRESOLVABLE,
        }
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
