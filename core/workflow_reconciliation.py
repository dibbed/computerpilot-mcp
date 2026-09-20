"""Side-effect-free reconciliation for uncertain workflow operations."""

from __future__ import annotations

from typing import Literal

from core.errors import ToolError
from core.reconcilers import evaluate_postcondition
from core.recovery_models import Postcondition
from core.workflow_models import OperationState
from core.workflow_store import WorkflowStore, redact_inputs


def reconcile_operation(store: WorkflowStore, operation_id: str, *, expected_version: int) -> dict[str, object]:
    operation = store.get_operation(operation_id)
    if operation["version"] != expected_version:
        raise ToolError("workflow_operation_version_conflict", "Operation changed; reload it before retrying.")
    postcondition = operation.get("postcondition")
    if not isinstance(postcondition, dict):
        evidence = {"source": "postcondition", "conclusive": False, "satisfied": False,
                    "reason": "postcondition_missing"}
    else:
        result = evaluate_postcondition(Postcondition(str(postcondition.get("kind", "")), dict(postcondition.get("expected", {}))))
        evidence = {"source": result.source, **result.data}
    if evidence.get("conclusive") is not True:
        target = OperationState.UNCERTAIN
    elif evidence.get("satisfied") is True:
        target = OperationState.SUCCEEDED
    else:
        target = OperationState.FAILED
    return store.resolve_uncertain_operation(
        operation_id, expected_version, target, evidence=evidence,
        event_type="operation_reconciled", metadata={"automated": True},
    )


def acknowledge_operation(
    store: WorkflowStore,
    operation_id: str,
    *,
    expected_version: int,
    resolution: Literal["resolved_completed", "resolved_failed", "remain_uncertain"],
    reason: str,
    actor: str,
) -> dict[str, object]:
    bounded_reason, bounded_actor = reason.strip()[:2_000], actor.strip()[:200]
    if not bounded_reason or not bounded_actor:
        raise ToolError("workflow_acknowledgement_required", "Acknowledgement requires non-empty reason and actor.")
    states = {
        "resolved_completed": OperationState.ACKNOWLEDGED,
        "resolved_failed": OperationState.UNRESOLVABLE,
        "remain_uncertain": OperationState.UNCERTAIN,
    }
    evidence = redact_inputs({
        "source": "operator_assertion", "conclusive": resolution != "remain_uncertain",
        "satisfied": resolution == "resolved_completed", "resolution": resolution,
        "reason": bounded_reason, "actor": bounded_actor,
    })
    return store.resolve_uncertain_operation(
        operation_id, expected_version, states[resolution], evidence=evidence,
        event_type="operation_acknowledged", metadata={"resolution": resolution, "reason": bounded_reason, "actor": bounded_actor},
    )
