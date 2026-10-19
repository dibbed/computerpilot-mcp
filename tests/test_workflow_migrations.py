from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from core import workflow_store
from core.errors import ToolError
from core.workflow_store import SCHEMA_VERSION
from core.workflows import WorkflowStore


@pytest.mark.parametrize("recover", [False, True])
def test_future_schema_is_rejected_without_database_writes(tmp_path: Path, recover: bool) -> None:
    path = tmp_path / "future.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            f"CREATE TABLE future_truth (id INTEGER PRIMARY KEY, value TEXT);"
            f"INSERT INTO future_truth VALUES (1, 'preserve'); PRAGMA user_version={SCHEMA_VERSION + 1};"
        )
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ToolError) as failure:
        WorkflowStore(path, recover=recover)
    assert failure.value.code == "workflow_schema_too_new"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION + 1
        assert connection.execute("SELECT name FROM sqlite_master ORDER BY name").fetchall() == [("future_truth",)]
        assert connection.execute("SELECT * FROM future_truth").fetchall() == [(1, "preserve")]
    assert not path.with_name(path.name + "-wal").exists()
    assert not path.with_name(path.name + "-shm").exists()


def _seed_v024_database(path: Path) -> None:
    definition = {
        "name": "legacy",
        "description": "existing workflow",
        "steps": [
            {
                "name": "verify",
                "action": "verify_changes",
                "arguments": {"cwd": "C:/repo", "api_token": "legacy-secret"},
                "timeout_sec": 300,
                "max_retries": 0,
                "postcondition": None,
            }
        ],
    }
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE workflows (
                workflow_id TEXT PRIMARY KEY,
                idempotency_key TEXT UNIQUE,
                definition_json TEXT NOT NULL,
                inputs_json TEXT NOT NULL,
                state TEXT NOT NULL,
                current_step INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_error TEXT
            );
            CREATE TABLE workflow_steps (
                workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                step_index INTEGER NOT NULL,
                state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                finished_at TEXT,
                evidence_json TEXT,
                error TEXT,
                PRIMARY KEY (workflow_id, step_index)
            );
            """
        )
        connection.execute(
            "INSERT INTO workflows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-workflow",
                "legacy-key",
                json.dumps(definition),
                json.dumps({"api_token": "<redacted>"}),
                "completed",
                1,
                7,
                "2026-09-19T00:00:00+00:00",
                "2026-09-19T00:01:00+00:00",
                None,
            ),
        )
        connection.execute(
            "INSERT INTO workflow_steps VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-workflow",
                0,
                "completed",
                1,
                "2026-09-19T00:00:10+00:00",
                "2026-09-19T00:00:20+00:00",
                json.dumps({"ok": True}),
                None,
            ),
        )


def test_existing_database_is_migrated_without_data_loss(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _seed_v024_database(path)

    store = WorkflowStore(path)
    migrated = store.get("legacy-workflow")
    operations = store.list_operations("legacy-workflow")

    assert migrated["state"] == "completed"
    assert migrated["version"] == 7
    assert migrated["steps"][0]["state"] == "completed"
    assert migrated["steps"][0]["evidence"] == {"ok": True}
    assert operations["total_count"] == 1
    assert operations["items"][0]["state"] == "succeeded"
    assert operations["items"][0]["action"] == "verify_changes"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        stored_definition = connection.execute(
            "SELECT definition_json FROM workflows WHERE workflow_id = 'legacy-workflow'"
        ).fetchone()[0]
        assert "legacy-secret" not in stored_definition
        assert "<redacted>" in stored_definition


def test_nonterminal_legacy_workflow_fails_closed_without_exact_execution_payload(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _seed_v024_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE workflows SET state = 'queued', current_step = 0 WHERE workflow_id = 'legacy-workflow'"
        )
        connection.execute(
            "UPDATE workflow_steps SET state = 'created', attempts = 0 WHERE workflow_id = 'legacy-workflow'"
        )

    store = WorkflowStore(path)
    migrated = store.get("legacy-workflow")

    assert migrated["execution_compatible"] is False
    with pytest.raises(ToolError, match="exact durable execution definition"):
        store.execution_definition("legacy-workflow")


def test_migration_reopen_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _seed_v024_database(path)

    first = WorkflowStore(path).list_operations("legacy-workflow")["items"]
    second = WorkflowStore(path).list_operations("legacy-workflow")["items"]

    assert len(first) == len(second) == 1
    assert first[0]["operation_id"] == second[0]["operation_id"]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0] == 1


def test_completed_backfill_skips_history_scan_but_repairs_missing_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "legacy.db"
    _seed_v024_database(path)
    WorkflowStore(path)

    def unexpected_scan(*args: object) -> None:
        raise AssertionError("Current schema should not rescan complete history")

    monkeypatch.setattr(WorkflowStore, "_redact_stored_definitions", staticmethod(unexpected_scan))
    monkeypatch.setattr(WorkflowStore, "_materialize_all", staticmethod(unexpected_scan))
    reopened = WorkflowStore(path)
    assert reopened.list_operations("legacy-workflow")["total_count"] == 1

    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM workflow_operations WHERE workflow_id = 'legacy-workflow'")
    repaired = WorkflowStore(path)
    assert repaired.list_operations("legacy-workflow")["total_count"] == 1


def test_failed_backfill_is_retried_after_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "legacy.db"
    _seed_v024_database(path)
    def crash_after_redaction(connection: sqlite3.Connection) -> None:
        raise RuntimeError("simulated crash")

    with monkeypatch.context() as scoped:
        scoped.setattr(WorkflowStore, "_materialize_all", staticmethod(crash_after_redaction))
        with pytest.raises(RuntimeError, match="simulated crash"):
            WorkflowStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflow_maintenance").fetchone()[0] == 0
    reopened = WorkflowStore(path)
    assert reopened.list_operations("legacy-workflow")["total_count"] == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflow_maintenance").fetchone()[0] == 1


def test_simultaneous_old_schema_open_serializes_version_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "legacy.db"
    _seed_v024_database(path)
    with sqlite3.connect(path) as connection:
        WorkflowStore._migrate_v1(connection)
    barrier = threading.Barrier(2)
    original = WorkflowStore._migrate_v2

    def synchronized_migration(connection: sqlite3.Connection) -> None:
        try:
            barrier.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            # A correctly serialized migration lets only one opener reach this point.
            pass
        original(connection)

    monkeypatch.setattr(WorkflowStore, "_migrate_v2", staticmethod(synchronized_migration))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(WorkflowStore, path) for _ in range(2)]
        for future in futures:
            assert future.result(timeout=12).get("legacy-workflow")["state"] == "completed"

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_transient_wal_lock_during_open_is_retried_without_leaking_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "legacy.db"
    _seed_v024_database(path)
    original_connect = sqlite3.connect
    attempts = 0
    connections: list[sqlite3.Connection] = []

    class ContendedConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            nonlocal attempts
            if sql == "PRAGMA journal_mode=WAL":
                attempts += 1
                if attempts == 1:
                    raise sqlite3.OperationalError("database is locked")
            return super().execute(sql, parameters)

    def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        if not args or args[0] != path:
            return original_connect(*args, **kwargs)
        connection = original_connect(*args, factory=ContendedConnection, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(workflow_store.sqlite3, "connect", connect)

    def unrelated_background_connection() -> None:
        connection = sqlite3.connect(tmp_path / "unrelated.db")
        try:
            assert type(connection) is sqlite3.Connection
            assert connection.execute("SELECT 1").fetchone() == (1,)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(unrelated_background_connection).result(timeout=5)
    store = WorkflowStore(path)
    assert store.get("legacy-workflow")["state"] == "completed"
    assert attempts >= 2
    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_wal_is_configured_at_startup_and_not_repeated_on_each_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "workflow.db"
    original_connect = sqlite3.connect
    wal_setups = 0

    class TrackedConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            nonlocal wal_setups
            if sql == "PRAGMA journal_mode=WAL":
                wal_setups += 1
            return super().execute(sql, parameters)

    def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        if not args or args[0] != path:
            return original_connect(*args, **kwargs)
        return original_connect(*args, factory=TrackedConnection, **kwargs)

    monkeypatch.setattr(workflow_store.sqlite3, "connect", connect)
    store = WorkflowStore(path)
    unrelated = sqlite3.connect(tmp_path / "unrelated.db")
    try:
        unrelated.execute("PRAGMA journal_mode=WAL")
    finally:
        unrelated.close()
    for _ in range(5):
        store.health_summary()
    assert wal_setups == 1
    with original_connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_v5_database_migrates_route_columns_without_losing_legacy_data(tmp_path: Path) -> None:
    path = tmp_path / "v5.db"
    _seed_v024_database(path)
    with sqlite3.connect(path) as connection:
        WorkflowStore._migrate_v1(connection)
        WorkflowStore._migrate_v2(connection)
        WorkflowStore._migrate_v3(connection)
        WorkflowStore._migrate_v4(connection)
        WorkflowStore._migrate_v5(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5

    store = WorkflowStore(path)
    migrated = store.get("legacy-workflow")
    operations = store.list_operations("legacy-workflow")

    assert SCHEMA_VERSION == 11
    assert migrated["state"] == "completed"
    assert migrated["execution_mode"] == "sequential"
    assert migrated["graph_contract_version"] == 0
    assert migrated["version"] == 7
    assert operations["total_count"] == 1
    operation = operations["items"][0]
    assert operation["execution_intent"] is None
    assert operation["selected_route"] is None
    assert operation["router_policy_version"] is None
    assert operation["route_decision"] is None
    assert operation["fallback_history"] == []

    with sqlite3.connect(path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(workflow_operations)").fetchall()
        }
        assert {
            "execution_intent_json",
            "selected_route",
            "router_policy_version",
            "route_decision_json",
            "fallback_history_json",
        } <= columns
        workflow_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(workflows)").fetchall()
        }
        step_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(workflow_steps)").fetchall()
        }
        assert {"execution_mode", "graph_contract_version"} <= workflow_columns
        assert "step_id" in step_columns
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_step_dependencies'"
        ).fetchone() is not None
        admission_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(workflow_execution_admissions)").fetchall()
        }
        operation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(workflow_operations)").fetchall()
        }
        assert {"attempt_token", "execution_id", "dispatch_phase", "attempt_generation"} <= operation_columns
        assert {"attempt_token", "execution_id", "lease_token", "category", "weight"} <= admission_columns
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_resource_requests'"
        ).fetchone() is not None
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_resource_claims'"
        ).fetchone() is not None
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_capacity_requests'"
        ).fetchone() is not None
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def _seed_v9_admissions(path: Path) -> None:
    _seed_v024_database(path)
    with sqlite3.connect(path) as db:
        for version in range(1, 10):
            getattr(WorkflowStore, f"_migrate_v{version}")(db)
        db.execute(
            "INSERT INTO workflow_resource_requests "
            "(request_id, workflow_id, owner_token, category, state, created_at, updated_at) "
            "VALUES ('legacy-resource','legacy-workflow','owner','test','active','before','before')",
        )
        db.execute(
            "INSERT INTO runtime_capacity_requests "
            "(admission_id, category, owner_token, weight, limit_snapshot, state, created_at, updated_at) "
            "VALUES ('legacy-permit','analyzer','owner',1,1,'active','before','before')",
        )


def test_v9_capacity_upgrade_preserves_unlinked_ownership_and_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "v9.sqlite3"
    _seed_v9_admissions(path)
    store = WorkflowStore(path, recover=False)

    assert store.get("legacy-workflow")["version"] == 7
    assert store.resource_request("legacy-resource")["state"] == "quarantined"
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT admission_id, state, resource_request_id FROM runtime_capacity_requests").fetchone()
        assert row == ("legacy-permit", "active", None)
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_v10_partial_upgrade_rolls_back_before_retry(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-denied.sqlite3"
    _seed_v9_admissions(path)
    with closing(sqlite3.connect(path)) as db:
        db.set_authorizer(
            lambda action, arg1, arg2, database, trigger:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_INDEX else sqlite3.SQLITE_OK,
        )
        with pytest.raises(sqlite3.DatabaseError):
            WorkflowStore._migrate_v10(db)
        db.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
        assert "resource_request_id" not in {row[1] for row in db.execute("PRAGMA table_info(runtime_capacity_requests)")}
    store = WorkflowStore(path, recover=False)
    assert store.health_summary()["runtime_capacity_counts"]["analyzer:active"] == 1


def _seed_v10_policy_owners(path: Path) -> None:
    _seed_v9_admissions(path)
    with closing(sqlite3.connect(path)) as db, db:
        WorkflowStore._migrate_v10(db)
        db.execute("UPDATE workflow_resource_requests SET state='active',reason=NULL WHERE request_id='legacy-resource'")
        db.execute(
            "INSERT INTO workflow_resource_requests(request_id,owner_token,category,state,created_at,updated_at) "
            "VALUES ('legacy-pending','pending-owner','test','pending','before','before')",
        )


def test_v11_policy_migration_preserves_unknown_effects_and_denies_legacy_activation(tmp_path: Path) -> None:
    path = tmp_path / "v10-policy.sqlite3"
    _seed_v10_policy_owners(path)
    store = WorkflowStore(path, recover=False)
    assert store.resource_request("legacy-resource")["state"] == "quarantined"
    assert store.resource_request("legacy-resource")["reason"] == "legacy_resource_policy_unknown"
    assert store.resource_request("legacy-pending")["state"] == "released"
    assert store.get("legacy-workflow")["version"] == 7
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 11
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT state FROM runtime_capacity_requests").fetchone()[0] == "active"
        with pytest.raises(sqlite3.IntegrityError, match="resource_policy_incompatible"):
            db.execute("UPDATE workflow_resource_requests SET state='active' WHERE request_id='legacy-pending'")
        with pytest.raises(sqlite3.IntegrityError, match="resource_policy_incompatible"):
            db.execute(
                "INSERT INTO workflow_resource_requests(request_id,owner_token,category,state,created_at,updated_at) "
                "VALUES ('old-client','old-owner','test','pending','before','before')",
            )


def test_v11_partial_policy_upgrade_rolls_back_without_unlocking_owners(tmp_path: Path) -> None:
    path = tmp_path / "policy-upgrade-denied.sqlite3"
    _seed_v10_policy_owners(path)
    with closing(sqlite3.connect(path)) as db:
        db.set_authorizer(
            lambda action, table, column, database, trigger:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_TRIGGER else sqlite3.SQLITE_OK,
        )
        with pytest.raises(sqlite3.DatabaseError):
            WorkflowStore._migrate_v11(db)
        db.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 10
        assert "policy_fingerprint" not in {row[1] for row in db.execute("PRAGMA table_info(workflow_resource_requests)")}
        assert db.execute("SELECT state FROM workflow_resource_requests WHERE request_id='legacy-resource'").fetchone()[0] == "active"
    assert WorkflowStore(path, recover=False).resource_request("legacy-resource")["state"] == "quarantined"
