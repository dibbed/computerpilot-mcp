from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core.errors import ToolError
from core.workflow_models import WorkflowLease
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowState, WorkflowStore


def _workflow(store: WorkflowStore) -> dict[str, object]:
    return store.create(WorkflowDefinition("lease", (StepDefinition("check", "check_file"),)))


def test_only_one_owner_can_acquire_a_live_lease(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow_id = str(_workflow(store)["workflow_id"])

    lease = store.acquire_lease(workflow_id, "worker-a", 30)

    assert isinstance(lease, WorkflowLease)
    assert lease.owner_id == "worker-a"
    with pytest.raises(ToolError, match="already claimed"):
        store.acquire_lease(workflow_id, "worker-b", 30)
    assert store.acquire_lease(workflow_id, "worker-a", 30) == lease


def test_concurrent_store_instances_serialize_lease_acquisition(tmp_path: Path) -> None:
    path = tmp_path / "workflows.db"
    first_store = WorkflowStore(path)
    workflow_id = str(_workflow(first_store)["workflow_id"])
    second_store = WorkflowStore(path)
    barrier = threading.Barrier(2)

    def acquire(store: WorkflowStore, owner: str) -> str:
        barrier.wait(timeout=5)
        try:
            return store.acquire_lease(workflow_id, owner, 30).owner_id
        except ToolError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: acquire(*item),
                ((first_store, "worker-a"), (second_store, "worker-b")),
            )
        )

    assert sorted(results) in (
        ["worker-a", "workflow_already_claimed"],
        ["worker-b", "workflow_already_claimed"],
    )


def test_renew_requires_exact_current_token(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow_id = str(_workflow(store)["workflow_id"])
    lease = store.acquire_lease(workflow_id, "worker-a", 30)

    with pytest.raises(ToolError, match="token"):
        store.renew_lease(workflow_id, "stale-token", 30)

    renewed = store.renew_lease(workflow_id, lease.lease_token, 60)
    assert renewed.lease_token == lease.lease_token
    assert renewed.version == lease.version + 1
    assert renewed.heartbeat_at >= lease.heartbeat_at


def test_stale_owner_cannot_require_or_release_another_lease(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow_id = str(_workflow(store)["workflow_id"])
    lease = store.acquire_lease(workflow_id, "worker-a", 30)

    with pytest.raises(ToolError, match="token"):
        store.require_lease(workflow_id, "stale-token")
    with pytest.raises(ToolError, match="token"):
        store.release_lease(workflow_id, "stale-token")
    with pytest.raises(ToolError, match="token"):
        store.checkpoint_step(workflow_id, 0, "running", lease_token="stale-token")

    assert store.require_lease(workflow_id, lease.lease_token) == lease
    store.checkpoint_step(workflow_id, 0, "running", lease_token=lease.lease_token)


def test_expired_lease_can_be_replaced(tmp_path: Path) -> None:
    path = tmp_path / "workflows.db"
    store = WorkflowStore(path)
    workflow_id = str(_workflow(store)["workflow_id"])
    stale = store.acquire_lease(workflow_id, "worker-a", 30)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE workflow_leases SET expires_at = '2000-01-01T00:00:00.000+00:00' WHERE workflow_id = ?",
            (workflow_id,),
        )

    replacement = store.acquire_lease(workflow_id, "worker-b", 30)

    assert replacement.owner_id == "worker-b"
    assert replacement.lease_token != stale.lease_token
    with pytest.raises(ToolError, match="token"):
        store.require_lease(workflow_id, stale.lease_token)


def test_reopen_does_not_recover_running_workflow_with_live_lease(tmp_path: Path) -> None:
    path = tmp_path / "workflows.db"
    store = WorkflowStore(path)
    workflow = store.create(_workflow_definition(), initial_state=WorkflowState.QUEUED)
    running = store.transition(workflow["workflow_id"], workflow["version"], WorkflowState.RUNNING)
    store.checkpoint_step(workflow["workflow_id"], 0, "running", increment_attempt=True)
    store.acquire_lease(workflow["workflow_id"], "worker-a", 30)

    reopened = WorkflowStore(path).get(workflow["workflow_id"])

    assert running["state"] == "running"
    assert reopened["state"] == "running"
    assert reopened["steps"][0]["state"] == "running"


def test_reopen_recovers_running_workflow_after_lease_expiry(tmp_path: Path) -> None:
    path = tmp_path / "workflows.db"
    store = WorkflowStore(path)
    workflow = store.create(_workflow_definition(), initial_state=WorkflowState.QUEUED)
    running = store.transition(workflow["workflow_id"], workflow["version"], WorkflowState.RUNNING)
    store.checkpoint_step(workflow["workflow_id"], 0, "running", increment_attempt=True)
    store.acquire_lease(workflow["workflow_id"], "worker-a", 30)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE workflow_leases SET expires_at = '2000-01-01T00:00:00.000+00:00' WHERE workflow_id = ?",
            (workflow["workflow_id"],),
        )

    reopened = WorkflowStore(path).get(workflow["workflow_id"])

    assert running["state"] == "running"
    assert reopened["state"] == "uncertain"
    assert reopened["steps"][0]["state"] == "uncertain"
    assert store.list_operations(workflow["workflow_id"])["items"][0]["state"] == "uncertain"


def test_release_is_idempotent_for_current_token(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow_id = str(_workflow(store)["workflow_id"])
    lease = store.acquire_lease(workflow_id, "worker-a", 30)

    store.release_lease(workflow_id, lease.lease_token)
    store.release_lease(workflow_id, lease.lease_token)

    with pytest.raises(ToolError, match="active lease"):
        store.require_lease(workflow_id, lease.lease_token)


@pytest.mark.parametrize("ttl_sec", [0, 4.99, 300.01, 3_600])
def test_lease_ttl_is_bounded(tmp_path: Path, ttl_sec: float) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow_id = str(_workflow(store)["workflow_id"])

    with pytest.raises(ToolError, match="between 5 and 300"):
        store.acquire_lease(workflow_id, "worker-a", ttl_sec)


def _workflow_definition() -> WorkflowDefinition:
    return WorkflowDefinition("lease", (StepDefinition("check", "check_file"),))
