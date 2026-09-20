from __future__ import annotations

import json
import sqlite3
from pathlib import Path

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


def test_migration_reopen_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _seed_v024_database(path)

    first = WorkflowStore(path).list_operations("legacy-workflow")["items"]
    second = WorkflowStore(path).list_operations("legacy-workflow")["items"]

    assert len(first) == len(second) == 1
    assert first[0]["operation_id"] == second[0]["operation_id"]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0] == 1
