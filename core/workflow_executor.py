"""Lease-aware execution of durable workflow operations."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from core.errors import ToolError
from core.reconcilers import evaluate_postcondition
from core.recovery_models import Postcondition
from core.workflow_actions import (
    ActionCancelled,
    ActionContext,
    SideEffectUncertain,
    TransientActionError,
    execute_action,
    get_action_descriptor,
)
from core.workflow_models import OperationState, StepDefinition, WorkflowState
from core.workflow_store import WorkflowStore


class _LeaseLostDuringAction(RuntimeError):
    pass


class _LeaseHeartbeat:
    def __init__(self, store: WorkflowStore, workflow_id: str, lease_token: str, ttl_sec: float) -> None:
        self.store = store
        self.workflow_id = workflow_id
        self.lease_token = lease_token
        self.ttl_sec = ttl_sec
        self.interval_sec = min(max(ttl_sec / 3.0, 1.0), 10.0)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: Exception | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"workflow-lease-{self.workflow_id[:8]}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_sec):
            try:
                self.store.renew_lease(self.workflow_id, self.lease_token, self.ttl_sec)
            except Exception as exc:  # lease loss is handled by the owning executor
                self.error = exc
                self._stop.set()
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def ensure_current(self) -> None:
        if self.error is not None:
            raise _LeaseLostDuringAction("Workflow lease heartbeat failed.") from self.error
        try:
            self.store.require_lease(self.workflow_id, self.lease_token)
        except ToolError as exc:
            raise _LeaseLostDuringAction("Workflow lease was lost while the action was running.") from exc


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

    def _run_step_with_heartbeat(
        self,
        workflow_id: str,
        operation_id: str,
        lease_token: str,
        lease_ttl_sec: float,
        step: StepDefinition,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        heartbeat = _LeaseHeartbeat(self.store, workflow_id, lease_token, lease_ttl_sec)
        descriptor = get_action_descriptor(step.action)
        context = ActionContext(
            workflow_id=workflow_id,
            operation_id=operation_id,
            is_cancel_requested=lambda: self.store.cancel_requested(workflow_id),
            persist_external_ref=lambda value: None,
            persist_intent_evidence=lambda value: None,
        )
        result: dict[str, Any] | None = None
        evidence: dict[str, Any] | None = None
        error: Exception | None = None
        heartbeat.start()
        try:
            try:
                result = execute_action(step.action, step.arguments, step.timeout_sec, context)
                evidence = self._postcondition(step, result)
            except Exception as exc:
                error = exc
        finally:
            heartbeat.stop()
        try:
            heartbeat.ensure_current()
        except _LeaseLostDuringAction as exc:
            detail = "Workflow lease was lost while a mutating action was running." if descriptor.mutates else str(exc)
            raise _LeaseLostDuringAction(detail) from exc
        if error is not None:
            raise error
        assert result is not None and evidence is not None
        return result, evidence

    def _cancel_operation(
        self,
        workflow_id: str,
        index: int,
        operation_id: str,
        lease_token: str,
        *,
        error: str = "workflow cancellation requested",
    ) -> dict[str, Any]:
        operation = self.store.get_operation(operation_id)
        if operation["state"] != OperationState.CANCELLED.value:
            self.store.checkpoint_operation(
                operation_id,
                OperationState.CANCELLED,
                lease_token=lease_token,
                error=error,
            )
        self.store.checkpoint_step(
            workflow_id,
            index,
            "cancelled",
            error=error,
            lease_token=lease_token,
        )
        latest = self.store.get(workflow_id)
        if latest["state"] in {WorkflowState.CANCELLING.value, WorkflowState.RUNNING.value}:
            return self.store.transition(
                workflow_id,
                latest["version"],
                WorkflowState.CANCELLED,
                current_step=index,
                lease_token=lease_token,
            )
        return latest

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
            current = self.store.transition(
                workflow_id, current["version"], WorkflowState.RUNNING, lease_token=lease.lease_token,
            )
            definition = self.store.execution_definition(workflow_id)
            operations = self.store.list_operations(workflow_id)["items"]
            for index in range(int(current["current_step"]), len(definition.steps)):
                step, operation = definition.steps[index], operations[index]
                if operation["state"] == OperationState.SUCCEEDED.value:
                    continue
                if self.store.cancel_requested(workflow_id):
                    return self._cancel_operation(
                        workflow_id, index, operation["operation_id"], lease.lease_token,
                    )
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
                        result, evidence = self._run_step_with_heartbeat(
                            workflow_id, operation["operation_id"], lease.lease_token, lease_ttl_sec, step,
                        )
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
                        if latest["state"] == WorkflowState.CANCELLED.value:
                            return latest
                        if latest["state"] == WorkflowState.CANCELLING.value:
                            return self.store.transition(
                                workflow_id, latest["version"], WorkflowState.CANCELLED,
                                current_step=next_index, lease_token=lease.lease_token,
                            )
                        if next_index == len(definition.steps):
                            current = self.store.transition(
                                workflow_id, latest["version"], WorkflowState.COMPLETED,
                                current_step=next_index, lease_token=lease.lease_token,
                            )
                        else:
                            current = self.store.advance_running(
                                workflow_id, latest["version"], current_step=next_index,
                                lease_token=lease.lease_token,
                            )
                        break
                    except _LeaseLostDuringAction as exc:
                        raise ToolError("workflow_lease_lost", str(exc)) from exc
                    except ActionCancelled as exc:
                        return self._cancel_operation(
                            workflow_id, index, operation["operation_id"], lease.lease_token, error=str(exc),
                        )
                    except TransientActionError as exc:
                        self.store.checkpoint_operation(
                            operation["operation_id"], OperationState.FAILED,
                            lease_token=lease.lease_token, error=str(exc),
                        )
                        if self.store.cancel_requested(workflow_id):
                            return self._cancel_operation(
                                workflow_id, index, operation["operation_id"], lease.lease_token, error=str(exc),
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
            self.store.release_lease_if_current(workflow_id, lease.lease_token)

    def _fail(self, workflow_id: str, index: int, lease_token: str, error: str, *, uncertain: bool) -> dict[str, Any]:
        step_state = "uncertain" if uncertain else "failed"
        workflow_state = WorkflowState.UNCERTAIN if uncertain else WorkflowState.FAILED
        self.store.checkpoint_step(workflow_id, index, step_state, error=error, lease_token=lease_token)
        latest = self.store.get(workflow_id)
        return self.store.transition(
            workflow_id, latest["version"], workflow_state, last_error=error, lease_token=lease_token,
        )

    def run(self, workflow_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        return self.execute(workflow_id, owner_id=f"legacy-{uuid.uuid4().hex}", dry_run=dry_run)
