from __future__ import annotations

import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import closing
from pathlib import Path

import pytest

from core.errors import ToolError
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowState, WorkflowStore


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition("verify", (StepDefinition("test", "verify_changes", {"cwd": "C:/repo"}),))


def test_reused_connection_serializes_sql_without_serializing_claimed_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.resource_claims import managed_resource_claims, path_claim

    store = WorkflowStore(tmp_path / "workflows.db", recover=False, reuse_connection=True)
    # Count connections created by this store only. Instrumenting sqlite3.connect
    # globally also counts independent background stores from other tests.
    original_new_connection = store._new_connection
    connections: list[sqlite3.Connection] = []

    def connect(*, setup_wal: bool = False, shared: bool = False) -> sqlite3.Connection:
        connection = original_new_connection(setup_wal=setup_wal, shared=shared)
        connections.append(connection)
        return connection

    monkeypatch.setattr(store, "_new_connection", connect)
    barrier = threading.Barrier(4)

    def work() -> None:
        with managed_resource_claims((path_claim(tmp_path / "target", "read"),), category="test", store=store):
            barrier.wait(timeout=5)

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: work(), range(4)))
        assert len(connections) == 1
        with closing(store._connect()) as connection:
            assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert store.health_summary()["active_resource_request_count"] == 0
    finally:
        store.close()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    with pytest.raises(ToolError) as error:
        store.health_summary()
    assert error.value.code == "workflow_store_closed"


def test_reused_connection_rolls_back_abandoned_transaction_before_reuse(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db", recover=False, reuse_connection=True)
    try:
        with pytest.raises(RuntimeError, match="interrupted"), closing(store._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO workflow_maintenance(key) VALUES ('uncommitted')")
            raise RuntimeError("interrupted")
        with closing(store._connect()) as connection:
            assert not connection.in_transaction
            assert connection.execute("SELECT COUNT(*) FROM workflow_maintenance WHERE key='uncommitted'").fetchone()[0] == 0
        store.create(_definition())
    finally:
        store.close()


def test_reused_connection_close_waits_for_lease_and_rejects_wrong_thread(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db", recover=False, reuse_connection=True)
    connection = store._connect()
    started = threading.Event()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(ToolError) as mismatch:
                pool.submit(connection.close).result(timeout=5)
            assert mismatch.value.code == "workflow_connection_owner_mismatch"

            def close() -> None:
                started.set()
                store.close()

            pending = pool.submit(close)
            assert started.wait(5)
            with pytest.raises(FutureTimeoutError):
                pending.result(timeout=0.05)
            connection.close()
            pending.result(timeout=5)
        with pytest.raises(ToolError) as closed:
            connection.execute("SELECT 1")
        assert closed.value.code == "workflow_connection_closed"
    finally:
        connection.close()
        store.close()


def test_reused_connection_discards_handle_when_rollback_fails(tmp_path: Path) -> None:
    class BrokenRollback(sqlite3.Connection):
        def rollback(self) -> None:
            raise sqlite3.OperationalError("injected rollback failure")

    store = WorkflowStore(tmp_path / "workflows.db", recover=False, reuse_connection=True)
    broken = sqlite3.connect(store.path, isolation_level=None, check_same_thread=False, factory=BrokenRollback)
    store._shared_connection = broken
    try:
        with pytest.raises(sqlite3.OperationalError, match="injected"), closing(store._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO workflow_maintenance(key) VALUES ('uncommitted')")
        assert store._shared_connection is None
        with closing(store._connect()) as connection:
            assert connection.execute("SELECT COUNT(*) FROM workflow_maintenance WHERE key='uncommitted'").fetchone()[0] == 0
        assert store.health_summary()["active_resource_request_count"] == 0
    finally:
        store.close()


def test_create_is_idempotent_for_exact_inputs_and_redacts_public_projection(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    inputs = {"api_token": "secret", "branch": "main"}
    first = store.create(_definition(), inputs, idempotency_key="same")
    second = store.create(_definition(), inputs, idempotency_key="same")
    assert first["workflow_id"] == second["workflow_id"]
    assert second["inputs"]["api_token"] == "<redacted>"
    assert second["inputs"]["branch"] == "main"


@pytest.mark.parametrize(
    "changed_inputs",
    [
        {"api_token": "different", "branch": "main"},
        {"api_token": "secret", "branch": "release"},
    ],
)
def test_idempotency_key_rejects_different_workflow_inputs(
    tmp_path: Path, changed_inputs: dict[str, str],
) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    store.create(
        _definition(),
        {"api_token": "secret", "branch": "main"},
        idempotency_key="same",
    )

    with pytest.raises(ToolError) as raised:
        store.create(_definition(), changed_inputs, idempotency_key="same")

    assert raised.value.code == "idempotency_conflict"


def test_compare_and_swap_rejects_stale_version(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(_definition())
    updated = store.transition(workflow["workflow_id"], 1, WorkflowState.QUEUED)
    assert updated["version"] == 2
    with pytest.raises(ToolError, match="changed"):
        store.transition(workflow["workflow_id"], 1, WorkflowState.CANCELLED)


def test_restart_marks_running_step_uncertain(tmp_path: Path) -> None:
    path = tmp_path / "workflows.db"
    store = WorkflowStore(path)
    workflow = store.create(_definition())
    queued = store.transition(workflow["workflow_id"], 1, WorkflowState.QUEUED)
    running = store.transition(workflow["workflow_id"], queued["version"], WorkflowState.RUNNING)
    store.checkpoint_step(workflow["workflow_id"], 0, "running", increment_attempt=True)
    assert running["state"] == "running"

    recovered = WorkflowStore(path).get(workflow["workflow_id"])
    assert recovered["state"] == "uncertain"
    assert recovered["steps"][0]["state"] == "uncertain"


def test_store_releases_sqlite_handles_after_operations(tmp_path: Path) -> None:
    root = tmp_path / "state"
    store = WorkflowStore(root / "workflows.db")
    workflow = store.create(_definition())
    store.get(workflow["workflow_id"])
    shutil.rmtree(root)
    assert not root.exists()
