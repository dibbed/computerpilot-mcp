from __future__ import annotations

from pathlib import Path

import pytest

from core.workflow_actions import ACTION_HANDLERS
from core.workflow_models import OperationState
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowExecutor, WorkflowState, WorkflowStore


def test_executor_checkpoints_operation_before_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    seen: list[str] = []

    def inspect(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        del arguments, timeout
        seen.append(store.list_operations(workflow["workflow_id"])["items"][0]["state"])
        return {"ok": True}

    monkeypatch.setitem(ACTION_HANDLERS, "check_file", inspect)
    workflow = store.create(
        WorkflowDefinition("execute", (StepDefinition("check", "check_file", {"path": "unused"}),)),
        initial_state=WorkflowState.QUEUED,
    )

    result = WorkflowExecutor(store).execute(workflow["workflow_id"], owner_id="test-owner")

    assert seen == ["running"]
    assert result["state"] == "completed"
    assert store.list_operations(workflow["workflow_id"])["items"][0]["state"] == "succeeded"


def _expire_lease(path: Path, workflow_id: str) -> None:
    import sqlite3

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE workflow_leases SET expires_at = '2000-01-01T00:00:00.000+00:00' WHERE workflow_id = ?",
            (workflow_id,),
        )


def _running_checkpoint(store: WorkflowStore) -> tuple[dict[str, object], str, str]:
    workflow = store.create(
        WorkflowDefinition("crash-window", (StepDefinition("check", "check_file", {"path": "unused"}),)),
        initial_state=WorkflowState.QUEUED,
    )
    workflow_id = str(workflow["workflow_id"])
    lease = store.acquire_lease(workflow_id, "worker-a", 30)
    running = store.transition(
        workflow_id,
        int(workflow["version"]),
        WorkflowState.RUNNING,
        lease_token=lease.lease_token,
    )
    operation_id = str(store.list_operations(workflow_id)["items"][0]["operation_id"])
    store.checkpoint_operation(
        operation_id,
        OperationState.RUNNING,
        lease_token=lease.lease_token,
        increment_attempt=True,
    )
    store.checkpoint_step(
        workflow_id,
        0,
        "running",
        increment_attempt=True,
        lease_token=lease.lease_token,
    )
    assert running["state"] == "running"
    return workflow, operation_id, lease.lease_token


def test_restart_finishes_aggregate_when_operation_result_was_persisted(tmp_path: Path) -> None:
    path = tmp_path / "workflows.db"
    store = WorkflowStore(path)
    workflow, operation_id, token = _running_checkpoint(store)
    workflow_id = str(workflow["workflow_id"])

    store.checkpoint_operation(
        operation_id,
        OperationState.SUCCEEDED,
        lease_token=token,
        result={"ok": True},
        evidence={"source": "action_result", "conclusive": True, "satisfied": True},
    )
    _expire_lease(path, workflow_id)

    reopened = WorkflowStore(path)
    current = reopened.get(workflow_id)

    assert current["state"] == "completed"
    assert current["current_step"] == 1
    assert current["steps"][0]["state"] == "completed"
    assert reopened.list_operations(workflow_id)["items"][0]["state"] == "succeeded"


def test_restart_finishes_aggregate_when_step_checkpoint_preceded_crash(tmp_path: Path) -> None:
    path = tmp_path / "workflows.db"
    store = WorkflowStore(path)
    workflow, operation_id, token = _running_checkpoint(store)
    workflow_id = str(workflow["workflow_id"])

    store.checkpoint_operation(
        operation_id,
        OperationState.SUCCEEDED,
        lease_token=token,
        result={"ok": True},
        evidence={"source": "action_result", "conclusive": True, "satisfied": True},
    )
    store.checkpoint_step(
        workflow_id,
        0,
        "completed",
        evidence={"postcondition": {"conclusive": True, "satisfied": True}},
        lease_token=token,
    )
    _expire_lease(path, workflow_id)

    current = WorkflowStore(path).get(workflow_id)

    assert current["state"] == "completed"
    assert current["current_step"] == 1
    assert current["steps"][0]["state"] == "completed"


def test_restart_preserves_failed_operation_truth_before_step_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "workflows.db"
    store = WorkflowStore(path)
    workflow, operation_id, token = _running_checkpoint(store)
    workflow_id = str(workflow["workflow_id"])

    store.checkpoint_operation(
        operation_id,
        OperationState.FAILED,
        lease_token=token,
        error="deterministic failure",
    )
    _expire_lease(path, workflow_id)

    reopened = WorkflowStore(path)
    current = reopened.get(workflow_id)

    assert current["state"] == "failed"
    assert current["steps"][0]["state"] == "failed"
    assert reopened.list_operations(workflow_id)["items"][0]["state"] == "failed"


def test_restart_during_cancellation_preserves_completed_effect_then_cancels(tmp_path: Path) -> None:
    path = tmp_path / "workflows.db"
    store = WorkflowStore(path)
    workflow, operation_id, token = _running_checkpoint(store)
    workflow_id = str(workflow["workflow_id"])
    running = store.get(workflow_id)
    cancelling = store.request_cancel(workflow_id, int(running["version"]), reason="operator")
    assert cancelling["state"] == "cancelling"

    store.checkpoint_operation(
        operation_id,
        OperationState.SUCCEEDED,
        lease_token=token,
        result={"ok": True},
        evidence={"source": "action_result", "conclusive": True, "satisfied": True},
    )
    _expire_lease(path, workflow_id)

    current = WorkflowStore(path).get(workflow_id)

    assert current["state"] == "cancelled"
    assert current["steps"][0]["state"] == "completed"
    assert current["current_step"] == 1


def test_completed_operation_is_not_replayed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    calls = 0

    def count(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        nonlocal calls
        del arguments, timeout
        calls += 1
        return {"ok": True}

    monkeypatch.setitem(ACTION_HANDLERS, "check_file", count)
    workflow = store.create(
        WorkflowDefinition("once", (StepDefinition("check", "check_file", {"path": "unused"}),)),
        initial_state=WorkflowState.QUEUED,
    )
    executor = WorkflowExecutor(store)
    executor.execute(workflow["workflow_id"], owner_id="test-owner")

    result = executor.execute(workflow["workflow_id"], owner_id="test-owner")

    assert result["state"] == "completed"
    assert calls == 1
