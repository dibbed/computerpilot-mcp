from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from core.errors import ToolError
from core.workflow_models import OperationState, StepDefinition, WorkflowDefinition, WorkflowState
from core.workflow_store import WorkflowStore


def _running(tmp_path: Path) -> tuple[WorkflowStore, str, str, list[dict[str, Any]]]:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    workflow = store.create(WorkflowDefinition(
        "recovery", tuple(StepDefinition(name, "check_file", {"path": name}, step_id=name) for name in ("a", "b", "c")),
        execution_mode="dag", max_parallel_steps=3,
    ), initial_state=WorkflowState.QUEUED)
    workflow_id = str(workflow["workflow_id"])
    lease = store.acquire_lease(workflow_id, "worker", 30, allow_reentry=False)
    store.transition(workflow_id, workflow["version"], WorkflowState.RUNNING, lease_token=lease.lease_token)
    operations = store.list_operations(workflow_id)["items"]
    return store, workflow_id, lease.lease_token, operations


def _attempt(store: WorkflowStore, operation_id: str, token: str, phase: str) -> dict[str, Any]:
    operation = store.claim_ready_operation(
        operation_id, lease_token=token, owner_id="worker", execution_id="execution", global_limit=3,
    )
    assert operation is not None
    if phase == "reserved":
        return operation
    operation = store.prepare_operation_attempt(
        operation_id, expected_version=operation["version"], attempt_token=operation["attempt_token"],
        lease_token=token, intent_evidence={"prepared": True}, postcondition=None,
    )
    if phase == "prepared":
        return operation
    return store.mark_operation_may_dispatch(
        operation_id, expected_version=operation["version"], attempt_token=operation["attempt_token"], lease_token=token,
    )


def _expire(store: WorkflowStore, workflow_id: str) -> None:
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE workflow_leases SET expires_at='2000-01-01' WHERE workflow_id=?", (workflow_id,))


@pytest.mark.parametrize("safe_phase", ["reserved", "prepared"])
def test_restart_recovers_every_branch_and_keeps_possible_effect_fenced(tmp_path: Path, safe_phase: str) -> None:
    store, workflow_id, token, operations = _running(tmp_path)
    first = _attempt(store, operations[0]["operation_id"], token, "may_dispatch")
    second = _attempt(store, operations[1]["operation_id"], token, "may_dispatch")
    _attempt(store, operations[2]["operation_id"], token, safe_phase)
    _expire(store, workflow_id)

    recovered = WorkflowStore(store.path)
    states = [operation["state"] for operation in recovered.list_operations(workflow_id)["items"]]
    assert states == ["uncertain", "uncertain", "created"]
    assert recovered.get(workflow_id)["state"] == "uncertain"
    assert {item["operation_id"] for item in recovered.active_admissions(workflow_id)} == {
        first["operation_id"], second["operation_id"],
    }
    assert recovered.recover_interrupted() == 0
    with pytest.raises(ToolError):
        recovered.complete_operation_attempt(
            second["operation_id"], expected_version=second["version"], attempt_token=second["attempt_token"],
            lease_token=token, final_state=OperationState.SUCCEEDED,
        )


def test_resolution_of_one_branch_preserves_sibling_uncertainty(tmp_path: Path) -> None:
    store, workflow_id, token, operations = _running(tmp_path)
    for operation in operations[:2]:
        store.checkpoint_operation(operation["operation_id"], OperationState.RUNNING, lease_token=token)
        store.checkpoint_operation(operation["operation_id"], OperationState.UNCERTAIN, lease_token=token)
    current = store.get(workflow_id)
    store.transition(workflow_id, current["version"], WorkflowState.UNCERTAIN, lease_token=token)
    operation = store.get_operation(operations[1]["operation_id"])
    result = store.resolve_uncertain_operation(
        operation["operation_id"], operation["version"], OperationState.ACKNOWLEDGED,
        evidence={"source": "operator_assertion", "conclusive": True, "satisfied": True}, event_type="test_resolution",
    )
    assert result["workflow"]["state"] == "uncertain"
    assert result["workflow"]["current_step"] == 0
    assert store.get_operation(operations[0]["operation_id"])["state"] == "uncertain"


def test_restart_repairs_all_terminal_projections_without_replaying(tmp_path: Path) -> None:
    store, workflow_id, token, operations = _running(tmp_path)
    for operation in operations:
        current = _attempt(store, operation["operation_id"], token, "may_dispatch")
        store.complete_operation_attempt(
            current["operation_id"], expected_version=current["version"], attempt_token=current["attempt_token"],
            lease_token=token, final_state=OperationState.SUCCEEDED, evidence={"conclusive": True, "satisfied": True},
        )
    # Crash after operation truth, before its denormalized step/aggregate publication.
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE workflow_steps SET state='running' WHERE workflow_id=?", (workflow_id,))
    _expire(store, workflow_id)
    recovered = WorkflowStore(store.path).get(workflow_id)
    assert recovered["state"] == "completed"
    assert [step["state"] for step in recovered["steps"]] == ["completed"] * 3


@pytest.mark.parametrize("blocker", ["sibling", "resource", "mutation"])
def test_queue_transition_rechecks_all_replay_blockers_atomically(tmp_path: Path, blocker: str) -> None:
    store, workflow_id, token, operations = _running(tmp_path)
    current = store.get(workflow_id)
    store.transition(workflow_id, current["version"], WorkflowState.PAUSED, lease_token=token)
    with sqlite3.connect(store.path) as connection:
        if blocker == "sibling":
            connection.execute("UPDATE workflow_operations SET state='uncertain' WHERE operation_id=?", (operations[1]["operation_id"],))
        elif blocker == "mutation":
            connection.execute("UPDATE workflow_operations SET state='failed',action='git_commit',attempts=1,dispatch_phase='settled' "
                               "WHERE operation_id=?", (operations[1]["operation_id"],))
    if blocker == "resource":
        from core.resource_claims import path_claim

        store.reserve_resource_claims(
            request_id="unresolved", owner_token="owner", claims=(path_claim(tmp_path / "owned", "write"),),
            category="test", workflow_id=workflow_id, operation_id=operations[0]["operation_id"],
        )
    paused = store.get(workflow_id)
    with pytest.raises(ToolError):
        store.transition(workflow_id, paused["version"], WorkflowState.QUEUED)
    assert store.get(workflow_id)["version"] == paused["version"]


def test_real_executor_process_death_keeps_all_effects_without_replay(tmp_path: Path) -> None:
    from core.process_ownership import spawn_owned_process

    script = '''
import sys,time,threading
from pathlib import Path
from core.workflow_actions import ACTION_HANDLERS
from core.workflow_executor import WorkflowExecutor,NATIVE_ONLY_ROUTING
from core.workflow_models import StepDefinition,WorkflowDefinition,WorkflowState
from core.workflow_store import WorkflowStore
root=Path(sys.argv[1]); store=WorkflowStore(root/'crash.sqlite3',recover=False)
steps=tuple(StepDefinition(n,'check_file',{'path':str(root/n)},step_id=n) for n in ('a','b','c'))
w=store.create(WorkflowDefinition('crash',steps,execution_mode='dag',max_parallel_steps=3),initial_state=WorkflowState.QUEUED)
lock=threading.Lock(); started=0

def action(arguments,timeout):
 global started
 name=Path(arguments['path']).name
 with lock:
  if name!='c':
   with (root/'effects').open('a') as f:f.write(name+'\\n')
  started+=1
  if started==3:
   (root/'ready.tmp').write_text(w['workflow_id'])
   (root/'ready.tmp').replace(root/'ready')
 time.sleep(60)
 return {'ok':True}

ACTION_HANDLERS['check_file']=action
WorkflowExecutor(store,route_planner=NATIVE_ONLY_ROUTING,enable_dag_execution=True).execute(w['workflow_id'],owner_id='crash')
'''
    process = spawn_owned_process(
        [sys.executable, "-c", script, str(tmp_path)], cwd=Path(__file__).resolve().parents[1],
        env=os.environ.copy(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=0, require_native=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (tmp_path / "ready").exists()
    finally:
        process.terminate_tree(force=True)
        process.wait(timeout=5)
        process.close_ownership()
    workflow_id = (tmp_path / "ready").read_text()
    path = tmp_path / "crash.sqlite3"
    store = WorkflowStore(path, recover=False)
    _expire(store, workflow_id)
    recovered = WorkflowStore(path)
    assert [op["state"] for op in recovered.list_operations(workflow_id)["items"]] == ["uncertain"] * 3
    assert sorted((tmp_path / "effects").read_text().splitlines()) == ["a", "b"]
    assert len(recovered.active_admissions(workflow_id)) == 3


@pytest.mark.parametrize("dag,action,expected_calls", [(False, "check_file", 1), (True, "check_http", 2)])
def test_descriptor_retry_policy_controls_automatic_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dag: bool, action: str, expected_calls: int,
) -> None:
    from core.workflow_actions import ACTION_HANDLERS, TransientActionError
    from core.workflow_executor import NATIVE_ONLY_ROUTING, WorkflowExecutor

    calls = []

    def flaky(arguments: dict[str, Any], timeout: float) -> dict[str, Any]:
        calls.append(timeout)
        if len(calls) == 1:
            raise TransientActionError("known pre-effect/read-only failure")
        return {"ok": True}

    monkeypatch.setitem(ACTION_HANDLERS, action, flaky)
    arguments = {"path": str(tmp_path)} if action == "check_file" else {"url": "http://localhost"}
    definition = WorkflowDefinition(
        "retry", (StepDefinition("one", action, arguments, max_retries=1, step_id="one" if dag else None),),
        execution_mode="dag" if dag else "sequential", total_timeout_sec=10 if dag else None,
    )
    store = WorkflowStore(tmp_path / "retry.sqlite3")
    workflow = store.create(definition, initial_state=WorkflowState.QUEUED)
    result = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING, enable_dag_execution=dag).execute(
        workflow["workflow_id"], owner_id="retry",
    )
    assert len(calls) == expected_calls
    assert result["state"] == ("completed" if dag else "failed")


def test_deadline_consumed_by_intent_prevents_action_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import workflow_executor
    from core.workflow_executor import NATIVE_ONLY_ROUTING, WorkflowExecutor

    dispatched = []

    def prepare(*args: Any, **kwargs: Any) -> tuple[None, None]:
        time.sleep(0.06)
        return None, None

    monkeypatch.setattr(workflow_executor, "prepare_action_intent", prepare)
    def execute(*args: Any) -> dict[str, Any]:
        dispatched.append("effect")
        return {"ok": True}

    monkeypatch.setattr(workflow_executor, "execute_action", execute)
    store = WorkflowStore(tmp_path / "deadline.sqlite3")
    workflow = store.create(WorkflowDefinition(
        "deadline", (StepDefinition("one", "check_file", {"path": str(tmp_path)}, step_id="one"),),
        execution_mode="dag", total_timeout_sec=0.04,
    ), initial_state=WorkflowState.QUEUED)
    result = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING, enable_dag_execution=True).execute(
        workflow["workflow_id"], owner_id="deadline",
    )
    assert dispatched == []
    assert result["state"] in {"failed", "paused"}


def test_file_contains_is_bounded_and_matches_across_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    from core import workflow_actions

    target = tmp_path / "text"
    target.write_text("x" * 65_535 + "محتوا" + "x", encoding="utf-8")
    assert workflow_actions.execute_action("check_file", {"path": str(target), "contains": "x" + "محتوا"}, 1)["contains_matched"] is True
    monkeypatch.setattr(workflow_actions, "SETTINGS", replace(workflow_actions.SETTINGS, max_file_write_chars=1_024))
    with pytest.raises(ToolError) as error:
        workflow_actions.execute_action("check_file", {"path": str(target), "contains": "x"}, 1)
    assert error.value.code == "workflow_file_check_limit"


def test_cancellation_fans_out_preserves_completed_results_and_blocks_join(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from core.workflow_actions import ACTION_HANDLERS, ActionCancelled, ActionContext
    from core.workflow_executor import NATIVE_ONLY_ROUTING, WorkflowExecutor

    store = WorkflowStore(tmp_path / "cancel.sqlite3")
    workflow = store.create(WorkflowDefinition(
        "cancel", (
            *(StepDefinition(n, "check_file", {"path": n}, step_id=n) for n in ("a", "b")),
            StepDefinition("join", "check_file", {"path": "join"}, step_id="join", depends_on=("a", "b")),
        ),
        execution_mode="dag", max_parallel_steps=2,
    ), initial_state=WorkflowState.QUEUED)
    both_started = threading.Barrier(3)
    calls = []

    def action(arguments: dict[str, Any], timeout: float, context: ActionContext) -> dict[str, Any]:
        name = arguments["path"]
        calls.append(name)
        both_started.wait(timeout=5)
        deadline = time.monotonic() + 5
        while not context.is_cancel_requested() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert context.is_cancel_requested()
        if name == "b":
            raise ActionCancelled("cooperative read cancelled")
        return {"result": "preserved after cancellation"}

    monkeypatch.setitem(ACTION_HANDLERS, "check_file", action)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING, enable_dag_execution=True).execute,
            workflow["workflow_id"], owner_id="cancel",
        )
        both_started.wait(timeout=5)
        current = store.get(workflow["workflow_id"])
        started = time.monotonic()
        store.request_cancel(workflow["workflow_id"], current["version"], reason="operator")
        result = future.result(timeout=5)
    assert time.monotonic() - started < 5
    assert result["state"] == "cancelled"
    assert set(calls) == {"a", "b"}
    assert [op["state"] for op in store.list_operations(workflow["workflow_id"])["items"]] == ["succeeded", "cancelled", "created"]
    assert store.active_admissions(workflow["workflow_id"]) == []


def test_last_resource_resolution_repairs_aggregate_after_operation_acknowledgement(tmp_path: Path) -> None:
    from core.resource_claims import path_claim

    store, workflow_id, token, operations = _running(tmp_path)
    store.reserve_resource_claims(
        request_id="effect", owner_token="owner", claims=(path_claim(tmp_path / "owned", "write"),),
        category="test", workflow_id=workflow_id, operation_id=operations[0]["operation_id"],
    )
    store.quarantine_resource_request("effect", owner_token="owner", reason="cleanup unknown")
    for index, operation in enumerate(operations):
        store.checkpoint_operation(operation["operation_id"], OperationState.RUNNING, lease_token=token)
        store.checkpoint_operation(
            operation["operation_id"], OperationState.UNCERTAIN if index == 0 else OperationState.SUCCEEDED, lease_token=token,
        )
    current = store.get(workflow_id)
    store.transition(workflow_id, current["version"], WorkflowState.UNCERTAIN, lease_token=token)
    operation = store.get_operation(operations[0]["operation_id"])
    resolved = store.resolve_uncertain_operation(
        operation["operation_id"], operation["version"], OperationState.ACKNOWLEDGED,
        evidence={"source": "operator_assertion", "conclusive": True, "satisfied": True}, event_type="test_ack",
    )
    assert resolved["workflow"]["state"] == "uncertain"
    store.resolve_quarantined_resource_request(
        "effect", owner_token="owner", resolution="released", evidence="owned child cleanup independently confirmed",
    )
    assert store.get(workflow_id)["state"] == "completed"


def test_parallel_exception_payload_does_not_cross_persistent_evidence_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.workflow_actions import ACTION_HANDLERS
    from core.workflow_executor import NATIVE_ONLY_ROUTING, WorkflowExecutor

    canary = "private-exception-canary-18364"

    def failed(*args: Any) -> dict[str, Any]:
        raise RuntimeError(canary)

    monkeypatch.setitem(ACTION_HANDLERS, "check_file", failed)
    store = WorkflowStore(tmp_path / "secrets.sqlite3")
    workflow = store.create(WorkflowDefinition(
        "private evidence", (StepDefinition("one", "check_file", {"path": "fixture"}, step_id="one"),),
        execution_mode="dag",
    ), initial_state=WorkflowState.QUEUED)
    result = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING, enable_dag_execution=True).execute(
        workflow["workflow_id"], owner_id="private",
    )
    with sqlite3.connect(store.path) as connection:
        snapshot = "\n".join(connection.iterdump())
    assert canary not in snapshot
    assert canary not in str(result)


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("failed_sibling", [False, True])
def test_concurrent_recovery_and_resolution_preserve_mixed_branch_truth(
    tmp_path: Path, cancel: bool, failed_sibling: bool,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    store, workflow_id, token, operations = _running(tmp_path)
    for index, operation in enumerate(operations):
        current = _attempt(store, operation["operation_id"], token, "may_dispatch")
        if index < 2:
            store.complete_operation_attempt(
                current["operation_id"], expected_version=current["version"], attempt_token=current["attempt_token"],
                lease_token=token, final_state=OperationState.FAILED if index == 1 and failed_sibling else OperationState.SUCCEEDED,
            )
    if cancel:
        current = store.get(workflow_id)
        store.request_cancel(workflow_id, current["version"], reason="persisted cancellation")
    _expire(store, workflow_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: WorkflowStore(store.path), range(2)))
    recovered = store.list_operations(workflow_id)["items"]
    assert [op["state"] for op in recovered] == ["succeeded", "failed" if failed_sibling else "succeeded", "uncertain"]
    assert store.get(workflow_id)["state"] == "uncertain"
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_events WHERE workflow_id=? AND event_type='workflow_restart_recovered'", (workflow_id,),
        ).fetchone()[0] == 1
    final = store.resolve_uncertain_operation(
        recovered[2]["operation_id"], recovered[2]["version"], OperationState.ACKNOWLEDGED,
        evidence={"source": "operator_assertion", "conclusive": True, "satisfied": True}, event_type="test_ack",
    )
    assert final["workflow"]["state"] == ("failed" if failed_sibling else "cancelled" if cancel else "completed")
