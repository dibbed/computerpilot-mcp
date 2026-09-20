"""Compatibility facade and executor for durable workflows."""

from __future__ import annotations

import time
from typing import Any

from core.errors import ToolError
from core.reconcilers import evaluate_postcondition
from core.recovery_models import Postcondition
from core.workflow_actions import SideEffectUncertain, TransientActionError, execute_action
from core.workflow_models import StepDefinition, WorkflowDefinition, WorkflowState
from core.workflow_store import WorkflowStore, redact_inputs, workflow_store

__all__ = [
    "StepDefinition",
    "WorkflowDefinition",
    "WorkflowExecutor",
    "WorkflowState",
    "WorkflowStore",
    "redact_inputs",
    "workflow_store",
]

_MUTATING_ACTIONS = {"git_stage", "git_commit", "run_durable_job"}


class WorkflowExecutor:
    def __init__(self, store: WorkflowStore) -> None:
        self.store = store

    @staticmethod
    def _postcondition(step: StepDefinition, result: dict[str, Any]) -> dict[str, Any]:
        if step.action not in _MUTATING_ACTIONS:
            return {"conclusive": True, "satisfied": True, "source": "action_result"}
        if step.postcondition is None:
            raise ToolError("workflow_postcondition_required", f"Mutating action {step.action!r} requires a postcondition.")
        kind = str(step.postcondition.get("kind", ""))
        expected = dict(step.postcondition.get("expected", {}))
        if kind == "git_head_from_result":
            kind = "git_head"
            expected = {"repo": result.get("repo"), "commit": result.get("head")}
        elif kind == "git_index_contains_from_result":
            requested = set(result.get("requested_paths", []))
            staged = set(result.get("staged_paths", []))
            return {
                "source": "git",
                "conclusive": True,
                "satisfied": bool(requested) and requested <= staged,
                "staged_paths": sorted(staged),
            }
        elif kind == "job_succeeded_from_result":
            return {
                "source": "durable_job",
                "conclusive": True,
                "satisfied": result.get("status") == "succeeded" and result.get("exit_code") == 0,
                "job_id": result.get("job_id"),
            }
        evidence = evaluate_postcondition(Postcondition(kind, expected))
        return {"source": evidence.source, **evidence.data}

    def run(self, workflow_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        current = self.store.get(workflow_id)
        if current["state"] != WorkflowState.QUEUED.value:
            raise ToolError("workflow_not_queued", "Only a queued workflow can be executed.")
        if dry_run:
            return {**current, "dry_run": True, "would_execute": current["definition"]["steps"]}
        current = self.store.transition(workflow_id, current["version"], WorkflowState.RUNNING)
        definition = WorkflowDefinition(
            current["definition"]["name"],
            tuple(StepDefinition(**step) for step in current["definition"]["steps"]),
            current["definition"].get("description", ""),
        )
        for index in range(int(current["current_step"]), len(definition.steps)):
            latest = self.store.get(workflow_id)
            if latest["state"] == WorkflowState.CANCELLED.value:
                self.store.checkpoint_step(workflow_id, index, "cancelled")
                return latest
            step = definition.steps[index]
            attempts = 0
            while True:
                attempts += 1
                self.store.checkpoint_step(workflow_id, index, "running", increment_attempt=True)
                try:
                    result = execute_action(step.action, step.arguments, step.timeout_sec)
                    evidence = self._postcondition(step, result)
                    if evidence.get("conclusive") is not True:
                        raise SideEffectUncertain("Postcondition could not be evaluated conclusively.")
                    if evidence.get("satisfied") is not True:
                        raise ToolError("workflow_postcondition_failed", "Step postcondition was not satisfied.")
                    self.store.checkpoint_step(
                        workflow_id, index, "completed", evidence={"action": result, "postcondition": evidence},
                    )
                    latest = self.store.get(workflow_id)
                    next_index = index + 1
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
                    if attempts <= step.max_retries:
                        time.sleep(min(2 ** (attempts - 1), 5))
                        continue
                    self.store.checkpoint_step(workflow_id, index, "failed", error=str(exc))
                    latest = self.store.get(workflow_id)
                    return self.store.transition(workflow_id, latest["version"], WorkflowState.FAILED, last_error=str(exc))
                except SideEffectUncertain as exc:
                    self.store.checkpoint_step(workflow_id, index, "uncertain", error=str(exc))
                    latest = self.store.get(workflow_id)
                    return self.store.transition(workflow_id, latest["version"], WorkflowState.UNCERTAIN, last_error=str(exc))
                except Exception as exc:
                    self.store.checkpoint_step(workflow_id, index, "failed", error=str(exc))
                    latest = self.store.get(workflow_id)
                    return self.store.transition(workflow_id, latest["version"], WorkflowState.FAILED, last_error=str(exc))
        return current
