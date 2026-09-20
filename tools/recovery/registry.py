"""MCP registration for evidence-based operation recovery."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from core import recovery
from core.audit import audit_action
from core.errors import ToolError
from core.reconcilers import evaluate_postcondition
from core.recovery_models import Evidence, OperationState, Postcondition
from core.tooling import MUTATING, READ_ONLY, compact_errors

OperationId = Annotated[str, Field(min_length=1, max_length=128)]
Expected = Annotated[dict[str, Any], Field(default_factory=dict)]


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("list_uncertain_operations")
    def list_uncertain_operations(
        offset: Annotated[int, Field(ge=0, le=10_000_000)] = 0,
        limit: Annotated[int, Field(ge=1, le=1_000)] = 100,
    ) -> dict[str, Any]:
        """List every unresolved uncertain operation with pagination; never replays work."""

        return recovery.OPERATION_RECOVERY.list_operations(
            state=OperationState.UNCERTAIN,
            offset=offset,
            max_items=limit,
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("inspect_uncertain_operation")
    def inspect_uncertain_operation(operation_id: OperationId) -> dict[str, Any]:
        """Inspect one journaled operation, including durable reconciliation evidence."""

        return recovery.OPERATION_RECOVERY.inspect(operation_id)

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("reconcile_operation")
    def reconcile_operation(
        operation_id: OperationId,
        kind: Literal["file_exists", "file_absent", "file_sha256", "git_head", "process_identity"],
        expected: Expected,
    ) -> dict[str, Any]:
        """Evaluate an allowlisted postcondition and durably resolve only conclusive evidence."""

        operation = recovery.OPERATION_RECOVERY.inspect(operation_id)
        if operation["state"] != OperationState.UNCERTAIN.value:
            raise ToolError("operation_not_uncertain", "Only an unresolved uncertain operation can be reconciled.")
        postcondition = Postcondition(kind=kind, expected=expected)
        evidence = evaluate_postcondition(postcondition)
        if evidence.data.get("conclusive") is not True:
            return {
                **operation,
                "reconciliation": "inconclusive",
                "candidate_postcondition": {"kind": kind, "expected": expected},
                "candidate_evidence": {"source": evidence.source, "data": evidence.data},
            }
        state = OperationState.SUCCEEDED if evidence.data.get("satisfied") is True else OperationState.FAILED
        audit_action(
            "reconcile_operation",
            target=operation_id,
            details={"kind": kind, "resolved_state": state.value},
            durable=True,
        )
        return recovery.OPERATION_RECOVERY.record_reconciliation(operation_id, state, postcondition, evidence)

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("acknowledge_uncertain_operation")
    def acknowledge_uncertain_operation(
        operation_id: OperationId,
        outcome: Literal["acknowledged", "unresolvable"],
        note: Annotated[str, Field(min_length=1, max_length=2_000)],
    ) -> dict[str, Any]:
        """Explicitly acknowledge an uncertain operation with bounded operator evidence."""

        operation = recovery.OPERATION_RECOVERY.inspect(operation_id)
        if operation["state"] != OperationState.UNCERTAIN.value:
            raise ToolError("operation_not_uncertain", "Only an unresolved uncertain operation can be acknowledged.")
        state = OperationState(outcome)
        evidence = Evidence("operator", {"note": note, "outcome": outcome})
        audit_action(
            "acknowledge_uncertain_operation",
            target=operation_id,
            details={"outcome": outcome, "note_chars": len(note)},
            durable=True,
        )
        return recovery.OPERATION_RECOVERY.acknowledge(operation_id, state, evidence)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("get_operation_history")
    def get_operation_history(
        operation_id: OperationId,
        offset: Annotated[int, Field(ge=0, le=10_000_000)] = 0,
        limit: Annotated[int, Field(ge=1, le=1_000)] = 100,
    ) -> dict[str, Any]:
        """Return bounded raw journal events for one operation in append order."""

        return recovery.OPERATION_RECOVERY.history(operation_id, offset, limit)

