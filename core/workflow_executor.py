"""Lease-aware execution of durable workflow operations."""

from __future__ import annotations

import time
import uuid
from typing import Any

from core.errors import ToolError
from core.reconcilers import evaluate_postcondition
from core.recovery_models import Postcondition
from core.workflow_actions import SideEffectUncertain, TransientActionError, execute_action, get_action_descriptor
from core.workflow_models import OperationState, StepDefinition, WorkflowDefinition, WorkflowState
from core.workflow_store import WorkflowStore


class WorkflowExecutor:
    def __init__(self, store: WorkflowStore) -> None:
        self.store = store

    @staticmethod
    def _postcondition(step: StepDefinition, result: dict[str, Any]) -> dict[str, Any]:
        descriptor = get_action_descriptor(step.action)
        if not descriptor.mutates:
            return {"conclusive": True, "satisfied": True, "source": "action_result"}
        if step.postcondition is None:
            raise ToolError("workflow_postcondition_required", f"Mutating action {step.action!r} requires a postcondition.")
        kind = str(step.postcondition.get("kind", ""))
        expected = dict(step.postcondition.get("expected", {}))
        if kind == "git_head_from_result":
            kind, expected = "git_head", {"repo": result.get("repo"), "commit": result.get("head")}
        elif kind == "git_index_contains_from_result":
            requested, staged = set(result.get("requested_paths", [])), set(result.get("staged_paths", []))
            return {"source": "git", "conclusive": True, "satisfied": bool(requested) and requested <= staged,
                    "staged_paths": sorted(staged)}
        elif kind == "job_succeeded_from_result":
            return {"source": "durable_job", "conclusive": True,
                    "satisfied": result.get("status") == "succeeded" and result.get("exit_code") == 0,
                    "job_id": result.get("job_id")}
        evidence = evaluate_postcondition(Postcondition(kind, expected))
        return {"source": evidence.source, **evidence.data}

    def execute(
        self,
        workflow_id: str,
        *,
        owner_id: str,
        dry_run: bool = False,
        lease_ttl_sec: float = 30.0,
    ) -> dict[str, Any]:
        current = self.store.get(workflow_id)
        if current["state"] == WorkflowState.COMPLETED.value:
            return current
        if current["state"] != WorkflowState.QUEUED.value:
            raise ToolError("workflow_not_queued", "Only a queued workflow can be executed.")
        if dry_run:
            return {**current, "dry_run": True, "would_execute": current["definition"]["steps"]}
        lease = self.store.acquire_lease(workflow_id, owner_id, lease_ttl_sec)
        try:
            current = self.store.transition(workflow_id, current["version"], WorkflowState.RUNNING)
            definition = WorkflowDefinition(
                current["definition"]["name"],
                tuple(StepDefinition(**step) for step in current["definition"]["steps"]),
                current["definition"].get("description", ""),
            )
            operations = self.store.list_operations(workflow_id)["items"]
            for index in range(int(current["current_step"]), len(definition.steps)):
                step, operation = definition.steps[index], operations[index]
                if operation["state"] == OperationState.SUCCEEDED.value:
                    continue
                attempts = int(operation["attempts"])
                while True:
                    attempts += 1
                    self.store.checkpoint_operation(
                        operation["operation_id"], OperationState.RUNNING,
                        lease_token=lease.lease_token, increment_attempt=True,
                    )
                    self.store.checkpoint_step(
                        workflow_id, index, "running", increment_attempt=True, lease_token=lease.lease_token,
                    )
                    try:
                        result = execute_action(step.action, step.arguments, step.timeout_sec)
                        evidence = self._postcondition(step, result)
                        if evidence.get("conclusive") is not True:
                            raise SideEffectUncertain("Postcondition could not be evaluated conclusively.")
                        if evidence.get("satisfied") is not True:
                            raise ToolError("workflow_postcondition_failed", "Step postcondition was not satisfied.")
                        self.store.checkpoint_operation(
                            operation["operation_id"], OperationState.SUCCEEDED,
                            lease_token=lease.lease_token, result=result, evidence=evidence,
                        )
                        self.store.checkpoint_step(
                            workflow_id, index, "completed",
                            evidence={"action": result, "postcondition": evidence}, lease_token=lease.lease_token,
                        )
                        latest, next_index = self.store.get(workflow_id), index + 1
                        if next_index == len(definition.steps):
                            current = self.store.transition(
                                workflow_id, latest["version"], WorkflowState.COMPLETED, current_step=next_index,
                            )
                        else:
                            current = self.store.advance_running(
                                workflow_id, latest["version"], current_step=next_index,
                            )
                        break
                    except TransientActionError as exc:
                        self.store.checkpoint_operation(
                            operation["operation_id"], OperationState.FAILED,
                            lease_token=lease.lease_token, error=str(exc),
                        )
                        if attempts <= step.max_retries:
                            lease = self.store.renew_lease(workflow_id, lease.lease_token, lease_ttl_sec)
                            time.sleep(min(2 ** (attempts - 1), 5))
                            continue
                        return self._fail(workflow_id, index, lease.lease_token, str(exc), uncertain=False)
                    except SideEffectUncertain as exc:
                        self.store.checkpoint_operation(
                            operation["operation_id"], OperationState.UNCERTAIN,
                            lease_token=lease.lease_token, error=str(exc),
                        )
                        return self._fail(workflow_id, index, lease.lease_token, str(exc), uncertain=True)
                    except Exception as exc:
                        self.store.checkpoint_operation(
                            operation["operation_id"], OperationState.FAILED,
                            lease_token=lease.lease_token, error=str(exc),
                        )
                        return self._fail(workflow_id, index, lease.lease_token, str(exc), uncertain=False)
                lease = self.store.renew_lease(workflow_id, lease.lease_token, lease_ttl_sec)
            return current
        finally:
            self.store.release_lease(workflow_id, lease.lease_token)

    def _fail(self, workflow_id: str, index: int, lease_token: str, error: str, *, uncertain: bool) -> dict[str, Any]:
        step_state = "uncertain" if uncertain else "failed"
        workflow_state = WorkflowState.UNCERTAIN if uncertain else WorkflowState.FAILED
        self.store.checkpoint_step(workflow_id, index, step_state, error=error, lease_token=lease_token)
        latest = self.store.get(workflow_id)
        return self.store.transition(workflow_id, latest["version"], workflow_state, last_error=error)

    def run(self, workflow_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        return self.execute(workflow_id, owner_id=f"legacy-{uuid.uuid4().hex}", dry_run=dry_run)
