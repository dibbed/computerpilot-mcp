from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

import core.workflow_executor as executor_module
from core.errors import ToolError
from core.workflow_actions import ACTION_HANDLERS, TransientActionError
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowExecutor, WorkflowState, WorkflowStore


def _workflow(store: WorkflowStore) -> dict[str, object]:
    return store.create(
        WorkflowDefinition("lease-heartbeat", (StepDefinition("check", "check_file", {"path": "unused"}),)),
        initial_state=WorkflowState.QUEUED,
    )


def test_lease_heartbeat_interval_is_bounded() -> None:
    assert executor_module._lease_heartbeat_interval(0.3) == 1.0
    assert executor_module._lease_heartbeat_interval(9) == 3.0
    assert executor_module._lease_heartbeat_interval(60) == 10.0


def test_executor_renews_lease_while_action_is_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = _workflow(store)
    renewals = 0
    original_renew = store.renew_lease

    def tracked_renew(workflow_id: str, lease_token: str, ttl_sec: float):  # type: ignore[no-untyped-def]
        nonlocal renewals
        renewals += 1
        return original_renew(workflow_id, lease_token, ttl_sec)

    def slow_action(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        del arguments, timeout
        time.sleep(0.16)
        return {"ok": True}

    monkeypatch.setattr(store, "renew_lease", tracked_renew)
    monkeypatch.setattr(executor_module, "LEASE_HEARTBEAT_MIN_SEC", 0.02)
    monkeypatch.setattr(executor_module, "LEASE_HEARTBEAT_MAX_SEC", 0.04)
    monkeypatch.setitem(ACTION_HANDLERS, "check_file", slow_action)

    result = WorkflowExecutor(store).execute(
        str(workflow["workflow_id"]),
        owner_id="heartbeat-owner",
        lease_ttl_sec=5,
    )

    assert result["state"] == "completed"
    assert renewals >= 1


def test_retry_backoff_does_not_expire_short_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition(
            "retry-lease",
            (StepDefinition("http", "check_http", {"url": "http://localhost"}, max_retries=4),),
        ),
        initial_state=WorkflowState.QUEUED,
    )
    calls = 0
    renewals = 0
    original_renew = store.renew_lease

    def tracked_renew(workflow_id: str, lease_token: str, ttl_sec: float):  # type: ignore[no-untyped-def]
        nonlocal renewals
        renewals += 1
        return original_renew(workflow_id, lease_token, ttl_sec)

    def flaky(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls <= 4:
            raise TransientActionError("temporary network failure")
        return {"ok": True}

    monkeypatch.setitem(ACTION_HANDLERS, "check_http", flaky)
    monkeypatch.setattr(store, "renew_lease", tracked_renew)
    monkeypatch.setattr(executor_module, "LEASE_HEARTBEAT_MIN_SEC", 0.01)
    monkeypatch.setattr(executor_module, "LEASE_HEARTBEAT_MAX_SEC", 0.02)
    monkeypatch.setattr(executor_module, "_retry_backoff_seconds", lambda attempt: 0.08)
    result = WorkflowExecutor(store).execute(
        workflow["workflow_id"],
        owner_id="retry-owner",
        lease_ttl_sec=5,
    )

    assert result["state"] == "completed"
    assert calls == 5
    assert renewals > 5
    assert result["steps"][0]["attempts"] == 5


def test_stale_release_does_not_delete_replacement_lease(tmp_path: Path) -> None:
    path = tmp_path / "workflows.db"
    store = WorkflowStore(path)
    workflow = _workflow(store)
    workflow_id = str(workflow["workflow_id"])
    stale = store.acquire_lease(workflow_id, "owner-a", 30)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE workflow_leases SET expires_at = '2000-01-01T00:00:00.000+00:00' WHERE workflow_id = ?",
            (workflow_id,),
        )
    replacement = store.acquire_lease(workflow_id, "owner-b", 30)

    assert store.release_lease_if_current(workflow_id, stale.lease_token) is False
    assert store.require_lease(workflow_id, replacement.lease_token) == replacement


def test_executor_reports_lease_loss_without_releasing_new_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "workflows.db"
    store = WorkflowStore(path)
    workflow = _workflow(store)
    workflow_id = str(workflow["workflow_id"])
    replacement_token: list[str] = []

    def steal_lease(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        del arguments, timeout
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE workflow_leases SET expires_at = '2000-01-01T00:00:00.000+00:00' WHERE workflow_id = ?",
                (workflow_id,),
            )
        replacement = store.acquire_lease(workflow_id, "owner-b", 30)
        replacement_token.append(replacement.lease_token)
        return {"ok": True}

    monkeypatch.setitem(ACTION_HANDLERS, "check_file", steal_lease)

    with pytest.raises(ToolError) as raised:
        WorkflowExecutor(store).execute(workflow_id, owner_id="owner-a", lease_ttl_sec=30)

    assert raised.value.code == "workflow_lease_lost"
    assert replacement_token
    assert store.require_lease(workflow_id, replacement_token[0]).owner_id == "owner-b"
