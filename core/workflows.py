"""SQLite-backed durable workflow definitions, checkpoints, and recovery."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from core.errors import ToolError


class WorkflowState(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    PAUSED = "paused"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    RECONCILING = "reconciling"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class StepDefinition:
    name: str
    action: str
    arguments: dict[str, Any] = field(default_factory=dict)
    timeout_sec: float = 300
    max_retries: int = 0
    postcondition: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    name: str
    steps: tuple[StepDefinition, ...]
    description: str = ""


_SECRET_MARKERS = ("password", "secret", "token", "api_key", "credential", "authorization")


def redact_inputs(value: Any, key: str = "") -> Any:
    if any(marker in key.casefold() for marker in _SECRET_MARKERS):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(item_key): redact_inputs(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [redact_inputs(item) for item in value]
    if isinstance(value, str):
        return value[:2_000]
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


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
        with self._connect() as connection:
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

    @staticmethod
    def _definition_json(definition: WorkflowDefinition) -> str:
        return json.dumps(asdict(definition), ensure_ascii=False, separators=(",", ":"), sort_keys=True)

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
        inputs_json = json.dumps(redact_inputs(inputs or {}), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        workflow_id = uuid.uuid4().hex
        now = _now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing = connection.execute(
                    "SELECT workflow_id, definition_json FROM workflows WHERE idempotency_key = ?",
                    (idempotency_key,),
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
            connection.commit()
        return self.get(workflow_id)

    def get(self, workflow_id: str) -> dict[str, Any]:
        with self._connect() as connection:
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

    def transition(
        self,
        workflow_id: str,
        expected_version: int,
        state: WorkflowState,
        *,
        current_step: int | None = None,
        last_error: str | None = None,
    ) -> dict[str, Any]:
        assignments = ["state = ?", "version = version + 1", "updated_at = ?", "last_error = ?"]
        parameters: list[Any] = [state.value, _now(), last_error[:2_000] if last_error else None]
        if current_step is not None:
            assignments.append("current_step = ?")
            parameters.append(current_step)
        parameters.extend([workflow_id, expected_version])
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE workflows SET {', '.join(assignments)} WHERE workflow_id = ? AND version = ?", parameters,
            )
            if cursor.rowcount != 1:
                if connection.execute("SELECT 1 FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone() is None:
                    raise ToolError("workflow_not_found", f"Workflow {workflow_id!r} was not found.")
                raise ToolError("workflow_version_conflict", "Workflow changed; reload status before retrying.")
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
        with self._lock, self._connect() as connection:
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
                    json.dumps(redact_inputs(evidence), ensure_ascii=False, separators=(",", ":")) if evidence else None,
                    error[:2_000] if error else None, workflow_id, step_index,
                ),
            )
            if cursor.rowcount != 1:
                raise ToolError("workflow_step_not_found", "Workflow step was not found.")

    def recover_interrupted(self) -> int:
        now = _now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT workflow_id, current_step FROM workflows WHERE state = 'running'").fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE workflows SET state = 'uncertain', version = version + 1, updated_at = ?, last_error = ? WHERE workflow_id = ?",
                    (now, "runtime_ended_during_step", row["workflow_id"]),
                )
                connection.execute(
                    """
                    UPDATE workflow_steps SET state = 'uncertain', finished_at = ?, error = ?
                    WHERE workflow_id = ? AND step_index = ? AND state = 'running'
                    """,
                    (now, "runtime_ended_without_checkpoint", row["workflow_id"], row["current_step"]),
                )
            connection.commit()
        return len(rows)

    def list(self, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        with self._connect() as connection:
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
