"""Versioned SQLite persistence for durable workflow lifecycle state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.errors import ToolError
from core.workflow_models import (
    OperationState,
    WorkflowDefinition,
    WorkflowLease,
    WorkflowState,
    validate_operation_transition,
    validate_workflow_transition,
)

SCHEMA_VERSION = 4
_SECRET_MARKERS = (
    "password",
    "secret",
    "token",
    "api_key",
    "credential",
    "authorization",
)
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


def canonical_execution_definition(definition: WorkflowDefinition) -> str:
    """Serialize the exact durable payload that the executor is allowed to run."""

    return _canonical(asdict(definition))


def public_workflow_projection(value: Any) -> Any:
    """Return the bounded/redacted representation exposed through status APIs."""

    return redact_inputs(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _lease_times(ttl_sec: float) -> tuple[str, str]:
    if not 5 <= ttl_sec <= 300:
        raise ToolError("invalid_lease_ttl", "Lease TTL must be between 5 and 300 seconds.")
    now = datetime.now(timezone.utc)
    return (
        now.isoformat(timespec="milliseconds"),
        (now + timedelta(seconds=ttl_sec)).isoformat(timespec="milliseconds"),
    )


def _lease_from_row(row: sqlite3.Row) -> WorkflowLease:
    return WorkflowLease(
        workflow_id=str(row["workflow_id"]),
        owner_id=str(row["owner_id"]),
        lease_token=str(row["lease_token"]),
        acquired_at=str(row["acquired_at"]),
        heartbeat_at=str(row["heartbeat_at"]),
        expires_at=str(row["expires_at"]),
        version=int(row["version"]),
    )


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
                CREATE INDEX IF NOT EXISTS idx_workflows_queue ON workflows(state, created_at);
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
                version = 1
            if version < 2:
                self._migrate_v2(connection)
                version = 2
            if version < 3:
                self._migrate_v3(connection)
                version = 3
            if version < 4:
                self._migrate_v4(connection)
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
    def _migrate_v2(connection: sqlite3.Connection) -> None:
        """Add exact execution payload storage while marking legacy rows unsafe to execute."""

        connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE workflows ADD COLUMN execution_definition_json TEXT;
            ALTER TABLE workflows ADD COLUMN execution_definition_hash TEXT;
            ALTER TABLE workflows ADD COLUMN execution_compatible INTEGER NOT NULL DEFAULT 0;
            PRAGMA user_version = 2;
            COMMIT;
            """
        )

    @staticmethod
    def _migrate_v3(connection: sqlite3.Connection) -> None:
        """Persist cooperative workflow cancellation requests."""

        connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE workflows ADD COLUMN cancel_requested_at TEXT;
            ALTER TABLE workflows ADD COLUMN cancel_reason TEXT;
            PRAGMA user_version = 3;
            COMMIT;
            """
        )

    @staticmethod
    def _migrate_v4(connection: sqlite3.Connection) -> None:
        """Separate operation intent, external identity, and reconciliation evidence."""

        connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE workflow_operations ADD COLUMN intent_evidence_json TEXT;
            ALTER TABLE workflow_operations ADD COLUMN external_ref_json TEXT;
            ALTER TABLE workflow_operations ADD COLUMN reconciliation_evidence_json TEXT;
            PRAGMA user_version = 4;
            COMMIT;
            """
        )

    @staticmethod
    def _definition_json(definition: WorkflowDefinition) -> str:
        return _canonical(public_workflow_projection(asdict(definition)))

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
        arguments_fingerprint = _digest(dict(step.get("arguments", {})))
        payload = {
            "name": str(step.get("name", "")),
            "action": str(step.get("action", "")),
            "arguments_fingerprint": arguments_fingerprint,
            "timeout_sec": float(step.get("timeout_sec", 300)),
            "max_retries": int(step.get("max_retries", 0)),
            "postcondition": step.get("postcondition"),
        }
        return payload, _digest(payload), arguments_fingerprint

    @classmethod
    def _materialize_workflow(
        cls,
        connection: sqlite3.Connection,
        workflow_id: str,
        *,
        legacy: bool,
    ) -> None:
        workflow = connection.execute(
            """
            SELECT definition_json, execution_definition_json, execution_compatible, updated_at
            FROM workflows WHERE workflow_id = ?
            """,
            (workflow_id,),
        ).fetchone()
        if workflow is None:
            raise ToolError("workflow_not_found", f"Workflow {workflow_id!r} was not found.")
        raw_definition = (
            workflow["execution_definition_json"]
            if int(workflow["execution_compatible"] or 0) and workflow["execution_definition_json"]
            else workflow["definition_json"]
        )
        definition = json.loads(str(raw_definition))
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
                (workflow_id, step_index, operation_id, state, _canonical({"legacy": legacy}), _now()),
            )

    @classmethod
    def _materialize_all(cls, connection: sqlite3.Connection) -> None:
        for row in connection.execute("SELECT workflow_id FROM workflows ORDER BY created_at").fetchall():
            cls._materialize_workflow(connection, str(row["workflow_id"]), legacy=True)

    def materialize_operations(self, workflow_id: str) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._materialize_workflow(connection, workflow_id, legacy=True)
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
        from core.workflow_actions import validate_workflow_definition

        validate_workflow_definition(definition)
        definition_json = self._definition_json(definition)
        execution_definition_json = canonical_execution_definition(definition)
        execution_definition_hash = hashlib.sha256(execution_definition_json.encode("utf-8")).hexdigest()
        inputs_json = _canonical(redact_inputs(inputs or {}))
        workflow_id = uuid.uuid4().hex
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing = connection.execute(
                    """
                    SELECT workflow_id, state, execution_definition_json,
                           execution_definition_hash, execution_compatible
                    FROM workflows WHERE idempotency_key = ?
                    """,
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    if int(existing["execution_compatible"] or 0) != 1:
                        if str(existing["state"]) not in {
                            WorkflowState.COMPLETED.value,
                            WorkflowState.CANCELLED.value,
                        }:
                            raise ToolError(
                                "workflow_legacy_definition_unrecoverable",
                                "The idempotent workflow was created by a legacy schema whose exact execution payload is unavailable.",
                            )
                        connection.commit()
                        return self.get(str(existing["workflow_id"]))
                    if (
                        existing["execution_definition_hash"] != execution_definition_hash
                        or existing["execution_definition_json"] != execution_definition_json
                    ):
                        raise ToolError(
                            "idempotency_conflict",
                            "Idempotency key already refers to a different workflow definition.",
                        )
                    connection.commit()
                    return self.get(str(existing["workflow_id"]))
            connection.execute(
                """
                INSERT INTO workflows(
                    workflow_id, idempotency_key, definition_json, inputs_json, state,
                    current_step, version, created_at, updated_at, last_error,
                    execution_definition_json, execution_definition_hash, execution_compatible
                ) VALUES (?, ?, ?, ?, ?, 0, 1, ?, ?, NULL, ?, ?, 1)
                """,
                (
                    workflow_id,
                    idempotency_key,
                    definition_json,
                    inputs_json,
                    initial_state.value,
                    now,
                    now,
                    execution_definition_json,
                    execution_definition_hash,
                ),
            )
            connection.executemany(
                "INSERT INTO workflow_steps(workflow_id, step_index, state) VALUES (?, ?, 'created')",
                [(workflow_id, index) for index in range(len(definition.steps))],
            )
            self._materialize_workflow(connection, workflow_id, legacy=False)
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
            "execution_compatible": bool(row["execution_compatible"]),
            "execution_definition_hash": row["execution_definition_hash"],
            "cancel_requested_at": row["cancel_requested_at"],
            "cancel_reason": row["cancel_reason"],
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

    def execution_definition(self, workflow_id: str) -> WorkflowDefinition:
        """Return the exact durable definition or fail closed for legacy rows."""

        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT execution_definition_json, execution_compatible
                FROM workflows WHERE workflow_id = ?
                """,
                (workflow_id,),
            ).fetchone()
        if row is None:
            raise ToolError("workflow_not_found", f"Workflow {workflow_id!r} was not found.")
        if int(row["execution_compatible"] or 0) != 1 or not row["execution_definition_json"]:
            raise ToolError(
                "workflow_legacy_definition_unrecoverable",
                "This legacy workflow has no exact durable execution definition and cannot be executed or resumed.",
            )
        from core.workflow_models import StepDefinition

        raw = json.loads(str(row["execution_definition_json"]))
        return WorkflowDefinition(
            str(raw["name"]),
            tuple(StepDefinition(**step) for step in raw["steps"]),
            str(raw.get("description", "")),
        )

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
            for key in (
                "postcondition_json", "result_json", "evidence_json",
                "intent_evidence_json", "external_ref_json", "reconciliation_evidence_json",
            ):
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

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT workflow_id FROM workflow_operations WHERE operation_id = ?", (operation_id,),
            ).fetchone()
        if row is None:
            raise ToolError("workflow_operation_not_found", "Workflow operation was not found.")
        operations = self.list_operations(str(row["workflow_id"]))["items"]
        return next(item for item in operations if item["operation_id"] == operation_id)

    def get_operation_for_reconciliation(self, operation_id: str) -> dict[str, Any]:
        """Return exact durable recovery fields for one operation."""

        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM workflow_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise ToolError("workflow_operation_not_found", "Workflow operation was not found.")
        item = dict(row)
        for key in (
            "postcondition_json", "result_json", "evidence_json",
            "intent_evidence_json", "external_ref_json", "reconciliation_evidence_json",
        ):
            raw = item.pop(key)
            item[key.removesuffix("_json")] = json.loads(raw) if raw else None
        return item

    def update_operation_context(
        self,
        operation_id: str,
        *,
        lease_token: str,
        intent_evidence: dict[str, Any] | None = None,
        external_ref: dict[str, Any] | None = None,
        postcondition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist recovery identity while an operation is running."""

        if intent_evidence is None and external_ref is None and postcondition is None:
            return self.get_operation(operation_id)
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT workflow_id, state FROM workflow_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise ToolError("workflow_operation_not_found", "Workflow operation was not found.")
            workflow_id = str(row["workflow_id"])
            if str(row["state"]) not in {OperationState.RUNNING.value, OperationState.WAITING.value}:
                raise ToolError("workflow_operation_not_active", "Recovery context can only be updated for an active operation.")
            lease = connection.execute(
                "SELECT lease_token, expires_at FROM workflow_leases WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            if lease is None or str(lease["lease_token"]) != lease_token:
                raise ToolError("workflow_lease_token_mismatch", "Workflow lease token does not match the current owner.")
            if str(lease["expires_at"]) <= now:
                raise ToolError("workflow_lease_expired", "Workflow lease has expired.")
            connection.execute(
                """
                UPDATE workflow_operations
                SET intent_evidence_json = COALESCE(?, intent_evidence_json),
                    external_ref_json = COALESCE(?, external_ref_json),
                    postcondition_json = COALESCE(?, postcondition_json),
                    updated_at = ?, version = version + 1
                WHERE operation_id = ?
                """,
                (
                    _canonical(intent_evidence) if intent_evidence else None,
                    _canonical(external_ref) if external_ref else None,
                    _canonical(postcondition) if postcondition else None,
                    now,
                    operation_id,
                ),
            )
            connection.commit()
        return self.get_operation(operation_id)

    def resolve_uncertain_operation(
        self,
        operation_id: str,
        expected_version: int,
        final_state: OperationState,
        *,
        evidence: dict[str, Any],
        event_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically resolve one uncertain operation and its step/workflow aggregates."""
        if final_state not in {
            OperationState.SUCCEEDED, OperationState.FAILED, OperationState.UNCERTAIN,
            OperationState.ACKNOWLEDGED, OperationState.UNRESOLVABLE,
        }:
            raise ToolError("invalid_operation_resolution", "Unsupported uncertain operation resolution.")
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT * FROM workflow_operations WHERE operation_id = ?", (operation_id,),
            ).fetchone()
            if operation is None:
                raise ToolError("workflow_operation_not_found", "Workflow operation was not found.")
            if int(operation["version"]) != expected_version:
                raise ToolError("workflow_operation_version_conflict", "Operation changed; reload it before retrying.")
            current_operation_state = OperationState(str(operation["state"]))
            if current_operation_state is not OperationState.UNCERTAIN:
                raise ToolError("workflow_operation_not_uncertain", "Only an uncertain operation can be resolved.")
            workflow_id, step_index = str(operation["workflow_id"]), int(operation["step_index"])
            workflow = connection.execute(
                "SELECT state, current_step FROM workflows WHERE workflow_id = ?", (workflow_id,),
            ).fetchone()
            if workflow is None or str(workflow["state"]) != WorkflowState.UNCERTAIN.value:
                raise ToolError("workflow_not_uncertain", "The operation aggregate is not in uncertain state.")

            if final_state is OperationState.UNCERTAIN:
                operation_version_increment = 1
            else:
                validate_operation_transition(current_operation_state, OperationState.RECONCILING)
                validate_operation_transition(OperationState.RECONCILING, final_state)
                operation_version_increment = 2

            if final_state in {OperationState.SUCCEEDED, OperationState.ACKNOWLEDGED}:
                step_state = "completed"
                next_step = step_index + 1
                step_count = int(connection.execute(
                    "SELECT COUNT(*) FROM workflow_steps WHERE workflow_id = ?", (workflow_id,),
                ).fetchone()[0])
                workflow_state = WorkflowState.COMPLETED if next_step == step_count else WorkflowState.PAUSED
                current_step = next_step
            elif final_state in {OperationState.FAILED, OperationState.UNRESOLVABLE}:
                step_state, workflow_state, current_step = "failed", WorkflowState.FAILED, step_index
            else:
                step_state, workflow_state, current_step = "uncertain", WorkflowState.UNCERTAIN, step_index

            if workflow_state is not WorkflowState.UNCERTAIN:
                validate_workflow_transition(WorkflowState.UNCERTAIN, WorkflowState.RECONCILING)
                validate_workflow_transition(WorkflowState.RECONCILING, workflow_state)
            redacted_evidence = redact_inputs(evidence)
            connection.execute(
                """
                UPDATE workflow_operations SET state = ?, version = version + ?, evidence_json = ?,
                    reconciliation_evidence_json = ?, updated_at = ?, finished_at = CASE WHEN ? = 'uncertain' THEN finished_at ELSE ? END,
                    error = CASE WHEN ? IN ('failed','unresolvable') THEN error ELSE NULL END
                WHERE operation_id = ? AND version = ?
                """,
                (final_state.value, operation_version_increment, _canonical(redacted_evidence),
                 _canonical(redacted_evidence), now, final_state.value, now, final_state.value,
                 operation_id, expected_version),
            )
            connection.execute(
                """
                UPDATE workflow_steps SET state = ?, evidence_json = ?,
                    finished_at = CASE WHEN ? = 'uncertain' THEN finished_at ELSE ? END
                WHERE workflow_id = ? AND step_index = ?
                """,
                (step_state, _canonical(redacted_evidence), step_state, now, workflow_id, step_index),
            )
            connection.execute(
                """
                UPDATE workflows SET state = ?, current_step = ?, version = version + ?, updated_at = ?,
                    last_error = CASE WHEN ? = 'failed' THEN COALESCE(last_error, 'operation resolution failed') ELSE NULL END
                WHERE workflow_id = ?
                """,
                (workflow_state.value, current_step, 1 if workflow_state is WorkflowState.UNCERTAIN else 2,
                 now, workflow_state.value, workflow_id),
            )
            connection.execute(
                """
                INSERT INTO workflow_events(workflow_id, step_index, operation_id, event_type, state, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (workflow_id, step_index, operation_id, event_type, final_state.value,
                 _canonical(redact_inputs(metadata or {})), now),
            )
            connection.commit()
        return {"operation": self.get_operation(operation_id), "workflow": self.get(workflow_id)}

    def checkpoint_operation(
        self,
        operation_id: str,
        state: OperationState,
        *,
        lease_token: str,
        result: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        intent_evidence: dict[str, Any] | None = None,
        external_ref: dict[str, Any] | None = None,
        postcondition: dict[str, Any] | None = None,
        error: str | None = None,
        increment_attempt: bool = False,
    ) -> dict[str, Any]:
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT workflow_id, state FROM workflow_operations WHERE operation_id = ?", (operation_id,),
            ).fetchone()
            if operation is None:
                raise ToolError("workflow_operation_not_found", "Workflow operation was not found.")
            validate_operation_transition(OperationState(str(operation["state"])), state)
            workflow_id = str(operation["workflow_id"])
            lease = connection.execute(
                "SELECT lease_token, expires_at FROM workflow_leases WHERE workflow_id = ?", (workflow_id,),
            ).fetchone()
            if lease is None or str(lease["lease_token"]) != lease_token:
                raise ToolError("workflow_lease_token_mismatch", "Workflow lease token does not match the current owner.")
            if str(lease["expires_at"]) <= now:
                raise ToolError("workflow_lease_expired", "Workflow lease has expired.")
            connection.execute(
                """
                UPDATE workflow_operations SET state = ?, attempts = attempts + ?, version = version + 1,
                    result_json = ?, evidence_json = ?,
                    intent_evidence_json = COALESCE(?, intent_evidence_json),
                    external_ref_json = COALESCE(?, external_ref_json),
                    postcondition_json = COALESCE(?, postcondition_json),
                    error = ?, updated_at = ?,
                    started_at = CASE WHEN ? = 'running' THEN COALESCE(started_at, ?) ELSE started_at END,
                    finished_at = CASE WHEN ? IN ('succeeded','failed','uncertain','cancelled') THEN ? ELSE finished_at END
                WHERE operation_id = ?
                """,
                (
                    state.value,
                    int(increment_attempt),
                    _canonical(redact_inputs(result)) if result else None,
                    _canonical(redact_inputs(evidence)) if evidence else None,
                    _canonical(intent_evidence) if intent_evidence else None,
                    _canonical(external_ref) if external_ref else None,
                    _canonical(postcondition) if postcondition else None,
                    error[:2_000] if error else None,
                    now,
                    state.value,
                    now,
                    state.value,
                    now,
                    operation_id,
                ),
            )
            connection.commit()
        return next(item for item in self.list_operations(workflow_id)["items"] if item["operation_id"] == operation_id)

    def acquire_lease(self, workflow_id: str, owner_id: str, ttl_sec: float) -> WorkflowLease:
        owner_id = owner_id.strip()
        if not owner_id or len(owner_id) > 200:
            raise ToolError("invalid_lease_owner", "Lease owner must contain between 1 and 200 characters.")
        now, expires_at = _lease_times(ttl_sec)
        token = uuid.uuid4().hex
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone() is None:
                raise ToolError("workflow_not_found", f"Workflow {workflow_id!r} was not found.")
            current = connection.execute(
                "SELECT * FROM workflow_leases WHERE workflow_id = ?", (workflow_id,),
            ).fetchone()
            if current is None:
                connection.execute(
                    """
                    INSERT INTO workflow_leases(
                        workflow_id, owner_id, lease_token, acquired_at, heartbeat_at, expires_at, version
                    ) VALUES (?, ?, ?, ?, ?, ?, 1)
                    """,
                    (workflow_id, owner_id, token, now, now, expires_at),
                )
            elif str(current["expires_at"]) > now:
                if str(current["owner_id"]) != owner_id:
                    raise ToolError("workflow_already_claimed", "Workflow is already claimed by another executor.")
                connection.commit()
                return _lease_from_row(current)
            else:
                connection.execute(
                    """
                    UPDATE workflow_leases
                    SET owner_id = ?, lease_token = ?, acquired_at = ?, heartbeat_at = ?,
                        expires_at = ?, version = version + 1
                    WHERE workflow_id = ?
                    """,
                    (owner_id, token, now, now, expires_at, workflow_id),
                )
            row = connection.execute(
                "SELECT * FROM workflow_leases WHERE workflow_id = ?", (workflow_id,),
            ).fetchone()
            connection.commit()
        if row is None:  # pragma: no cover - guarded by the transaction above
            raise ToolError("workflow_lease_missing", "Workflow lease could not be persisted.")
        return _lease_from_row(row)

    def renew_lease(self, workflow_id: str, lease_token: str, ttl_sec: float) -> WorkflowLease:
        now, expires_at = _lease_times(ttl_sec)
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM workflow_leases WHERE workflow_id = ?", (workflow_id,),
            ).fetchone()
            if current is None:
                raise ToolError("workflow_lease_missing", "Workflow has no active lease.")
            if str(current["lease_token"]) != lease_token:
                raise ToolError("workflow_lease_token_mismatch", "Workflow lease token does not match the current owner.")
            if str(current["expires_at"]) <= now:
                raise ToolError("workflow_lease_expired", "Workflow lease has expired and cannot be renewed.")
            connection.execute(
                """
                UPDATE workflow_leases
                SET heartbeat_at = ?, expires_at = ?, version = version + 1
                WHERE workflow_id = ? AND lease_token = ?
                """,
                (now, expires_at, workflow_id, lease_token),
            )
            row = connection.execute(
                "SELECT * FROM workflow_leases WHERE workflow_id = ?", (workflow_id,),
            ).fetchone()
            connection.commit()
        if row is None:  # pragma: no cover - guarded by the transaction above
            raise ToolError("workflow_lease_missing", "Workflow lease disappeared during renewal.")
        return _lease_from_row(row)

    def require_lease(self, workflow_id: str, lease_token: str) -> WorkflowLease:
        now = _now()
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM workflow_leases WHERE workflow_id = ?", (workflow_id,),
            ).fetchone()
        if row is None:
            raise ToolError("workflow_lease_missing", "Workflow has no active lease.")
        if str(row["lease_token"]) != lease_token:
            raise ToolError("workflow_lease_token_mismatch", "Workflow lease token does not match the current owner.")
        if str(row["expires_at"]) <= now:
            raise ToolError("workflow_lease_expired", "Workflow lease has expired.")
        return _lease_from_row(row)

    def release_lease(self, workflow_id: str, lease_token: str) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT lease_token FROM workflow_leases WHERE workflow_id = ?", (workflow_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return
            if str(row["lease_token"]) != lease_token:
                raise ToolError("workflow_lease_token_mismatch", "Workflow lease token does not match the current owner.")
            connection.execute(
                "DELETE FROM workflow_leases WHERE workflow_id = ? AND lease_token = ?",
                (workflow_id, lease_token),
            )
            connection.commit()

    def release_lease_if_current(self, workflow_id: str, lease_token: str) -> bool:
        """Release only this exact lease token and never disturb a replacement owner."""

        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT lease_token FROM workflow_leases WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            if row is None or str(row["lease_token"]) != lease_token:
                connection.commit()
                return False
            deleted = connection.execute(
                "DELETE FROM workflow_leases WHERE workflow_id = ? AND lease_token = ?",
                (workflow_id, lease_token),
            ).rowcount
            connection.commit()
            return deleted == 1

    def cancel_requested(self, workflow_id: str) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT state, cancel_requested_at FROM workflows WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
        if row is None:
            raise ToolError("workflow_not_found", f"Workflow {workflow_id!r} was not found.")
        return bool(row["cancel_requested_at"]) or str(row["state"]) in {
            WorkflowState.CANCELLING.value,
            WorkflowState.CANCELLED.value,
        }

    def request_cancel(
        self,
        workflow_id: str,
        expected_version: int,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Persist a cooperative cancellation request without lying about an in-flight side effect."""

        bounded_reason = reason.strip()[:2_000] if isinstance(reason, str) and reason.strip() else None
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, version FROM workflows WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            if row is None:
                raise ToolError("workflow_not_found", f"Workflow {workflow_id!r} was not found.")
            if int(row["version"]) != expected_version:
                raise ToolError("workflow_version_conflict", "Workflow changed; reload status before retrying.")
            current = WorkflowState(str(row["state"]))
            if current in {WorkflowState.COMPLETED, WorkflowState.CANCELLED}:
                connection.commit()
                return self.get(workflow_id)
            if current is WorkflowState.CANCELLING:
                connection.commit()
                return self.get(workflow_id)
            target = WorkflowState.CANCELLING if current is WorkflowState.RUNNING else WorkflowState.CANCELLED
            validate_workflow_transition(current, target)
            cursor = connection.execute(
                """
                UPDATE workflows
                SET state = ?, cancel_requested_at = COALESCE(cancel_requested_at, ?),
                    cancel_reason = COALESCE(?, cancel_reason), updated_at = ?, version = version + 1
                WHERE workflow_id = ? AND version = ?
                """,
                (target.value, now, bounded_reason, now, workflow_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise ToolError("workflow_version_conflict", "Workflow changed; reload status before retrying.")
            connection.execute(
                """
                INSERT INTO workflow_events(workflow_id, event_type, state, metadata_json, created_at)
                VALUES (?, 'workflow_cancel_requested', ?, ?, ?)
                """,
                (
                    workflow_id,
                    target.value,
                    _canonical(redact_inputs({"reason": bounded_reason})) if bounded_reason else None,
                    now,
                ),
            )
            connection.commit()
        return self.get(workflow_id)

    def transition(
        self,
        workflow_id: str,
        expected_version: int,
        state: WorkflowState,
        *,
        current_step: int | None = None,
        last_error: str | None = None,
        lease_token: str | None = None,
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
            if lease_token is not None:
                lease = connection.execute(
                    "SELECT lease_token, expires_at FROM workflow_leases WHERE workflow_id = ?", (workflow_id,),
                ).fetchone()
                if lease is None or str(lease["lease_token"]) != lease_token:
                    raise ToolError("workflow_lease_token_mismatch", "Workflow lease token does not match the current owner.")
                if str(lease["expires_at"]) <= _now():
                    raise ToolError("workflow_lease_expired", "Workflow lease has expired.")
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

    def advance_running(
        self,
        workflow_id: str,
        expected_version: int,
        *,
        current_step: int,
        lease_token: str | None = None,
    ) -> dict[str, Any]:
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
            if lease_token is not None:
                lease = connection.execute(
                    "SELECT lease_token, expires_at FROM workflow_leases WHERE workflow_id = ?", (workflow_id,),
                ).fetchone()
                if lease is None or str(lease["lease_token"]) != lease_token:
                    raise ToolError("workflow_lease_token_mismatch", "Workflow lease token does not match the current owner.")
                if str(lease["expires_at"]) <= _now():
                    raise ToolError("workflow_lease_expired", "Workflow lease has expired.")
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
        lease_token: str | None = None,
    ) -> None:
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if lease_token is not None:
                lease = connection.execute(
                    "SELECT lease_token, expires_at FROM workflow_leases WHERE workflow_id = ?",
                    (workflow_id,),
                ).fetchone()
                if lease is None:
                    raise ToolError("workflow_lease_missing", "Workflow has no active lease.")
                if str(lease["lease_token"]) != lease_token:
                    raise ToolError(
                        "workflow_lease_token_mismatch",
                        "Workflow lease token does not match the current owner.",
                    )
                if str(lease["expires_at"]) <= now:
                    raise ToolError("workflow_lease_expired", "Workflow lease has expired.")
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
            connection.commit()

    def recover_interrupted(self) -> int:
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT workflows.workflow_id, workflows.current_step
                FROM workflows
                LEFT JOIN workflow_leases ON workflow_leases.workflow_id = workflows.workflow_id
                WHERE workflows.state = 'running'
                  AND (workflow_leases.workflow_id IS NULL OR workflow_leases.expires_at <= ?)
                """,
                (now,),
            ).fetchall()
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
                    WHERE workflow_id = ? AND step_index = ? AND state IN ('created','running','waiting')
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

    def list_queued(self, *, limit: int = 100) -> dict[str, Any]:
        if limit < 1 or limit > 500:
            raise ToolError("invalid_pagination", "limit must be between 1 and 500.")
        with closing(self._connect()) as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM workflows WHERE state = 'queued'"
            ).fetchone()[0])
            rows = connection.execute(
                """
                SELECT workflow_id, state, current_step, version, created_at, updated_at
                FROM workflows
                WHERE state = 'queued'
                ORDER BY created_at ASC, workflow_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "count": len(rows),
            "total_count": total,
            "has_more": len(rows) < total,
        }

    def health_summary(self, *, sample_limit: int = 5) -> dict[str, Any]:
        bounded = min(max(sample_limit, 1), 20)
        with closing(self._connect()) as connection:
            workflow_counts = {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    "SELECT state, COUNT(*) AS count FROM workflows GROUP BY state ORDER BY state"
                ).fetchall()
            }
            operation_counts = {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    "SELECT state, COUNT(*) AS count FROM workflow_operations GROUP BY state ORDER BY state"
                ).fetchall()
            }
            unresolved = connection.execute(
                """
                SELECT operation_id, workflow_id, step_index, action, state, updated_at
                FROM workflow_operations
                WHERE state IN ('uncertain','reconciling','unresolvable')
                ORDER BY updated_at ASC LIMIT ?
                """,
                (bounded,),
            ).fetchall()
            lease_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM workflow_leases WHERE expires_at > ?",
                    (_now(),),
                ).fetchone()[0]
            )
            event_total = int(connection.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0])
        db_bytes = 0
        for path in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            try:
                if path.is_file():
                    db_bytes += path.stat().st_size
            except OSError:
                continue
        unresolved_total = sum(
            operation_counts.get(state, 0)
            for state in ("uncertain", "reconciling", "unresolvable")
        )
        return {
            "workflow_counts": workflow_counts,
            "workflow_total": sum(workflow_counts.values()),
            "operation_counts": operation_counts,
            "operation_total": sum(operation_counts.values()),
            "event_total": event_total,
            "workflow_db_bytes": db_bytes,
            "queued_workflows": workflow_counts.get("queued", 0),
            "running_workflows": workflow_counts.get("running", 0) + workflow_counts.get("cancelling", 0),
            "uncertain_workflows": workflow_counts.get("uncertain", 0) + workflow_counts.get("reconciling", 0),
            "unresolved_operation_count": unresolved_total,
            "oldest_unresolved_operations": [dict(row) for row in unresolved],
            "active_lease_count": lease_count,
            "sample_limit": bounded,
        }


_STORE: WorkflowStore | None = None
_STORE_PATH: Path | None = None


def workflow_store(path: Path) -> WorkflowStore:
    global _STORE, _STORE_PATH
    if _STORE is None or _STORE_PATH != path:
        _STORE = WorkflowStore(path)
        _STORE_PATH = path
    return _STORE
