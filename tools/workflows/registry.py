"""MCP workflow planning and lifecycle registration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import BaseModel, ConfigDict, Field

from core.audit import audit_action
from core.config import SETTINGS
from core.errors import ToolError
from core.tooling import MUTATING, READ_ONLY, compact_errors
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowState, workflow_store
from tools.workflows.builtins import builtin_workflow


class StepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_sec: float = Field(default=300, gt=0, le=3_600)
    max_retries: int = Field(default=0, ge=0, le=10)
    postcondition: dict[str, Any] | None = None


class WorkflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2_000)
    steps: list[StepInput] = Field(min_length=1, max_length=100)

    def definition(self) -> WorkflowDefinition:
        return WorkflowDefinition(
            self.name,
            tuple(StepDefinition(**step.model_dump()) for step in self.steps),
            self.description,
        )


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("workflow_plan")
    def workflow_plan(
        definition: WorkflowInput | None = None,
        builtin: Literal["implement_and_verify", "safe_git_commit", "prepare_release", "deploy_and_healthcheck"] | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate and return an immutable workflow plan without starting it."""
        if (definition is None) == (builtin is None):
            raise ToolError("workflow_definition_required", "Provide exactly one of definition or builtin.")
        planned = definition.definition() if definition else builtin_workflow(str(builtin), parameters or {})
        return {"ok": True, "definition": asdict(planned)}

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("workflow_start")
    def workflow_start(
        definition: WorkflowInput | None = None,
        builtin: Literal["implement_and_verify", "safe_git_commit", "prepare_release", "deploy_and_healthcheck"] | None = None,
        parameters: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        idempotency_key: Annotated[str | None, Field(max_length=200)] = None,
    ) -> dict[str, Any]:
        """Persist a queued workflow exactly once; execution uses allowlisted actions."""
        if (definition is None) == (builtin is None):
            raise ToolError("workflow_definition_required", "Provide exactly one of definition or builtin.")
        planned = definition.definition() if definition else builtin_workflow(str(builtin), parameters or {})
        result = workflow_store(SETTINGS.workflow_db).create(
            planned, inputs, idempotency_key=idempotency_key, initial_state=WorkflowState.QUEUED,
        )
        audit_action("workflow_start", target=result["workflow_id"], details={"name": planned.name}, durable=True)
        return result

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("workflow_status")
    def workflow_status(workflow_id: Annotated[str, Field(min_length=1, max_length=128)]) -> dict[str, Any]:
        """Return durable workflow state, redacted inputs, and step checkpoints."""
        return workflow_store(SETTINGS.workflow_db).get(workflow_id)

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("workflow_resume")
    def workflow_resume(
        workflow_id: Annotated[str, Field(min_length=1, max_length=128)],
        expected_version: Annotated[int, Field(ge=1)],
    ) -> dict[str, Any]:
        """Queue a paused/failed workflow; uncertain steps require reconciliation first."""
        current = workflow_store(SETTINGS.workflow_db).get(workflow_id)
        if current["state"] == WorkflowState.UNCERTAIN.value:
            raise ToolError("workflow_uncertain", "Reconcile the uncertain step before resume; it will not be replayed blindly.")
        if current["state"] not in {WorkflowState.PAUSED.value, WorkflowState.FAILED.value, WorkflowState.CREATED.value}:
            raise ToolError("workflow_not_resumable", f"Workflow state {current['state']!r} is not resumable.")
        audit_action("workflow_resume", target=workflow_id, durable=True)
        return workflow_store(SETTINGS.workflow_db).transition(workflow_id, expected_version, WorkflowState.QUEUED)

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("workflow_cancel")
    def workflow_cancel(
        workflow_id: Annotated[str, Field(min_length=1, max_length=128)],
        expected_version: Annotated[int, Field(ge=1)],
    ) -> dict[str, Any]:
        """Cancel a non-terminal workflow with a compare-and-swap version guard."""
        current = workflow_store(SETTINGS.workflow_db).get(workflow_id)
        if current["state"] in {WorkflowState.COMPLETED.value, WorkflowState.CANCELLED.value}:
            return current
        audit_action("workflow_cancel", target=workflow_id, durable=True)
        return workflow_store(SETTINGS.workflow_db).transition(workflow_id, expected_version, WorkflowState.CANCELLED)
