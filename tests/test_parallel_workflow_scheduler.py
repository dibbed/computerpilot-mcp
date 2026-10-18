from __future__ import annotations

import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from core import workflow_executor
from core.errors import ToolError
from core.workflow_executor import NATIVE_ONLY_ROUTING, WorkflowExecutor
from core.workflow_models import OperationState, StepDefinition, WorkflowDefinition, WorkflowState
from core.workflow_store import WorkflowStore


def _dag(cap: int = 2) -> WorkflowDefinition:
    return WorkflowDefinition(
        "parallel",
        (
            StepDefinition("root-a", "check_file", {"path": "a"}, step_id="a"),
            StepDefinition("root-b", "check_file", {"path": "b"}, step_id="b"),
            StepDefinition("join", "check_file", {"path": "join"}, step_id="join", depends_on=("a", "b")),
        ),
        execution_mode="dag",
        max_parallel_steps=cap,
        total_timeout_sec=30,
    )


def _single_dag(name: str, *, cap: int = 2) -> WorkflowDefinition:
    return WorkflowDefinition(
        name,
        (StepDefinition("root", "check_file", {"path": name}, step_id="root"),),
        execution_mode="dag",
        max_parallel_steps=cap,
        total_timeout_sec=30,
    )


def _patch_safe_actions(
    monkeypatch: pytest.MonkeyPatch, *, fail_step: str | None = None, hold_until_two: bool = False,
) -> dict[str, Any]:
    barrier = threading.Barrier(2, timeout=10) if hold_until_two else None
    lock = threading.Lock()
    active = 0
    peak = 0
    started: list[str] = []
    finished: list[str] = []

    monkeypatch.setattr(
        workflow_executor,
        "prepare_action_intent",
        lambda action, arguments, postcondition, **kwargs: (postcondition, {"prepared": True}),
    )

    def fake_execute(action: str, arguments: dict[str, object], timeout_sec: float, context: object) -> dict[str, object]:
        nonlocal active, peak
        name = str(arguments["path"])
        with lock:
            active += 1
            peak = max(peak, active)
            started.append(name)
        try:
            if name in {"a", "b"}:
                if barrier is not None:
                    barrier.wait()
                else:
                    time.sleep(0.05)
            if name == fail_step:
                raise ToolError("simulated_failure", "simulated")
            return {"name": name}
        finally:
            with lock:
                active -= 1
                finished.append(name)

    monkeypatch.setattr(workflow_executor, "execute_action", fake_execute)
    return {
        "peak": lambda: peak,
        "started": started,
        "finished": finished,
    }


def _running_workflow(store: WorkflowStore, definition: WorkflowDefinition, owner: str) -> tuple[dict[str, Any], str]:
    created = store.create(definition, initial_state=WorkflowState.QUEUED)
    workflow_id = str(created["workflow_id"])
    store.materialize_operations(workflow_id, return_operations=False)
    lease = store.acquire_lease(workflow_id, owner, 30, allow_reentry=False)
    store.transition(
        workflow_id,
        int(created["version"]),
        WorkflowState.RUNNING,
        lease_token=lease.lease_token,
    )
    return store.get(workflow_id), lease.lease_token


def test_invalid_pre_dispatch_resource_footprint_fails_workflow_without_abandoned_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The pytest temp root is inside this checkout; prohibit Git parent discovery.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    invalid_repo = tmp_path / "not-a-git-repo"
    invalid_repo.mkdir()
    created = store.create(
        WorkflowDefinition(
            "invalid-footprint",
            (StepDefinition("git-read", "git_status", {"repo": str(invalid_repo)}, step_id="git-read"),),
            execution_mode="dag",
        ),
        initial_state=WorkflowState.QUEUED,
    )
    executor = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING, enable_dag_execution=True)
    result = executor.execute(created["workflow_id"], owner_id="worker")
    assert result["state"] == "failed"
    operation = store.list_operations(created["workflow_id"])["items"][0]
    assert operation["state"] == "failed"
    assert store.health_summary()["active_workflow_leases"] == 0


def test_preflight_denied_mutation_has_replay_safe_resume_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    repo = tmp_path / "new-repository"
    repo.mkdir()
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    created = store.create(
        WorkflowDefinition(
            "safe-preflight",
            (StepDefinition("stage", "git_stage", {"repo": str(repo), "paths": ["a.txt"]},
                            postcondition={"kind": "git_stage_intent"}, step_id="stage"),),
            execution_mode="dag",
        ),
        initial_state=WorkflowState.QUEUED,
    )
    executor = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING, enable_dag_execution=True)
    failed = executor.execute(created["workflow_id"], owner_id="worker")
    assert failed["state"] == "failed"
    operation = store.list_operations(created["workflow_id"])["items"][0]
    assert operation["state"] == "failed"
    assert operation["evidence"]["dispatch_phase"] == "preflight_rejected"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "a.txt").write_text("safe", encoding="utf-8")
    queued = store.transition(failed["workflow_id"], failed["version"], WorkflowState.QUEUED)
    assert queued["state"] == "queued"
    done = executor.execute(queued["workflow_id"], owner_id="worker")
    assert done["state"] == "completed"


def test_unchanged_running_aggregate_does_not_emit_polling_history(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    current, lease = _running_workflow(store, _single_dag("waiting"), "owner")
    with sqlite3.connect(store.path) as db:
        before = db.execute(
            "SELECT COUNT(*) FROM workflow_events WHERE workflow_id=?",
            (current["workflow_id"],),
        ).fetchone()[0]
    for _ in range(6):
        updated = store.recompute_workflow_aggregate(
            current["workflow_id"], lease_token=lease, keep_running=True,
        )
        assert updated["state"] == "running"
    with sqlite3.connect(store.path) as db:
        after = db.execute(
            "SELECT COUNT(*) FROM workflow_events WHERE workflow_id=?",
            (current["workflow_id"],),
        ).fetchone()[0]
    assert after == before
    assert updated["version"] == current["version"]


def test_resource_storage_denial_rolls_back_all_claims_and_prevents_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    workflow = store.create(_dag(), initial_state=WorkflowState.QUEUED)
    dispatched: list[object] = []
    original_connect = store._connect

    def connect(*, setup_wal: bool = False) -> sqlite3.Connection:
        db = original_connect(setup_wal=setup_wal)
        db.set_authorizer(
            lambda action, table, column, database, trigger:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and table == "workflow_resource_claims" else sqlite3.SQLITE_OK,
        )
        return db

    monkeypatch.setattr(store, "_connect", connect)
    monkeypatch.setattr(workflow_executor, "execute_action", lambda *args, **kwargs: dispatched.append("action"))
    monkeypatch.setattr(workflow_executor, "prepare_action_intent", lambda *args, **kwargs: dispatched.append("intent"))
    with pytest.raises(sqlite3.DatabaseError):
        WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING, enable_dag_execution=True).execute(
            workflow["workflow_id"], owner_id="worker",
        )
    assert dispatched == []
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM workflow_resource_requests").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM workflow_resource_claims").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM workflow_execution_admissions").fetchone()[0] == 0


def test_dag_scheduler_respects_cap_and_join_dependencies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _patch_safe_actions(monkeypatch, hold_until_two=True)
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    created = store.create(_dag(2), initial_state=WorkflowState.QUEUED)
    result = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING, enable_dag_execution=True).execute(
        created["workflow_id"], owner_id="worker", lease_ttl_sec=10
    )
    assert result["state"] == "completed"
    assert tracker["peak"]() == 2
    started = tracker["started"]
    assert set(started[:2]) == {"a", "b"}
    assert started[-1] == "join"


@pytest.mark.parametrize("cleanup_denied", [False, True])
def test_dag_does_not_checkpoint_success_with_unresolved_analyzer_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup_denied: bool,
) -> None:
    from core.runtime_capacity import RuntimeCapacityPermit, acquire_runtime_capacity

    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    permits: list[RuntimeCapacityPermit] = []

    def execute(*args: object, **kwargs: object) -> dict[str, object]:
        permit = acquire_runtime_capacity(store.path, category="analyzer", limit=1, deadline=time.monotonic() + 1)
        assert permit is not None
        permits.append(permit)
        if cleanup_denied:
            with pytest.raises(ToolError), permit:
                raise ToolError("process_cleanup_incomplete", "child cleanup denied")
        return {"handled": "result available, ownership unresolved"}

    monkeypatch.setattr(workflow_executor, "execute_action", execute)
    created = store.create(_single_dag("owner"), initial_state=WorkflowState.QUEUED)
    try:
        result = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING, enable_dag_execution=True).execute(
            created["workflow_id"], owner_id="worker",
        )
        assert result["state"] == "uncertain"
        assert store.list_operations(created["workflow_id"])["items"][0]["state"] == "uncertain"
        assert store.health_summary()["quarantined_resource_request_count"] == 1
    finally:
        for permit in permits:
            permit.release()


def test_dag_scheduler_cap_one_remains_serial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _patch_safe_actions(monkeypatch)
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    created = store.create(_dag(1), initial_state=WorkflowState.QUEUED)
    result = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING, enable_dag_execution=True).execute(
        created["workflow_id"], owner_id="worker", lease_ttl_sec=10
    )
    assert result["state"] == "completed"
    assert tracker["peak"]() == 1
    assert tracker["started"] == ["a", "b", "join"]


def test_executor_lease_disallows_same_owner_reentry(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    created = store.create(
        WorkflowDefinition("one", (StepDefinition("x", "check_file", {"path": "x"}),)),
        initial_state=WorkflowState.QUEUED,
    )
    first = store.acquire_lease(created["workflow_id"], "same-owner", 30, allow_reentry=False)
    with pytest.raises(ToolError) as raised:
        store.acquire_lease(created["workflow_id"], "same-owner", 30, allow_reentry=False)
    assert raised.value.code == "workflow_already_claimed"
    store.release_lease(created["workflow_id"], first.lease_token)


def test_dag_failure_stops_new_descendant_admission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _patch_safe_actions(monkeypatch, fail_step="a")
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    created = store.create(_dag(2), initial_state=WorkflowState.QUEUED)
    result = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING, enable_dag_execution=True).execute(
        created["workflow_id"], owner_id="worker", lease_ttl_sec=10
    )
    assert result["state"] == "failed"
    assert "join" not in tracker["started"]
    status = store.get(created["workflow_id"])
    assert status["steps"][2]["readiness"] == "blocked_dependencies"


def test_global_admission_limit_is_atomic_across_workflows(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    first, first_lease = _running_workflow(store, _single_dag("first"), "owner-1")
    second, second_lease = _running_workflow(store, _single_dag("second"), "owner-2")
    first_op = store.list_operations(first["workflow_id"])["items"][0]
    second_op = store.list_operations(second["workflow_id"])["items"][0]

    claimed_first = store.claim_ready_operation(
        first_op["operation_id"],
        lease_token=first_lease,
        owner_id="owner-1",
        execution_id="exec-1",
        global_limit=1,
    )
    assert claimed_first is not None
    assert store.get(first["workflow_id"])["active_admission_count"] == 1
    assert store.get(first["workflow_id"])["active_step_indices"] == [0]
    assert store.claim_ready_operation(
        second_op["operation_id"],
        lease_token=second_lease,
        owner_id="owner-2",
        execution_id="exec-2",
        global_limit=1,
    ) is None

    store.complete_operation_attempt(
        first_op["operation_id"],
        expected_version=int(claimed_first["version"]),
        attempt_token=str(claimed_first["attempt_token"]),
        lease_token=first_lease,
        final_state=OperationState.CANCELLED,
        error="test cleanup",
    )
    claimed_second = store.claim_ready_operation(
        second_op["operation_id"],
        lease_token=second_lease,
        owner_id="owner-2",
        execution_id="exec-2",
        global_limit=1,
    )
    assert claimed_second is not None


def test_late_attempt_result_cannot_overwrite_settled_operation(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    current, lease_token = _running_workflow(store, _single_dag("late"), "owner")
    operation = store.list_operations(current["workflow_id"])["items"][0]
    reserved = store.claim_ready_operation(
        operation["operation_id"],
        lease_token=lease_token,
        owner_id="owner",
        execution_id="exec",
        global_limit=2,
    )
    assert reserved is not None
    prepared = store.prepare_operation_attempt(
        operation["operation_id"],
        expected_version=int(reserved["version"]),
        attempt_token=str(reserved["attempt_token"]),
        lease_token=lease_token,
        intent_evidence=None,
        postcondition=None,
    )
    running = store.mark_operation_may_dispatch(
        operation["operation_id"],
        expected_version=int(prepared["version"]),
        attempt_token=str(reserved["attempt_token"]),
        lease_token=lease_token,
    )
    settled = store.complete_operation_attempt(
        operation["operation_id"],
        expected_version=int(running["version"]),
        attempt_token=str(reserved["attempt_token"]),
        lease_token=lease_token,
        final_state=OperationState.SUCCEEDED,
        result={"ok": True},
        evidence={"conclusive": True, "satisfied": True},
    )
    assert settled["state"] == "succeeded"
    with pytest.raises(ToolError) as raised:
        store.complete_operation_attempt(
            operation["operation_id"],
            expected_version=int(running["version"]),
            attempt_token=str(reserved["attempt_token"]),
            lease_token=lease_token,
            final_state=OperationState.FAILED,
            error="late",
        )
    assert raised.value.code == "workflow_attempt_conflict"
    assert store.get_operation(operation["operation_id"])["state"] == "succeeded"
