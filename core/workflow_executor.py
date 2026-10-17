"""Lease-aware execution of durable workflow operations."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any

from core.config import SETTINGS
from core.errors import ToolError
from core.execution_models import ExecutionIntent
from core.reconcilers import evaluate_postcondition
from core.recovery_models import Postcondition
from core.resource_claims import make_resource_capability, resource_capability
from core.workflow_actions import (
    ActionCancelled,
    ActionContext,
    SideEffectUncertain,
    TransientActionError,
    derive_action_resource_claims,
    execute_action,
    get_action_descriptor,
    prepare_action_intent,
)
from core.workflow_models import OperationState, StepDefinition, WorkflowDefinition, WorkflowState
from core.workflow_store import WorkflowStore

LEASE_HEARTBEAT_MIN_SEC = 1.0
LEASE_HEARTBEAT_MAX_SEC = 10.0


def _lease_heartbeat_interval(ttl_sec: float) -> float:
    return min(max(ttl_sec / 3.0, LEASE_HEARTBEAT_MIN_SEC), LEASE_HEARTBEAT_MAX_SEC)


def _retry_backoff_seconds(attempt: int) -> float:
    return float(min(2 ** (attempt - 1), 5))


class _LeaseLostDuringAction(RuntimeError):
    pass


class _LeaseHeartbeat:
    def __init__(self, store: WorkflowStore, workflow_id: str, lease_token: str, ttl_sec: float) -> None:
        self.store = store
        self.workflow_id = workflow_id
        self.lease_token = lease_token
        self.ttl_sec = ttl_sec
        self.interval_sec = _lease_heartbeat_interval(ttl_sec)
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


RoutePlanner = Callable[[ExecutionIntent], dict[str, Any]]


class NativeOnlyRouting:
    """Declaration that a caller never submits a step with an execution intent.

    Required instead of an omitted argument so a new entrypoint cannot silently
    construct an executor that fails every routed step with
    `workflow_route_planner_missing`. A caller that does route must pass a planner.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "NATIVE_ONLY_ROUTING"


NATIVE_ONLY_ROUTING = NativeOnlyRouting()


class WorkflowExecutor:
    def __init__(
        self,
        store: WorkflowStore,
        *,
        route_planner: RoutePlanner | NativeOnlyRouting,
        enable_dag_execution: bool = False,
    ) -> None:
        """Require every caller to declare how its workflow steps are routed.

        `route_planner` deliberately has no default: a caller that can execute a
        step declaring an execution intent must supply a plan callable, and a
        caller that never routes must pass `NATIVE_ONLY_ROUTING` explicitly.
        Omitting the argument is a TypeError instead of a run-time failure.
        """
        if isinstance(route_planner, NativeOnlyRouting):
            self.route_planner: RoutePlanner | None = None
        elif callable(route_planner):
            self.route_planner = route_planner
        else:
            raise TypeError("route_planner must be a plan callable or NATIVE_ONLY_ROUTING")
        self.store = store
        self.enable_dag_execution = bool(enable_dag_execution)

    def _plan_route(
        self,
        step: StepDefinition,
        operation_id: str,
        lease_token: str,
    ) -> None:
        if step.execution_intent is None:
            return
        if self.route_planner is None:
            raise ToolError(
                "workflow_route_planner_missing",
                "This workflow step declares an execution intent but the executor was "
                "constructed with native-only routing and no route planner.",
            )
        intent = ExecutionIntent(**step.execution_intent)
        decision = self.route_planner(intent)
        selected_route = decision.get("selected_route")
        policy_version = decision.get("router_policy_version")
        if decision.get("ok") is not True or not isinstance(selected_route, str) or not selected_route:
            raise ToolError(
                "workflow_route_unavailable",
                f"No valid execution route is available for intent {intent.name!r}.",
            )
        if not isinstance(policy_version, str) or not policy_version:
            raise ToolError(
                "workflow_route_policy_missing",
                "Route planner did not return a policy version.",
            )
        self.store.record_route_decision(
            operation_id,
            lease_token=lease_token,
            selected_route=selected_route,
            router_policy_version=policy_version,
            decision=decision,
        )
        expected_route = get_action_descriptor(step.action).execution_route
        if expected_route is None:
            raise ToolError(
                "workflow_action_route_unmapped",
                f"Workflow action {step.action!r} has no fixed execution route.",
            )
        if selected_route != expected_route:
            raise ToolError(
                "workflow_route_mismatch",
                f"Selected route {selected_route!r} does not match fixed workflow action route {expected_route!r}.",
            )

    @staticmethod
    def _postcondition(step: StepDefinition, result: dict[str, Any], *, timeout_sec: float | None = None) -> dict[str, Any]:
        descriptor = get_action_descriptor(step.action)
        if not descriptor.mutates:
            return {"conclusive": True, "satisfied": True, "source": "action_result"}
        if step.postcondition is None:
            raise ToolError("workflow_postcondition_required", f"Mutating action {step.action!r} requires a postcondition.")
        kind = str(step.postcondition.get("kind", ""))
        expected = dict(step.postcondition.get("expected", {}))
        if kind == "job_request_key_state" and result.get("status") in {
            "succeeded", "failed", "cancelled", "timed_out", "interrupted"
        }:
            return {
                "source": "durable_job",
                "conclusive": True,
                "satisfied": result.get("status") == expected.get("status", "succeeded") and result.get("exit_code") == 0,
                "job_id": result.get("job_id"),
                "request_key": expected.get("request_key"),
            }
        evidence = evaluate_postcondition(Postcondition(kind, expected), timeout_sec=timeout_sec)
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

        def persist_external_ref(value: dict[str, Any]) -> None:
            self.store.update_operation_context(
                operation_id,
                lease_token=lease_token,
                external_ref=value,
            )

        def persist_intent_evidence(value: dict[str, Any]) -> None:
            self.store.update_operation_context(
                operation_id,
                lease_token=lease_token,
                intent_evidence=value,
            )

        context = ActionContext(
            workflow_id=workflow_id,
            operation_id=operation_id,
            is_cancel_requested=lambda: self.store.cancel_requested(workflow_id),
            persist_external_ref=persist_external_ref,
            persist_intent_evidence=persist_intent_evidence,
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
                return_operation=False,
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

    def _run_dag_attempt(
        self,
        workflow_id: str,
        operation_id: str,
        lease_token: str,
        step: StepDefinition,
        reserved: dict[str, Any],
        resource_request_id: str,
        resource_owner_token: str,
        workflow_deadline: float,
        stop_signal: threading.Event,
    ) -> dict[str, Any]:
        """Execute one already-admitted DAG attempt behind resource and dispatch barriers."""

        attempt_token = str(reserved.get("attempt_token") or "")
        if not attempt_token:
            raise ToolError("workflow_attempt_missing", "Reserved workflow operation has no attempt token.")
        descriptor = get_action_descriptor(step.action)
        effectful = descriptor.mutates or step.action == "verify_changes"
        deadline = min(workflow_deadline, time.monotonic() + step.timeout_sec)
        retry_safe = False

        def remaining() -> float:
            budget = deadline - time.monotonic()
            if budget <= 0:
                raise TimeoutError("Workflow attempt deadline expired.")
            return budget

        crossed_dispatch_barrier = False
        canonical_postcondition: dict[str, Any] | None = None

        def persist_external_ref(value: dict[str, Any]) -> None:
            self.store.update_operation_context(
                operation_id,
                lease_token=lease_token,
                external_ref=value,
            )

        def persist_intent_evidence(value: dict[str, Any]) -> None:
            self.store.update_operation_context(
                operation_id,
                lease_token=lease_token,
                intent_evidence=value,
            )

        def settle_resource(final_state: OperationState, *, reason: str = "") -> None:
            if final_state is OperationState.UNCERTAIN:
                self.store.quarantine_resource_request(
                    resource_request_id,
                    owner_token=resource_owner_token,
                    reason=reason or "workflow effect outcome is uncertain",
                )
                return
            if not self.store.release_resource_request(
                resource_request_id,
                owner_token=resource_owner_token,
            ):
                resource = self.store.resource_request(resource_request_id)
                if resource["state"] not in {"released", "quarantined"}:
                    raise ToolError(
                        "resource_release_failed",
                        "Workflow operation settled but its durable resource claim could not be released.",
                    )

        context = ActionContext(
            workflow_id=workflow_id,
            operation_id=operation_id,
            is_cancel_requested=lambda: stop_signal.is_set() or self.store.cancel_requested(workflow_id),
            persist_external_ref=persist_external_ref,
            persist_intent_evidence=persist_intent_evidence,
        )
        capability = make_resource_capability(resource_request_id, resource_owner_token, self.store.path)
        try:
            with resource_capability(capability):
                remaining()
                if stop_signal.is_set():
                    raise ActionCancelled("Workflow stopped before dispatch.")
                self._plan_route(step, operation_id, lease_token)
                latest = self.store.get_operation(operation_id)
                canonical_postcondition, intent_evidence = prepare_action_intent(
                    step.action,
                    step.arguments,
                    step.postcondition,
                    timeout_sec=remaining(),
                )
                prepared = self.store.prepare_operation_attempt(
                    operation_id,
                    expected_version=int(latest["version"]),
                    attempt_token=attempt_token,
                    lease_token=lease_token,
                    intent_evidence=intent_evidence,
                    postcondition=canonical_postcondition,
                )
                remaining()
                if stop_signal.is_set():
                    raise ActionCancelled("Workflow stopped before dispatch.")
                self.store.mark_operation_may_dispatch(
                    operation_id,
                    expected_version=int(prepared["version"]),
                    attempt_token=attempt_token,
                    lease_token=lease_token,
                )
                crossed_dispatch_barrier = True
                execution_step = StepDefinition(
                    name=step.name,
                    action=step.action,
                    arguments=step.arguments,
                    timeout_sec=step.timeout_sec,
                    max_retries=step.max_retries,
                    postcondition=canonical_postcondition,
                    execution_intent=step.execution_intent,
                    step_id=step.step_id,
                    depends_on=step.depends_on,
                )
                result = execute_action(step.action, step.arguments, remaining(), context)
                evidence = self._postcondition(execution_step, result, timeout_sec=remaining())
                if evidence.get("conclusive") is not True:
                    raise SideEffectUncertain("Postcondition could not be evaluated conclusively.")
                if evidence.get("satisfied") is not True:
                    raise ToolError("workflow_postcondition_failed", "Step postcondition was not satisfied.")
            latest = self.store.get_operation(operation_id)
            settled = self.store.complete_operation_attempt(
                operation_id,
                expected_version=int(latest["version"]),
                attempt_token=attempt_token,
                lease_token=lease_token,
                final_state=OperationState.SUCCEEDED,
                result=result,
                evidence=evidence,
            )
            settle_resource(OperationState(str(settled["state"])))
            return settled
        except _LeaseLostDuringAction:
            self.store.quarantine_resource_request(
                resource_request_id,
                owner_token=resource_owner_token,
                reason="workflow lease ownership was lost during an active attempt",
            )
            raise
        except ActionCancelled as exc:
            final_state = OperationState.UNCERTAIN if crossed_dispatch_barrier and effectful else OperationState.CANCELLED
            error = str(exc)
        except TransientActionError as exc:
            retry_safe = not effectful and descriptor.retry_policy == "transient"
            final_state = OperationState.FAILED if not effectful else OperationState.UNCERTAIN
            error = str(exc)
        except SideEffectUncertain as exc:
            final_state = OperationState.UNCERTAIN
            error = str(exc)
        except Exception as exc:
            final_state = OperationState.UNCERTAIN if crossed_dispatch_barrier and effectful else OperationState.FAILED
            error = str(exc)

        latest = self.store.get_operation(operation_id)
        try:
            settled = self.store.complete_operation_attempt(
                operation_id,
                expected_version=int(latest["version"]),
                attempt_token=attempt_token,
                lease_token=lease_token,
                final_state=final_state,
                evidence={
                    "source": "workflow_attempt",
                    "conclusive": final_state is not OperationState.UNCERTAIN,
                    "satisfied": False,
                    "retry_safe": retry_safe,
                    "dispatch_phase": "may_dispatch" if crossed_dispatch_barrier else str(latest.get("dispatch_phase") or "reserved"),
                },
                error=error,
            )
            settle_resource(OperationState(str(settled["state"])), reason=error)
            return settled
        except ToolError as settle_error:
            if settle_error.code in {
                "workflow_lease_expired",
                "workflow_lease_token_mismatch",
                "workflow_attempt_conflict",
            }:
                self.store.quarantine_resource_request(
                    resource_request_id,
                    owner_token=resource_owner_token,
                    reason="workflow attempt lost durable ownership before settlement",
                )
                raise _LeaseLostDuringAction("Workflow attempt lost durable ownership before settlement.") from settle_error
            raise

    def _execute_dag(
        self,
        workflow_id: str,
        current: dict[str, Any],
        definition: WorkflowDefinition,
        *,
        owner_id: str,
        lease_ttl_sec: float,
    ) -> dict[str, Any]:
        """Run dependency-ready DAG steps with one lease heartbeat and bounded in-flight work."""

        self.store.materialize_operations(workflow_id, return_operations=False)
        lease = self.store.acquire_lease(
            workflow_id,
            owner_id,
            lease_ttl_sec,
            allow_reentry=False,
        )
        execution_id = uuid.uuid4().hex
        heartbeat = _LeaseHeartbeat(self.store, workflow_id, lease.lease_token, lease_ttl_sec)
        effective_cap = min(
            int(definition.max_parallel_steps),
            SETTINGS.workflow_max_parallel_steps,
            SETTINGS.max_running_workflow_steps,
        )
        total_timeout = float(definition.total_timeout_sec or 7_200.0)
        deadline = time.monotonic() + total_timeout
        futures: dict[Future[Any], int] = {}
        resource_waits: dict[int, tuple[str, str]] = {}
        stop_admission_reason: str | None = None
        stop_signal = threading.Event()
        retry_after: dict[int, tuple[int, float]] = {}

        def retryable(operation: dict[str, Any], index: int) -> bool:
            descriptor = get_action_descriptor(definition.steps[index].action)
            return (operation["state"] == "failed" and descriptor.retry_policy == "transient" and
                    not descriptor.mutates and (operation.get("evidence") or {}).get("retry_safe") is True and
                    int(operation["attempts"]) <= definition.steps[index].max_retries)

        def operations() -> list[dict[str, Any]]:
            items: list[dict[str, Any]] = []
            while True:
                page = self.store.list_operations(workflow_id, offset=len(items), limit=500)
                items.extend(page["items"])
                if not page["has_more"]:
                    return items

        try:
            current = self.store.transition(
                workflow_id,
                int(current["version"]),
                WorkflowState.RUNNING,
                lease_token=lease.lease_token,
            )
            heartbeat.start()
            with ThreadPoolExecutor(max_workers=effective_cap, thread_name_prefix="workflow-dag") as pool:
                while True:
                    try:
                        heartbeat.ensure_current()
                    except _LeaseLostDuringAction as exc:
                        stop_admission_reason = str(exc)

                    if time.monotonic() >= deadline and stop_admission_reason is None:
                        stop_admission_reason = "workflow total deadline expired"
                    if self.store.cancel_requested(workflow_id) and stop_admission_reason is None:
                        stop_admission_reason = "workflow cancellation requested"

                    snapshot = operations()
                    if any(
                        str(operation["state"])
                        in {
                            OperationState.FAILED.value,
                            OperationState.UNCERTAIN.value,
                            OperationState.UNRESOLVABLE.value,
                        }
                        for index, operation in enumerate(snapshot) if not retryable(operation, index)
                    ):
                        stop_admission_reason = stop_admission_reason or "workflow branch failed or became uncertain"

                    if stop_admission_reason is not None:
                        stop_signal.set()
                    claimed_any = False
                    if stop_admission_reason is None:
                        ready_indices = set(self.store.get(workflow_id).get("ready_step_indices", []))
                        for index, operation in enumerate(snapshot):
                            if len(futures) >= effective_cap:
                                break
                            retry = retryable(operation, index)
                            if str(operation["state"]) != OperationState.CREATED.value and not retry:
                                continue
                            if index not in ready_indices and not retry:
                                continue
                            if retry:
                                count = int(operation["attempts"])
                                if retry_after.get(index, (-1, 0))[0] != count:
                                    retry_after[index] = (count, time.monotonic() + _retry_backoff_seconds(count))
                                if time.monotonic() < retry_after[index][1]:
                                    continue
                            resource_request_id, resource_owner_token = resource_waits.get(
                                index,
                                (f"resource-{uuid.uuid4().hex}", f"resource-owner-{uuid.uuid4().hex}"),
                            )
                            resource_waits[index] = (resource_request_id, resource_owner_token)
                            resource_admission = self.store.reserve_resource_claims(
                                request_id=resource_request_id,
                                owner_token=resource_owner_token,
                                claims=derive_action_resource_claims(
                                    definition.steps[index].action,
                                    definition.steps[index].arguments,
                                    definition.steps[index].postcondition,
                                ),
                                category="workflow_step",
                                workflow_id=workflow_id,
                                operation_id=str(operation["operation_id"]),
                            )
                            if resource_admission["admitted"] is not True:
                                continue
                            reserved = self.store.claim_ready_operation(
                                str(operation["operation_id"]),
                                lease_token=lease.lease_token,
                                owner_id=owner_id,
                                execution_id=execution_id,
                                global_limit=SETTINGS.max_running_workflow_steps,
                            )
                            if reserved is None:
                                self.store.release_resource_request(
                                    resource_request_id,
                                    owner_token=resource_owner_token,
                                )
                                resource_waits.pop(index, None)
                                continue
                            self.store.bind_resource_request_attempt(
                                resource_request_id,
                                owner_token=resource_owner_token,
                                attempt_token=str(reserved["attempt_token"]),
                            )
                            future = pool.submit(
                                self._run_dag_attempt,
                                workflow_id,
                                str(operation["operation_id"]),
                                lease.lease_token,
                                definition.steps[index],
                                reserved,
                                resource_request_id,
                                resource_owner_token,
                                deadline,
                                stop_signal,
                            )
                            futures[future] = index
                            resource_waits.pop(index, None)
                            claimed_any = True

                    if futures:
                        remaining = max(0.0, deadline - time.monotonic())
                        wait_timeout = min(max(0.05, heartbeat.interval_sec), remaining) if remaining else 0.05
                        done, _ = wait(tuple(futures), timeout=wait_timeout, return_when=FIRST_COMPLETED)
                        for future in done:
                            futures.pop(future, None)
                            try:
                                future.result()
                            except _LeaseLostDuringAction as exc:
                                stop_admission_reason = stop_admission_reason or str(exc)
                        continue

                    if stop_admission_reason is not None:
                        try:
                            return self.store.recompute_workflow_aggregate(
                                workflow_id,
                                lease_token=lease.lease_token,
                                stop_admission_reason=stop_admission_reason,
                                keep_running=False,
                            )
                        except ToolError as exc:
                            if exc.code in {"workflow_lease_expired", "workflow_lease_token_mismatch"}:
                                raise ToolError("workflow_lease_lost", stop_admission_reason) from exc
                            raise

                    if any(retryable(operation, index) for index, operation in enumerate(snapshot)):
                        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
                        continue
                    aggregate = self.store.recompute_workflow_aggregate(
                        workflow_id,
                        lease_token=lease.lease_token,
                        keep_running=True,
                    )
                    if aggregate["state"] in {
                        WorkflowState.COMPLETED.value,
                        WorkflowState.FAILED.value,
                        WorkflowState.UNCERTAIN.value,
                        WorkflowState.CANCELLED.value,
                    }:
                        return aggregate
                    if not claimed_any:
                        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        finally:
            for resource_request_id, resource_owner_token in tuple(resource_waits.values()):
                self.store.release_resource_request(
                    resource_request_id,
                    owner_token=resource_owner_token,
                )
            heartbeat.stop()
            self.store.release_lease_if_current(workflow_id, lease.lease_token)

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
        definition = self.store.execution_definition(workflow_id)
        if definition.execution_mode == "dag":
            if not self.enable_dag_execution:
                raise ToolError(
                    "workflow_dag_execution_unavailable",
                    "DAG workflow execution is not enabled until scheduler and resource gates are active.",
                )
            return self._execute_dag(
                workflow_id,
                current,
                definition,
                owner_id=owner_id,
                lease_ttl_sec=lease_ttl_sec,
            )
        self.store.materialize_operations(workflow_id, return_operations=False)
        lease = self.store.acquire_lease(workflow_id, owner_id, lease_ttl_sec)
        try:
            current = self.store.transition(
                workflow_id, current["version"], WorkflowState.RUNNING, lease_token=lease.lease_token,
            )
            definition = self.store.execution_definition(workflow_id)
            operations: list[dict[str, Any]] = []
            while True:
                page = self.store.list_operations(workflow_id, offset=len(operations), limit=500)
                operations.extend(page["items"])
                if not page["has_more"]:
                    break
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
                    if self.store.cancel_requested(workflow_id):
                        return self._cancel_operation(
                            workflow_id, index, operation["operation_id"], lease.lease_token,
                        )
                    attempts += 1
                    try:
                        self._plan_route(
                            step,
                            operation["operation_id"],
                            lease.lease_token,
                        )
                        canonical_postcondition, intent_evidence = prepare_action_intent(
                            step.action, step.arguments, step.postcondition,
                        )
                    except Exception as exc:
                        self.store.checkpoint_operation(
                            operation["operation_id"],
                            OperationState.RUNNING,
                            lease_token=lease.lease_token,
                            increment_attempt=True,
                            return_operation=False,
                        )
                        self.store.checkpoint_step(
                            workflow_id, index, "running", increment_attempt=True, lease_token=lease.lease_token,
                        )
                        self.store.checkpoint_operation(
                            operation["operation_id"],
                            OperationState.FAILED,
                            lease_token=lease.lease_token,
                            error=str(exc),
                            return_operation=False,
                        )
                        return self._fail(workflow_id, index, lease.lease_token, str(exc), uncertain=False)
                    execution_step = StepDefinition(
                        step.name,
                        step.action,
                        step.arguments,
                        step.timeout_sec,
                        step.max_retries,
                        canonical_postcondition,
                        step.execution_intent,
                    )
                    self.store.checkpoint_operation(
                        operation["operation_id"],
                        OperationState.RUNNING,
                        lease_token=lease.lease_token,
                        increment_attempt=True,
                        intent_evidence=intent_evidence,
                        postcondition=canonical_postcondition,
                        return_operation=False,
                    )
                    self.store.checkpoint_step(
                        workflow_id, index, "running", increment_attempt=True, lease_token=lease.lease_token,
                    )
                    try:
                        result, evidence = self._run_step_with_heartbeat(
                            workflow_id,
                            operation["operation_id"],
                            lease.lease_token,
                            lease_ttl_sec,
                            execution_step,
                        )
                        if evidence.get("conclusive") is not True:
                            raise SideEffectUncertain("Postcondition could not be evaluated conclusively.")
                        if evidence.get("satisfied") is not True:
                            raise ToolError("workflow_postcondition_failed", "Step postcondition was not satisfied.")
                        self.store.checkpoint_operation(
                            operation["operation_id"], OperationState.SUCCEEDED,
                            lease_token=lease.lease_token, result=result, evidence=evidence,
                            return_operation=False,
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
                            return_operation=False,
                        )
                        if self.store.cancel_requested(workflow_id):
                            return self._cancel_operation(
                                workflow_id, index, operation["operation_id"], lease.lease_token, error=str(exc),
                            )
                        if get_action_descriptor(step.action).retry_policy == "transient" and attempts <= step.max_retries:
                            lease = self.store.renew_lease(workflow_id, lease.lease_token, lease_ttl_sec)
                            heartbeat = _LeaseHeartbeat(self.store, workflow_id, lease.lease_token, lease_ttl_sec)
                            heartbeat.start()
                            try:
                                time.sleep(_retry_backoff_seconds(attempts))
                            finally:
                                heartbeat.stop()
                            try:
                                heartbeat.ensure_current()
                            except _LeaseLostDuringAction as lost:
                                raise ToolError("workflow_lease_lost", str(lost)) from lost
                            continue
                        return self._fail(workflow_id, index, lease.lease_token, str(exc), uncertain=False)
                    except SideEffectUncertain as exc:
                        self.store.checkpoint_operation(
                            operation["operation_id"], OperationState.UNCERTAIN,
                            lease_token=lease.lease_token, error=str(exc),
                            return_operation=False,
                        )
                        return self._fail(workflow_id, index, lease.lease_token, str(exc), uncertain=True)
                    except Exception as exc:
                        self.store.checkpoint_operation(
                            operation["operation_id"], OperationState.FAILED,
                            lease_token=lease.lease_token, error=str(exc),
                            return_operation=False,
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
