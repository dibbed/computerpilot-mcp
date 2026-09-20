"""Versioned SQLite persistence for durable workflow lifecycle state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.errors import ToolError
from core.workflow_models import OperationState, WorkflowDefinition, WorkflowState, validate_workflow_transition

SCHEMA_VERSION = 1
_SECRET_MARKERS = ("password", "secret", "token", "api_key", "credential", "authorization")
_STEP_OPERATION_STATES = {
    "created": "created",
    "running": "uncertain",
    "waiting": "waiting",
    "failed": "failed",
    "uncertain": "uncertain",
    "reconciling": "reconciling",
    "completed": "succeeded",
    "cancelled": "cancelled",
}


def redact_inputs(value: Any, key: str = "") -> Any:
    if any(marker in key.casefold() for marker in _SECRET_MARKERS):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(item_key): redact_inputs(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_inputs(item) for item in value]
    if isinstance(value, str):
        return value[:2_000]
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class WorkflowStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self.recover_interrupted()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflows (
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
                CREATE TABLE IF NOT EXISTS workflow_steps (
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
                CREATE INDEX IF NOT EXISTS idx_workflows_state ON workflows(state, updated_at);
                """
            )
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise ToolError(
                    "workflow_schema_too_new",
                    f"Workflow database schema {version} is newer than supported version {SCHEMA_VERSION}.",
                )
            if version < 1:
                self._migrate_v1(connection)
            connection.execute("BEGIN IMMEDIATE")
            self._redact_stored_definitions(connection)
            self._materialize_all(connection)
            connection.commit()

    @staticmethod
    def _migrate_v1(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS workflow_operations (
                operation_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                step_index INTEGER NOT NULL,
                operation_index INTEGER NOT NULL,
                action TEXT NOT NULL,
                definition_hash TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1,
                arguments_fingerprint TEXT NOT NULL,
                postcondition_json TEXT,
                result_json TEXT,
                evidence_json TEXT,
                recovery_operation_id TEXT,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                finished_at TEXT,
                error TEXT,
                UNIQUE(workflow_id, step_index, operation_index)
            );
            CREATE TABLE IF NOT EXISTS workflow_leases (
                workflow_id TEXT PRIMARY KEY REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                owner_id TEXT NOT NULL,
                lease_token TEXT NOT NULL UNIQUE,
                acquired_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS workflow_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                step_index INTEGER,
                operation_id TEXT,
                event_type TEXT NOT NULL,
                state TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_workflow_operations_state
                ON workflow_operations(workflow_id, state, step_index, operation_index);
            CREATE INDEX IF NOT EXISTS idx_workflow_events_lookup
                ON workflow_events(workflow_id, event_id);
            PRAGMA user_version = 1;
            COMMIT;
            """
        )

    @staticmethod
    def _definition_json(definition: WorkflowDefinition) -> str:
        return _canonical(redact_inputs(asdict(definition)))

    @staticmethod
    def _redact_stored_definitions(connection: sqlite3.Connection) -> None:
        rows = connection.execute("SELECT workflow_id, definition_json FROM workflows").fetchall()
        for row in rows:
            current = str(row["definition_json"])
            redacted = _canonical(redact_inputs(json.loads(current)))
            if redacted != current:
                connection.execute(
                    "UPDATE workflows SET definition_json = ? WHERE workflow_id = ?",
                    (redacted, row["workflow_id"]),
                )

    @staticmethod
    def _operation_payload(step: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
        arguments_fingerprint = _digest(redact_inputs(dict(step.get("arguments", {}))))
        payload = {
            "name": str(step.get("name", "")),
            "action": str(step.get("action", "")),
            "arguments_fingerprint": arguments_fingerprint,
            "timeout_sec": float(step.get("timeout_sec", 300)),
            "max_retries": int(step.get("max_retries", 0)),
            "postcondition": redact_inputs(step.get("postcondition")),
        }
        return payload, _digest(payload), arguments_fingerprint

    @classmethod
    def _materialize_workflow(cls, connection: sqlite3.Connection, workflow_id: str) -> None:
        workflow = connection.execute(
            "SELECT definition_json, updated_at FROM workflows WHERE workflow_id = ?", (workflow_id,),
        ).fetchone()
        if workflow is None:
            raise ToolError("workflow_not_found", f"Workflow {workflow_id!r} was not found.")
        definition = json.loads(str(workflow["definition_json"]))
        steps = connection.execute(
            "SELECT * FROM workflow_steps WHERE workflow_id = ? ORDER BY step_index", (workflow_id,),
        ).fetchall()
        definitions = list(definition.get("steps", []))
        if len(steps) != len(definitions):
            raise ToolError("workflow_definition_conflict", "Stored workflow steps do not match the workflow definition.")
        for step_row, step in zip(steps, definitions, strict=True):
            step_index = int(step_row["step_index"])
            payload, definition_hash, arguments_fingerprint = cls._operation_payload(dict(step))
            idempotency_key = hashlib.sha256(
                f"{workflow_id}|{step_index}|0|{definition_hash}".encode()
            ).hexdigest()
            operation_id = hashlib.sha256(f"operation|{idempotency_key}".encode()).hexdigest()[:32]
            existing = connection.execute(
                """
                SELECT definition_hash FROM workflow_operations
                WHERE workflow_id = ? AND step_index = ? AND operation_index = 0
                """,
                (workflow_id, step_index),
            ).fetchone()
            if existing is not None:
                if str(existing["definition_hash"]) != definition_hash:
                    raise ToolError(
                        "workflow_definition_conflict",
                        "Materialized operation does not match the stored workflow definition.",
                    )
                continue
            state = _STEP_OPERATION_STATES.get(str(step_row["state"]), OperationState.UNCERTAIN.value)
            postcondition = payload["postcondition"]
            connection.execute(
                """
                INSERT INTO workflow_operations(
                    operation_id, workflow_id, step_index, operation_index, action,
                    definition_hash, idempotency_key, state, attempts, version,
                    arguments_fingerprint, postcondition_json, result_json, evidence_json,
                    recovery_operation_id, started_at, updated_at, finished_at, error
                ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, 1, ?, ?, NULL, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    workflow_id,
                    step_index,
                    payload["action"],
                    definition_hash,
                    idempotency_key,
                    state,
                    int(step_row["attempts"]),
                    arguments_fingerprint,
                    _canonical(postcondition) if postcondition is not None else None,
                    step_row["evidence_json"],
                    step_row["started_at"],
                    workflow["updated_at"],
                    step_row["finished_at"],
                    step_row["error"],
                ),
            )
            connection.execute(
                """
                INSERT INTO workflow_events(
                    workflow_id, step_index, operation_id, event_type, state, metadata_json, created_at
                ) VALUES (?, ?, ?, 'operation_materialized', ?, ?, ?)
                """,
                (workflow_id, step_index, operation_id, state, _canonical({"legacy": True}), _now()),
            )

    @classmethod
    def _materialize_all(cls, connection: sqlite3.Connection) -> None:
        for row in connection.execute("SELECT workflow_id FROM workflows ORDER BY created_at").fetchall():
            cls._materialize_workflow(connection, str(row["workflow_id"]))

    def materialize_operations(self, workflow_id: str) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._materialize_workflow(connection, workflow_id)
            connection.commit()
        return self.list_operations(workflow_id)

    def create(
        self,
        definition: WorkflowDefinition,
        inputs: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        initial_state: WorkflowState = WorkflowState.CREATED,
    ) -> dict[str, Any]:
        if not definition.steps:
            raise ToolError("empty_workflow", "A workflow must contain at least one step.")
        definition_json = self._definition_json(definition)
        inputs_json = _canonical(redact_inputs(inputs or {}))
        workflow_id = uuid.uuid4().hex
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing = connection.execute(
                    "SELECT workflow_id, definition_json FROM workflows WHERE idempotency_key = ?", (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    if existing["definition_json"] != definition_json:
                        raise ToolError("idempotency_conflict", "Idempotency key already refers to a different workflow definition.")
                    connection.commit()
                    return self.get(str(existing["workflow_id"]))
            connection.execute(
                "INSERT INTO workflows VALUES (?, ?, ?, ?, ?, 0, 1, ?, ?, NULL)",
                (workflow_id, idempotency_key, definition_json, inputs_json, initial_state.value, now, now),
            )
            connection.executemany(
                "INSERT INTO workflow_steps(workflow_id, step_index, state) VALUES (?, ?, 'created')",
                [(workflow_id, index) for index in range(len(definition.steps))],
            )
            self._materialize_workflow(connection, workflow_id)
            connection.commit()
        return self.get(workflow_id)

    def get(self, workflow_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
            if row is None:
                raise ToolError("workflow_not_found", f"Workflow {workflow_id!r} was not found.")
            steps = connection.execute(
                "SELECT * FROM workflow_steps WHERE workflow_id = ? ORDER BY step_index", (workflow_id,),
            ).fetchall()
        return {
            "workflow_id": row["workflow_id"],
            "state": row["state"],
            "current_step": row["current_step"],
            "version": row["version"],
            "definition": json.loads(row["definition_json"]),
            "inputs": json.loads(row["inputs_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_error": row["last_error"],
            "steps": [
                {
                    "step_index": step["step_index"],
                    "state": step["state"],
                    "attempts": step["attempts"],
                    "started_at": step["started_at"],
                    "finished_at": step["finished_at"],
                    "evidence": json.loads(step["evidence_json"]) if step["evidence_json"] else None,
                    "error": step["error"],
                }
                for step in steps
            ],
        }

    def list_operations(
        self,
        workflow_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
        state: OperationState | None = None,
    ) -> dict[str, Any]:
        if offset < 0 or limit < 1 or limit > 500:
            raise ToolError("invalid_pagination", "offset must be non-negative and limit must be between 1 and 500.")
        filters = ["workflow_id = ?"]
        parameters: list[Any] = [workflow_id]
        if state is not None:
            filters.append("state = ?")
            parameters.append(state.value)
        where = " AND ".join(filters)
        with closing(self._connect()) as connection:
            if connection.execute("SELECT 1 FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone() is None:
                raise ToolError("workflow_not_found", f"Workflow {workflow_id!r} was not found.")
            total = int(connection.execute(f"SELECT COUNT(*) FROM workflow_operations WHERE {where}", parameters).fetchone()[0])
            rows = connection.execute(
                f"SELECT * FROM workflow_operations WHERE {where} ORDER BY step_index, operation_index LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            for key in ("postcondition_json", "result_json", "evidence_json"):
                raw = item.pop(key)
                item[key.removesuffix("_json")] = json.loads(raw) if raw else None
            items.append(item)
        return {
            "items": items,
            "count": len(items),
            "total_count": total,
            "offset": offset,
            "has_more": offset + len(items) < total,
        }

    def transition(
        self,
        workflow_id: str,
        expected_version: int,
        state: WorkflowState,
        *,
        current_step: int | None = None,
        last_error: str | None = None,
    ) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT state, version FROM workflows WHERE workflow_id = ?", (workflow_id,),
            ).fetchone()
            if current is None:
                raise ToolError("workflow_not_found", f"Workflow {workflow_id!r} was not found.")
            if int(current["version"]) != expected_version:
                raise ToolError("workflow_version_conflict", "Workflow changed; reload status before retrying.")
            validate_workflow_transition(WorkflowState(str(current["state"])), state)
            assignments = ["state = ?", "version = version + 1", "updated_at = ?", "last_error = ?"]
            parameters: list[Any] = [state.value, _now(), last_error[:2_000] if last_error else None]
            if current_step is not None:
                assignments.append("current_step = ?")
                parameters.append(current_step)
            parameters.extend([workflow_id, expected_version])
            cursor = connection.execute(
                f"UPDATE workflows SET {', '.join(assignments)} WHERE workflow_id = ? AND version = ?", parameters,
            )
            if cursor.rowcount != 1:
                raise ToolError("workflow_version_conflict", "Workflow changed; reload status before retrying.")
            connection.commit()
        return self.get(workflow_id)

    def advance_running(self, workflow_id: str, expected_version: int, *, current_step: int) -> dict[str, Any]:
        if current_step < 0:
            raise ToolError("invalid_workflow_step", "current_step must be non-negative.")
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, version FROM workflows WHERE workflow_id = ?", (workflow_id,),
            ).fetchone()
            if row is None:
                raise ToolError("workflow_not_found", f"Workflow {workflow_id!r} was not found.")
            if int(row["version"]) != expected_version:
                raise ToolError("workflow_version_conflict", "Workflow changed; reload status before retrying.")
            if row["state"] != WorkflowState.RUNNING.value:
                raise ToolError("workflow_not_running", "Only a running workflow can advance its current step.")
            cursor = connection.execute(
                """
                UPDATE workflows SET current_step = ?, version = version + 1, updated_at = ?
                WHERE workflow_id = ? AND version = ? AND state = 'running'
                """,
                (current_step, _now(), workflow_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise ToolError("workflow_version_conflict", "Workflow changed; reload status before retrying.")
            connection.commit()
        return self.get(workflow_id)

    def checkpoint_step(
        self,
        workflow_id: str,
        step_index: int,
        state: str,
        *,
        evidence: dict[str, Any] | None = None,
        error: str | None = None,
        increment_attempt: bool = False,
    ) -> None:
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                UPDATE workflow_steps SET state = ?, attempts = attempts + ?,
                    started_at = CASE WHEN ? = 'running' THEN COALESCE(started_at, ?) ELSE started_at END,
                    finished_at = CASE WHEN ? IN ('completed','failed','uncertain','cancelled') THEN ? ELSE finished_at END,
                    evidence_json = ?, error = ?
                WHERE workflow_id = ? AND step_index = ?
                """,
                (
                    state, int(increment_attempt), state, now, state, now,
                    _canonical(redact_inputs(evidence)) if evidence else None,
                    error[:2_000] if error else None, workflow_id, step_index,
                ),
            )
            if cursor.rowcount != 1:
                raise ToolError("workflow_step_not_found", "Workflow step was not found.")

    def recover_interrupted(self) -> int:
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT workflow_id, current_step FROM workflows WHERE state = 'running'").fetchall()
            for row in rows:
                workflow_id = str(row["workflow_id"])
                step_index = int(row["current_step"])
                connection.execute(
                    """
                    UPDATE workflows SET state = 'uncertain', version = version + 1,
                        updated_at = ?, last_error = ? WHERE workflow_id = ?
                    """,
                    (now, "runtime_ended_during_step", workflow_id),
                )
                connection.execute(
                    """
                    UPDATE workflow_steps SET state = 'uncertain', finished_at = ?, error = ?
                    WHERE workflow_id = ? AND step_index = ? AND state = 'running'
                    """,
                    (now, "runtime_ended_without_checkpoint", workflow_id, step_index),
                )
                connection.execute(
                    """
                    UPDATE workflow_operations SET state = 'uncertain', version = version + 1,
                        updated_at = ?, finished_at = ?, error = ?
                    WHERE workflow_id = ? AND step_index = ? AND state = 'running'
                    """,
                    (now, now, "runtime_ended_without_checkpoint", workflow_id, step_index),
                )
            connection.commit()
        return len(rows)

    def list(self, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM workflows").fetchone()[0])
            rows = connection.execute(
                """
                SELECT workflow_id, state, current_step, version, created_at, updated_at
                FROM workflows ORDER BY updated_at DESC LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "count": len(rows),
            "total_count": total,
            "offset": offset,
            "has_more": offset + len(rows) < total,
        }


_STORE: WorkflowStore | None = None
_STORE_PATH: Path | None = None


def workflow_store(path: Path) -> WorkflowStore:
    global _STORE, _STORE_PATH
    if _STORE is None or _STORE_PATH != path:
        _STORE = WorkflowStore(path)
        _STORE_PATH = path
    return _STORE
