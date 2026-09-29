from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core.errors import ToolError
from core import workflow_store
from core.workflow_store import SCHEMA_VERSION
from core.workflows import WorkflowStore


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
        def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:
            nonlocal attempts
            if sql == "PRAGMA journal_mode=WAL":
                attempts += 1
                if attempts == 1:
                    raise sqlite3.OperationalError("database is locked")
            return super().execute(sql, parameters)

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = original_connect(*args, factory=ContendedConnection, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(workflow_store.sqlite3, "connect", connect)
    store = WorkflowStore(path)
    assert store.get("legacy-workflow")["state"] == "completed"
    assert attempts >= 2
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
