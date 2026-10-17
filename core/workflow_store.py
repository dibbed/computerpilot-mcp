"""Versioned SQLite persistence for durable workflow lifecycle state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Sequence
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, cast

from core.errors import ToolError
from core.file_lock import exclusive_file_lock
from core.resource_claims import ResourceClaim, canonical_claims, claim_sets_conflict, native_resource_owner_alive
from core.workflow_models import (
    OperationState,
    StepDefinition,
    WorkflowDefinition,
    WorkflowLease,
    WorkflowState,
    validate_operation_transition,
    validate_workflow_transition,
    workflow_topological_order,
)

SCHEMA_VERSION = 10
GRAPH_CONTRACT_VERSION = 1
_RESOURCE_CAPABILITY_SQL = """
SELECT r.* FROM workflow_resource_requests r
WHERE r.request_id=? AND r.owner_token=? AND r.state='active'
  AND ((r.workflow_id IS NULL AND r.operation_id IS NULL) OR EXISTS (
      SELECT 1 FROM workflow_execution_admissions a
      JOIN workflow_operations o ON o.operation_id=a.operation_id
      JOIN workflow_leases l ON l.workflow_id=a.workflow_id
      JOIN workflows w ON w.workflow_id=a.workflow_id
      WHERE a.operation_id=r.operation_id AND a.workflow_id=r.workflow_id
        AND a.attempt_token=r.attempt_token AND o.attempt_token=a.attempt_token
        AND o.execution_id=a.execution_id
        AND o.dispatch_phase IN ('reserved','prepared','may_dispatch')
        AND l.lease_token=a.lease_token AND l.expires_at>?
        AND w.state='running' AND w.cancel_requested_at IS NULL
  ))
"""
_ROUTE_HISTORY_MAX = 20
_ROUTE_JSON_MAX_CHARS = 16_000
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


def _definition_payload(definition: WorkflowDefinition) -> dict[str, Any]:
    """Serialize definitions while preserving exact legacy sequential bytes."""

    payload = asdict(definition)
    if definition.execution_mode == "sequential":
        payload.pop("execution_mode", None)
        payload.pop("max_parallel_steps", None)
        payload.pop("total_timeout_sec", None)
        for step in payload["steps"]:
            step.pop("step_id", None)
            step.pop("depends_on", None)
    return payload


def canonical_execution_definition(definition: WorkflowDefinition) -> str:
    """Serialize the exact durable payload that the executor is allowed to run."""

    return _canonical(_definition_payload(definition))


def public_workflow_projection(value: Any) -> Any:
    """Return the bounded/redacted representation exposed through status APIs."""

    return redact_inputs(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _execution_mode(value: Any) -> Literal["sequential", "dag"]:
    mode = str(value or "sequential")
    if mode not in {"sequential", "dag"}:
        raise ToolError("workflow_definition_conflict", f"Unknown workflow execution mode {mode!r}.")
    return cast(Literal["sequential", "dag"], mode)


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
    def __init__(self, path: Path, *, recover: bool = True) -> None:
        self.path = path
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        if recover:
            self.recover_interrupted()

    def _connect(self, *, setup_wal: bool = False) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=10000")
            if setup_wal:
                for attempt in range(50):
                    try:
                        connection.execute("PRAGMA journal_mode=WAL")
                        break
                    except sqlite3.OperationalError as exc:
                        if "locked" not in str(exc).casefold() or attempt == 49:
                            raise
                        time.sleep(0.05)
            connection.execute("PRAGMA foreign_keys=ON")
            return connection
        except BaseException:
            connection.close()
            raise

    def _initialize(self) -> None:
        with self._lock, closing(self._connect(setup_wal=True)) as connection:
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
                CREATE TABLE IF NOT EXISTS workflow_maintenance (
                    key TEXT PRIMARY KEY
                );
                """
            )
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version < SCHEMA_VERSION:
                # executescript commits its caller's transaction; serialize the
                # version check and all migration stages across processes.
                with exclusive_file_lock(self.path.with_name(f"{self.path.name}.migration.lock")):
                    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
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
                        version = 4
                    if version < 5:
                        self._migrate_v5(connection)
                        version = 5
                    if version < 6:
                        self._migrate_v6(connection)
                        version = 6
                    if version < 7:
                        self._migrate_v7(connection)
                        version = 7
                    if version < 8:
                        self._migrate_v8(connection)
                        version = 8
                    if version < 9:
                        self._migrate_v9(connection)
                        version = 9
                    if version < 10:
                        self._migrate_v10(connection)
                        version = 10
            if version > SCHEMA_VERSION:
                raise ToolError(
                    "workflow_schema_too_new",
                    f"Workflow database schema {version} is newer than supported version {SCHEMA_VERSION}.",
                )
            backfill_key = "definition_redaction_and_operations_v1"
            complete = connection.execute(
                "SELECT 1 FROM workflow_maintenance WHERE key = ?", (backfill_key,),
            ).fetchone()
            if complete is None:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute(
                    "SELECT 1 FROM workflow_maintenance WHERE key = ?", (backfill_key,),
                ).fetchone() is None:
                    self._redact_stored_definitions(connection)
                    self._materialize_all(connection)
                    connection.execute("INSERT INTO workflow_maintenance(key) VALUES (?)", (backfill_key,))
                connection.commit()
            else:
                missing_sql = (
                    "SELECT DISTINCT steps.workflow_id FROM workflow_steps AS steps "
                    "LEFT JOIN workflow_operations AS operations "
                    "ON operations.workflow_id = steps.workflow_id "
                    "AND operations.step_index = steps.step_index AND operations.operation_index = 0 "
                    "WHERE operations.operation_id IS NULL"
                )
                if connection.execute(f"{missing_sql} LIMIT 1").fetchone() is not None:
                    connection.execute("BEGIN IMMEDIATE")
                    for row in connection.execute(missing_sql).fetchall():
                        self._materialize_workflow(connection, str(row["workflow_id"]), legacy=True)
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
    def _migrate_v5(connection: sqlite3.Connection) -> None:
        """Persist exact workflow-input identity for new idempotent submissions."""

        connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE workflows ADD COLUMN inputs_hash TEXT;
            PRAGMA user_version = 5;
            COMMIT;
            """
        )

    @staticmethod
    def _migrate_v6(connection: sqlite3.Connection) -> None:
        """Persist execution-router intent, decision, policy, and fallback history."""

        connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE workflow_operations ADD COLUMN execution_intent_json TEXT;
            ALTER TABLE workflow_operations ADD COLUMN selected_route TEXT;
            ALTER TABLE workflow_operations ADD COLUMN router_policy_version TEXT;
            ALTER TABLE workflow_operations ADD COLUMN route_decision_json TEXT;
            ALTER TABLE workflow_operations ADD COLUMN fallback_history_json TEXT;
            PRAGMA user_version = 6;
            COMMIT;
            """
        )

    @staticmethod
    def _migrate_v7(connection: sqlite3.Connection) -> None:
        """Persist explicit workflow graph identity and dependency edges."""

        connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE workflows ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'sequential';
            ALTER TABLE workflows ADD COLUMN graph_contract_version INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE workflow_steps ADD COLUMN step_id TEXT;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_steps_step_id
                ON workflow_steps(workflow_id, step_id) WHERE step_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS workflow_step_dependencies (
                workflow_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                dependency_step_index INTEGER NOT NULL,
                PRIMARY KEY (workflow_id, step_index, dependency_step_index),
                FOREIGN KEY (workflow_id, step_index)
                    REFERENCES workflow_steps(workflow_id, step_index) ON DELETE CASCADE,
                FOREIGN KEY (workflow_id, dependency_step_index)
                    REFERENCES workflow_steps(workflow_id, step_index) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_workflow_step_dependencies_parent
                ON workflow_step_dependencies(workflow_id, dependency_step_index, step_index);
            INSERT OR IGNORE INTO workflow_step_dependencies(workflow_id, step_index, dependency_step_index)
                SELECT workflow_id, step_index, step_index - 1
                FROM workflow_steps WHERE step_index > 0;
            PRAGMA user_version = 7;
            COMMIT;
            """
        )

    @staticmethod
    def _migrate_v8(connection: sqlite3.Connection) -> None:
        """Persist fenced workflow attempts and active execution admissions."""

        connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE workflows ADD COLUMN max_parallel_steps INTEGER NOT NULL DEFAULT 1;
            ALTER TABLE workflows ADD COLUMN total_timeout_sec REAL;
            ALTER TABLE workflow_operations ADD COLUMN attempt_token TEXT;
            ALTER TABLE workflow_operations ADD COLUMN execution_id TEXT;
            ALTER TABLE workflow_operations ADD COLUMN dispatch_phase TEXT;
            ALTER TABLE workflow_operations ADD COLUMN attempt_generation INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE workflow_operations ADD COLUMN next_retry_at TEXT;
            CREATE TABLE IF NOT EXISTS workflow_execution_admissions (
                operation_id TEXT PRIMARY KEY REFERENCES workflow_operations(operation_id) ON DELETE CASCADE,
                workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                attempt_token TEXT NOT NULL UNIQUE,
                execution_id TEXT NOT NULL,
                lease_token TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                category TEXT NOT NULL,
                weight INTEGER NOT NULL DEFAULT 1,
                admitted_at TEXT NOT NULL,
                process_pid INTEGER,
                process_create_time REAL
            );
            CREATE INDEX IF NOT EXISTS idx_workflow_execution_admissions_active
                ON workflow_execution_admissions(category, admitted_at, workflow_id);
            CREATE INDEX IF NOT EXISTS idx_workflow_operations_attempt
                ON workflow_operations(workflow_id, attempt_token, dispatch_phase);
            PRAGMA user_version = 8;
            COMMIT;
            """
        )

    @staticmethod
    def _migrate_v9(connection: sqlite3.Connection) -> None:
        """Persist fair resource requests/claims and shared runtime capacity admissions."""

        connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS workflow_resource_requests (
                ticket INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL UNIQUE,
                workflow_id TEXT REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                operation_id TEXT REFERENCES workflow_operations(operation_id) ON DELETE CASCADE,
                attempt_token TEXT,
                owner_token TEXT NOT NULL,
                category TEXT NOT NULL,
                state TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                resolved_evidence TEXT
            );
            CREATE TABLE IF NOT EXISTS workflow_resource_claims (
                claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL REFERENCES workflow_resource_requests(request_id) ON DELETE CASCADE,
                resource_kind TEXT NOT NULL,
                resource_key TEXT NOT NULL,
                mode TEXT NOT NULL,
                scope TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_workflow_resource_requests_state_ticket
                ON workflow_resource_requests(state, ticket);
            CREATE INDEX IF NOT EXISTS idx_workflow_resource_requests_operation
                ON workflow_resource_requests(operation_id, state);
            CREATE INDEX IF NOT EXISTS idx_workflow_resource_claims_request
                ON workflow_resource_claims(request_id, resource_kind, resource_key);
            CREATE TABLE IF NOT EXISTS runtime_capacity_requests (
                ticket INTEGER PRIMARY KEY AUTOINCREMENT,
                admission_id TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                owner_token TEXT NOT NULL,
                weight INTEGER NOT NULL,
                limit_snapshot INTEGER NOT NULL,
                state TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_runtime_capacity_category_state_ticket
                ON runtime_capacity_requests(category, state, ticket);
            PRAGMA user_version = 9;
            COMMIT;
            """
        )

    @staticmethod
    def _migrate_v10(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE runtime_capacity_requests ADD COLUMN resource_request_id TEXT
                REFERENCES workflow_resource_requests(request_id) ON DELETE SET NULL;
            CREATE INDEX idx_runtime_capacity_resource_state
                ON runtime_capacity_requests(resource_request_id, state);
            UPDATE workflow_resource_requests
            SET state='quarantined', reason='legacy_unlinked_capacity_requires_reconciliation'
            WHERE state='active' AND EXISTS (
                SELECT 1 FROM runtime_capacity_requests WHERE state!='released'
            );
            PRAGMA user_version = 10;
            COMMIT;
            """
        )

    @staticmethod
    def _definition_json(definition: WorkflowDefinition) -> str:
        return _canonical(public_workflow_projection(_definition_payload(definition)))

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
        if step.get("execution_intent") is not None:
            payload["execution_intent"] = step["execution_intent"]
        if step.get("step_id") is not None:
            payload["step_id"] = str(step["step_id"])
            payload["depends_on"] = list(step.get("depends_on", []))
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
                    recovery_operation_id, started_at, updated_at, finished_at, error,
                    execution_intent_json
                ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, 1, ?, ?, NULL, ?, NULL, ?, ?, ?, ?, ?)
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
                    (
                        _canonical(payload["execution_intent"])
                        if payload.get("execution_intent") is not None
                        else None
                    ),
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

    def materialize_operations(self, workflow_id: str, *, return_operations: bool = True) -> dict[str, Any] | None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._materialize_workflow(connection, workflow_id, legacy=True)
            connection.commit()
        return self.list_operations(workflow_id) if return_operations else None

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
        exact_inputs = inputs or {}
        inputs_json = _canonical(redact_inputs(exact_inputs))
        inputs_hash = _digest(exact_inputs)
        workflow_id = uuid.uuid4().hex
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing = connection.execute(
                    """
                    SELECT workflow_id, state, inputs_json, inputs_hash,
                           execution_definition_json, execution_definition_hash, execution_compatible
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
                    existing_inputs_hash = existing["inputs_hash"]
                    inputs_match = (
                        str(existing_inputs_hash) == inputs_hash
                        if existing_inputs_hash is not None
                        else str(existing["inputs_json"]) == inputs_json
                    )
                    if not inputs_match:
                        raise ToolError(
                            "idempotency_conflict",
                            "Idempotency key already refers to different workflow inputs.",
                        )
                    connection.commit()
                    return self.get(str(existing["workflow_id"]))
            connection.execute(
                """
                INSERT INTO workflows(
                    workflow_id, idempotency_key, definition_json, inputs_json, inputs_hash, state,
                    current_step, version, created_at, updated_at, last_error,
                    execution_definition_json, execution_definition_hash, execution_compatible,
                    execution_mode, graph_contract_version, max_parallel_steps, total_timeout_sec
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, ?, NULL, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    idempotency_key,
                    definition_json,
                    inputs_json,
                    inputs_hash,
                    initial_state.value,
                    now,
                    now,
                    execution_definition_json,
                    execution_definition_hash,
                    definition.execution_mode,
                    GRAPH_CONTRACT_VERSION if definition.execution_mode == "dag" else 0,
                    definition.max_parallel_steps,
                    definition.total_timeout_sec,
                ),
            )
            connection.executemany(
                "INSERT INTO workflow_steps(workflow_id, step_index, state, step_id) VALUES (?, ?, 'created', ?)",
                [(workflow_id, index, step.step_id) for index, step in enumerate(definition.steps)],
            )
            if definition.execution_mode == "dag":
                by_id = {str(step.step_id): index for index, step in enumerate(definition.steps)}
                dependency_rows = [
                    (workflow_id, index, by_id[dependency])
                    for index, step in enumerate(definition.steps)
                    for dependency in step.depends_on
                ]
            else:
                dependency_rows = [
                    (workflow_id, index, index - 1)
                    for index in range(1, len(definition.steps))
                ]
            if dependency_rows:
                connection.executemany(
                    """
                    INSERT INTO workflow_step_dependencies(workflow_id, step_index, dependency_step_index)
                    VALUES (?, ?, ?)
                    """,
                    dependency_rows,
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
            dependencies = connection.execute(
                """
                SELECT step_index, dependency_step_index
                FROM workflow_step_dependencies
                WHERE workflow_id = ?
                ORDER BY step_index, dependency_step_index
                """,
                (workflow_id,),
            ).fetchall()
            operations = connection.execute(
                """
                SELECT step_index, state FROM workflow_operations
                WHERE workflow_id = ? AND operation_index = 0
                ORDER BY step_index
                """,
                (workflow_id,),
            ).fetchall()
            admissions = connection.execute(
                """
                SELECT adm.operation_id, op.step_index, adm.attempt_token, adm.execution_id,
                       adm.category, adm.weight, adm.admitted_at
                FROM workflow_execution_admissions adm
                JOIN workflow_operations op USING(operation_id)
                WHERE adm.workflow_id = ?
                ORDER BY adm.admitted_at, operation_id
                """,
                (workflow_id,),
            ).fetchall()

        execution_mode = _execution_mode(row["execution_mode"])
        graph_contract_version = int(row["graph_contract_version"] or 0)
        dependency_indices: dict[int, list[int]] = {}
        for edge in dependencies:
            dependency_indices.setdefault(int(edge["step_index"]), []).append(int(edge["dependency_step_index"]))
        operation_states = {int(operation["step_index"]): str(operation["state"]) for operation in operations}
        admitted_indices = {int(admission["step_index"]) for admission in admissions}
        step_ids = {int(step["step_index"]): step["step_id"] for step in steps}
        successful_parent_states = {OperationState.SUCCEEDED.value, OperationState.ACKNOWLEDGED.value}
        blocked_parent_states = {
            OperationState.FAILED.value,
            OperationState.UNRESOLVABLE.value,
            OperationState.CANCELLED.value,
        }

        public_steps: list[dict[str, Any]] = []
        for step in steps:
            index = int(step["step_index"])
            item: dict[str, Any] = {
                "step_index": index,
                "state": step["state"],
                "attempts": step["attempts"],
                "started_at": step["started_at"],
                "finished_at": step["finished_at"],
                "evidence": json.loads(step["evidence_json"]) if step["evidence_json"] else None,
                "error": step["error"],
            }
            if execution_mode == "dag":
                parent_indices = dependency_indices.get(index, [])
                parent_ids = [str(step_ids[parent]) for parent in parent_indices]
                parent_states = [operation_states.get(parent, OperationState.CREATED.value) for parent in parent_indices]
                own_state = operation_states.get(index, OperationState.CREATED.value)
                if own_state in successful_parent_states:
                    readiness = "completed"
                elif index in admitted_indices:
                    readiness = "active"
                elif own_state in blocked_parent_states:
                    readiness = "terminal"
                elif own_state in {
                    OperationState.RUNNING.value,
                    OperationState.WAITING.value,
                    OperationState.UNCERTAIN.value,
                    OperationState.RECONCILING.value,
                }:
                    readiness = "active"
                elif any(state in blocked_parent_states for state in parent_states):
                    readiness = "blocked_dependencies"
                elif all(state in successful_parent_states for state in parent_states):
                    readiness = "ready"
                else:
                    readiness = "waiting_dependencies"
                item.update(
                    {
                        "step_id": step["step_id"],
                        "depends_on": parent_ids,
                        "readiness": readiness,
                    }
                )
            public_steps.append(item)

        ready_step_indices = [
            int(step["step_index"]) for step in public_steps if step.get("readiness") == "ready"
        ]
        active_step_indices = [
            int(step["step_index"]) for step in public_steps if step.get("readiness") == "active"
        ]
        blocked_step_indices = [
            int(step["step_index"])
            for step in public_steps
            if step.get("readiness") == "blocked_dependencies"
        ]
        completed_step_count = sum(1 for step in public_steps if step.get("readiness") == "completed")
        requested_parallel_steps = int(row["max_parallel_steps"] or 1)
        if execution_mode == "dag":
            from core.config import SETTINGS

            effective_parallel_steps = min(
                requested_parallel_steps,
                SETTINGS.workflow_max_parallel_steps,
                SETTINGS.max_running_workflow_steps,
            )
        else:
            effective_parallel_steps = 1

        topological_order = list(range(len(steps)))
        if execution_mode == "dag" and int(row["execution_compatible"] or 0) == 1 and row["execution_definition_json"]:
            raw = json.loads(str(row["execution_definition_json"]))
            definition = WorkflowDefinition(
                str(raw["name"]),
                tuple(StepDefinition(**step) for step in raw["steps"]),
                str(raw.get("description", "")),
                execution_mode,
                int(row["max_parallel_steps"] or raw.get("max_parallel_steps", 1)),
                float(row["total_timeout_sec"]) if row["total_timeout_sec"] is not None else raw.get("total_timeout_sec"),
            )
            topological_order = list(workflow_topological_order(definition))

        return {
            "workflow_id": row["workflow_id"],
            "state": row["state"],
            "current_step": row["current_step"],
            "version": row["version"],
            "definition": json.loads(row["definition_json"]),
            "inputs": json.loads(row["inputs_json"]),
            "execution_compatible": bool(row["execution_compatible"]),
            "execution_definition_hash": row["execution_definition_hash"],
            "execution_mode": execution_mode,
            "graph_contract_version": graph_contract_version,
            "topological_order": topological_order,
            "max_parallel_steps": requested_parallel_steps,
            "effective_parallel_steps": effective_parallel_steps,
            "total_timeout_sec": row["total_timeout_sec"],
            "active_admission_count": len(admissions),
            "ready_step_indices": ready_step_indices,
            "active_step_indices": active_step_indices,
            "blocked_step_indices": blocked_step_indices,
            "completed_step_count": completed_step_count,
            "cancel_requested_at": row["cancel_requested_at"],
            "cancel_reason": row["cancel_reason"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_error": row["last_error"],
            "steps": public_steps,
        }

    def execution_definition(self, workflow_id: str) -> WorkflowDefinition:
        """Return the exact durable definition or fail closed for legacy rows."""

        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT execution_definition_json, execution_compatible,
                       execution_mode, graph_contract_version, max_parallel_steps, total_timeout_sec
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
        execution_mode = _execution_mode(row["execution_mode"])
        graph_contract_version = int(row["graph_contract_version"] or 0)
        if execution_mode == "dag" and graph_contract_version != GRAPH_CONTRACT_VERSION:
            raise ToolError(
                "unsupported_workflow_graph_version",
                f"Workflow graph contract version {graph_contract_version} is not supported.",
            )
        if execution_mode == "sequential" and graph_contract_version != 0:
            raise ToolError(
                "workflow_definition_conflict",
                "Sequential workflow unexpectedly contains graph contract metadata.",
            )

        raw = json.loads(str(row["execution_definition_json"]))
        definition = WorkflowDefinition(
            str(raw["name"]),
            tuple(StepDefinition(**step) for step in raw["steps"]),
            str(raw.get("description", "")),
            execution_mode,
            int(row["max_parallel_steps"] or raw.get("max_parallel_steps", 1)),
            float(row["total_timeout_sec"])
            if row["total_timeout_sec"] is not None
            else raw.get("total_timeout_sec"),
        )
        workflow_topological_order(definition)
        return definition

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
                "execution_intent_json", "route_decision_json", "fallback_history_json",
            ):
                raw = item.pop(key)
                public_key = key.removesuffix("_json")
                if key == "fallback_history_json":
                    item[public_key] = json.loads(raw) if raw else []
                else:
                    item[public_key] = json.loads(raw) if raw else None
            items.append(item)
        return {
            "items": items,
            "count": len(items),
            "total_count": total,
            "offset": offset,
            "has_more": offset + len(items) < total,
        }

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        return self.get_operation_for_reconciliation(operation_id)

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
            "execution_intent_json", "route_decision_json", "fallback_history_json",
        ):
            raw = item.pop(key)
            public_key = key.removesuffix("_json")
            if key == "fallback_history_json":
                item[public_key] = json.loads(raw) if raw else []
            else:
                item[public_key] = json.loads(raw) if raw else None
        return item

    def record_route_decision(
        self,
        operation_id: str,
        *,
        lease_token: str,
        selected_route: str,
        router_policy_version: str,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a bounded route decision before an operation side effect begins."""

        selected_route = selected_route.strip()
        router_policy_version = router_policy_version.strip()
        if not selected_route or len(selected_route) > 200:
            raise ToolError("invalid_selected_route", "selected_route must contain between 1 and 200 characters.")
        if not router_policy_version or len(router_policy_version) > 100:
            raise ToolError(
                "invalid_router_policy_version",
                "router_policy_version must contain between 1 and 100 characters.",
            )
        redacted_decision = redact_inputs(decision)
        decision_json = _canonical(redacted_decision)
        if len(decision_json) > _ROUTE_JSON_MAX_CHARS:
            raise ToolError(
                "route_decision_too_large",
                f"Route decision exceeds the {_ROUTE_JSON_MAX_CHARS}-character persistence limit.",
            )

        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT workflow_id, step_index, state, execution_intent_json,
                       selected_route, router_policy_version, route_decision_json,
                       fallback_history_json
                FROM workflow_operations WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if row is None:
                raise ToolError("workflow_operation_not_found", "Workflow operation was not found.")
            if row["execution_intent_json"] is None:
                raise ToolError(
                    "workflow_execution_intent_missing",
                    "Route metadata can only be recorded for a step with an execution intent.",
                )
            if str(row["state"]) not in {
                OperationState.CREATED.value,
                OperationState.FAILED.value,
                OperationState.WAITING.value,
            }:
                raise ToolError(
                    "workflow_route_locked",
                    "Route selection is locked after execution begins or the operation becomes terminal/uncertain.",
                )

            workflow_id = str(row["workflow_id"])
            lease = connection.execute(
                "SELECT lease_token, expires_at FROM workflow_leases WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            if lease is None or str(lease["lease_token"]) != lease_token:
                raise ToolError("workflow_lease_token_mismatch", "Workflow lease token does not match the current owner.")
            if str(lease["expires_at"]) <= now:
                raise ToolError("workflow_lease_expired", "Workflow lease has expired.")

            try:
                history = list(json.loads(str(row["fallback_history_json"]))) if row["fallback_history_json"] else []
            except (TypeError, ValueError):
                history = []
            previous_route = str(row["selected_route"]) if row["selected_route"] else None
            if previous_route is not None and previous_route != selected_route:
                previous_decision = (
                    json.loads(str(row["route_decision_json"]))
                    if row["route_decision_json"]
                    else {}
                )
                history.append(
                    {
                        "selected_route": previous_route,
                        "router_policy_version": (
                            str(row["router_policy_version"])
                            if row["router_policy_version"]
                            else None
                        ),
                        "representative_tool": previous_decision.get("representative_tool"),
                        "fallback_level": previous_decision.get("fallback_level"),
                    }
                )
                history = history[-_ROUTE_HISTORY_MAX:]

            connection.execute(
                """
                UPDATE workflow_operations
                SET selected_route = ?, router_policy_version = ?, route_decision_json = ?,
                    fallback_history_json = ?, updated_at = ?, version = version + 1
                WHERE operation_id = ?
                """,
                (
                    selected_route,
                    router_policy_version,
                    decision_json,
                    _canonical(history),
                    now,
                    operation_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO workflow_events(
                    workflow_id, step_index, operation_id, event_type, state, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    int(row["step_index"]),
                    operation_id,
                    "route_changed" if previous_route and previous_route != selected_route else "route_selected",
                    str(row["state"]),
                    _canonical(
                        {
                            "selected_route": selected_route,
                            "router_policy_version": router_policy_version,
                            "fallback_count": len(history),
                        }
                    ),
                    now,
                ),
            )
            connection.commit()
        return self.get_operation(operation_id)

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
        return_operation: bool = True,
    ) -> dict[str, Any] | None:
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
        return self.get_operation(operation_id) if return_operation else None

    @staticmethod
    def _resource_claims_for_request(
        connection: sqlite3.Connection,
        request_id: str,
    ) -> tuple[ResourceClaim, ...]:
        rows = connection.execute(
            """
            SELECT resource_kind, resource_key, mode, scope
            FROM workflow_resource_claims
            WHERE request_id = ?
            ORDER BY resource_kind, resource_key, scope, mode
            """,
            (request_id,),
        ).fetchall()
        return tuple(
            ResourceClaim(
                str(row["resource_kind"]),
                str(row["resource_key"]),
                cast(Literal["read", "write"], str(row["mode"])),
                cast(Literal["exact", "subtree"], str(row["scope"])),
            )
            for row in rows
        )

    @staticmethod
    def _make_admission_room(
        connection: sqlite3.Connection,
        table: Literal["workflow_resource_requests", "runtime_capacity_requests"],
    ) -> None:
        from core.config import SETTINGS

        limit = SETTINGS.max_runtime_admission_records
        count = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        if count < limit:
            return
        needed = count - limit + 1
        removed = connection.execute(
            f"DELETE FROM {table} WHERE ticket IN ("
            f"SELECT ticket FROM {table} WHERE state='released' ORDER BY ticket LIMIT ?)",
            (needed,),
        ).rowcount
        if removed < needed:
            raise ToolError(
                "runtime_admission_storage_exhausted",
                "Admission record quota is exhausted; unresolved ownership must be reconciled before new work.",
            )

    def reserve_resource_claims(
        self,
        *,
        request_id: str,
        owner_token: str,
        claims: Sequence[ResourceClaim],
        category: str,
        workflow_id: str | None = None,
        operation_id: str | None = None,
        attempt_token: str | None = None,
    ) -> dict[str, Any]:
        """Persist one fair, all-or-none resource request and activate it when conflict-free."""

        request_id = request_id.strip()
        owner_token = owner_token.strip()
        category = category.strip()
        if not request_id or len(request_id) > 200:
            raise ToolError("invalid_resource_request", "Resource request id must contain 1..200 characters.")
        if not owner_token or len(owner_token) > 200:
            raise ToolError("invalid_resource_owner", "Resource owner token must contain 1..200 characters.")
        if not category or len(category) > 80:
            raise ToolError("invalid_resource_category", "Resource category must contain 1..80 characters.")
        canonical = canonical_claims(claims)
        if not canonical:
            raise ToolError("empty_resource_claims", "At least one resource claim is required.")
        if len(canonical) > 256:
            raise ToolError("too_many_resource_claims", "A resource request may contain at most 256 claims.")
        if sum(len(claim.key.encode("utf-8")) + len(claim.kind.encode("utf-8")) for claim in canonical) > 65_536:
            raise ToolError("resource_claims_too_large", "A resource claim set may contain at most 64 KiB of keys.")
        now = _now()

        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workflow_resource_requests WHERE request_id = ?", (request_id,),
            ).fetchone()
            if row is None:
                self._make_admission_room(connection, "workflow_resource_requests")
                connection.execute(
                    """
                    INSERT INTO workflow_resource_requests(
                        request_id, workflow_id, operation_id, attempt_token, owner_token,
                        category, state, reason, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?)
                    """,
                    (
                        request_id,
                        workflow_id,
                        operation_id,
                        attempt_token,
                        owner_token,
                        category,
                        now,
                        now,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO workflow_resource_claims(
                        request_id, resource_kind, resource_key, mode, scope
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [(request_id, claim.kind, claim.key, claim.mode, claim.scope) for claim in canonical],
                )
                row = connection.execute(
                    "SELECT * FROM workflow_resource_requests WHERE request_id = ?", (request_id,),
                ).fetchone()
            if row is None:  # pragma: no cover - guarded by insert/read above
                raise ToolError("resource_request_missing", "Resource request could not be persisted.")
            if str(row["owner_token"]) != owner_token or str(row["category"]) != category:
                raise ToolError("resource_request_conflict", "Resource request id is already owned by another request.")
            persisted_claims = self._resource_claims_for_request(connection, request_id)
            if persisted_claims != canonical:
                raise ToolError("resource_request_conflict", "Resource request id was reused with different claims.")
            state = str(row["state"])
            if state == "active":
                connection.commit()
                return {"admitted": True, "request_id": request_id, "state": state, "reason": None}
            if state == "quarantined":
                connection.commit()
                return {
                    "admitted": False,
                    "request_id": request_id,
                    "state": state,
                    "reason": "resource_quarantined",
                }
            if state == "released":
                connection.commit()
                return {
                    "admitted": False,
                    "request_id": request_id,
                    "state": state,
                    "reason": "resource_request_terminal",
                }

            current_ticket = int(row["ticket"])
            other_rows = connection.execute(
                """
                SELECT request_id, ticket, state
                FROM workflow_resource_requests
                WHERE request_id <> ? AND state IN ('pending', 'active', 'quarantined')
                ORDER BY ticket
                """,
                (request_id,),
            ).fetchall()
            conflict_reason: str | None = None
            for other in other_rows:
                other_state = str(other["state"])
                if other_state == "pending" and int(other["ticket"]) > current_ticket:
                    continue
                other_claims = self._resource_claims_for_request(connection, str(other["request_id"]))
                if not claim_sets_conflict(canonical, other_claims):
                    continue
                if other_state == "quarantined":
                    conflict_reason = "resource_quarantined"
                    break
                if other_state == "active":
                    conflict_reason = "resource_conflict"
                    break
                conflict_reason = "older_conflicting_request"
                break

            if conflict_reason is not None:
                connection.execute(
                    "UPDATE workflow_resource_requests SET reason = ?, updated_at = ? WHERE request_id = ?",
                    (conflict_reason, now, request_id),
                )
                connection.commit()
                return {
                    "admitted": False,
                    "request_id": request_id,
                    "state": "pending",
                    "reason": conflict_reason,
                }

            connection.execute(
                """
                UPDATE workflow_resource_requests
                SET state = 'active', reason = NULL, updated_at = ?
                WHERE request_id = ? AND state = 'pending'
                """,
                (now, request_id),
            )
            connection.commit()
        return {"admitted": True, "request_id": request_id, "state": "active", "reason": None}

    def resource_request(self, request_id: str, *, expected_owner_token: str | None = None) -> dict[str, Any]:
        if expected_owner_token is not None and native_resource_owner_alive(expected_owner_token) is False:
            raise ToolError("resource_capability_invalid", "Native resource ownership cannot be verified.")
        with closing(self._connect()) as connection:
            if expected_owner_token is None:
                row = connection.execute(
                    "SELECT * FROM workflow_resource_requests WHERE request_id = ?", (request_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    _RESOURCE_CAPABILITY_SQL,
                    (request_id, expected_owner_token, _now()),
                ).fetchone()
            if row is None:
                if expected_owner_token is not None:
                    raise ToolError("resource_capability_invalid", "Internal resource capability is no longer active.")
                raise ToolError("resource_request_not_found", "Resource request was not found.")
            claims = self._resource_claims_for_request(connection, request_id)
        return {**dict(row), "claims": [claim.public() for claim in claims]}

    def bind_resource_request_attempt(
        self,
        request_id: str,
        *,
        owner_token: str,
        attempt_token: str,
    ) -> dict[str, Any]:
        """Attach one active resource request to the exact durable workflow attempt."""

        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, owner_token, attempt_token FROM workflow_resource_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise ToolError("resource_request_not_found", "Resource request was not found.")
            if str(row["owner_token"]) != owner_token:
                raise ToolError("resource_owner_mismatch", "Resource request owner token does not match.")
            if str(row["state"]) != "active":
                raise ToolError("resource_request_not_active", "Resource request must be active before binding an attempt.")
            current_attempt = row["attempt_token"]
            if current_attempt is not None and str(current_attempt) != attempt_token:
                raise ToolError("resource_attempt_conflict", "Resource request is already bound to another attempt.")
            connection.execute(
                """
                UPDATE workflow_resource_requests
                SET attempt_token = ?, updated_at = ?
                WHERE request_id = ? AND owner_token = ? AND state = 'active'
                """,
                (attempt_token, now, request_id, owner_token),
            )
            connection.commit()
        return self.resource_request(request_id)

    def release_resource_request(self, request_id: str, *, owner_token: str) -> bool:
        """Release only an active request owned by the exact token; quarantine needs resolution."""

        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, owner_token FROM workflow_resource_requests WHERE request_id = ?", (request_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return False
            if str(row["owner_token"]) != owner_token:
                raise ToolError("resource_owner_mismatch", "Resource request owner token does not match.")
            if str(row["state"]) not in {"active", "pending"}:
                connection.commit()
                return False
            if connection.execute(
                "SELECT 1 FROM runtime_capacity_requests WHERE resource_request_id=? AND state!='released' LIMIT 1",
                (request_id,),
            ).fetchone() is not None:
                connection.execute(
                    "UPDATE workflow_resource_requests SET state='quarantined', "
                    "reason='child_capacity_cleanup_unresolved', updated_at=? WHERE request_id=?",
                    (now, request_id),
                )
                connection.commit()
                return False
            connection.execute(
                """
                UPDATE workflow_resource_requests
                SET state = 'released', reason = NULL, updated_at = ?
                WHERE request_id = ? AND owner_token = ? AND state IN ('active', 'pending')
                """,
                (now, request_id, owner_token),
            )
            connection.commit()
        return True

    def quarantine_resource_request(self, request_id: str, *, owner_token: str, reason: str) -> dict[str, Any]:
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, owner_token FROM workflow_resource_requests WHERE request_id = ?", (request_id,),
            ).fetchone()
            if row is None:
                raise ToolError("resource_request_not_found", "Resource request was not found.")
            if str(row["owner_token"]) != owner_token:
                raise ToolError("resource_owner_mismatch", "Resource request owner token does not match.")
            if str(row["state"]) == "released":
                raise ToolError("resource_request_terminal", "Released resource request cannot be quarantined.")
            connection.execute(
                """
                UPDATE workflow_resource_requests
                SET state = 'quarantined', reason = ?, updated_at = ?
                WHERE request_id = ? AND owner_token = ?
                """,
                (reason[:2_000], now, request_id, owner_token),
            )
            connection.commit()
        return self.resource_request(request_id)

    def resolve_quarantined_resource_request(
        self,
        request_id: str,
        *,
        owner_token: str,
        resolution: Literal["released"],
        evidence: str,
    ) -> dict[str, Any]:
        if not evidence.strip():
            raise ToolError("resource_resolution_evidence_required", "Resource resolution requires bounded operator evidence.")
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, owner_token FROM workflow_resource_requests WHERE request_id = ?", (request_id,),
            ).fetchone()
            if row is None:
                raise ToolError("resource_request_not_found", "Resource request was not found.")
            if str(row["owner_token"]) != owner_token:
                raise ToolError("resource_owner_mismatch", "Resource request owner token does not match.")
            if str(row["state"]) != "quarantined":
                raise ToolError("resource_request_not_quarantined", "Resource request is not quarantined.")
            if connection.execute(
                "SELECT 1 FROM runtime_capacity_requests WHERE resource_request_id=? AND state!='released' LIMIT 1",
                (request_id,),
            ).fetchone() is not None:
                raise ToolError("resource_children_unresolved", "Child process capacity must settle before resource resolution.")
            connection.execute(
                """
                UPDATE workflow_resource_requests
                SET state = ?, reason = 'resolved', resolved_evidence = ?, updated_at = ?
                WHERE request_id = ? AND owner_token = ? AND state = 'quarantined'
                """,
                (resolution, evidence[:2_000], now, request_id, owner_token),
            )
            connection.commit()
        return self.resource_request(request_id)

    def reserve_capacity(
        self,
        *,
        admission_id: str,
        category: str,
        owner_token: str,
        weight: int,
        limit: int,
        resource_request_id: str | None = None,
        resource_owner_token: str | None = None,
    ) -> dict[str, Any]:
        """Reserve shared weighted runtime capacity with FIFO pending fairness."""

        if weight < 1 or limit < 1 or weight > limit:
            raise ToolError("invalid_capacity_request", "Capacity weight/limit are invalid.")
        if not 1 <= len(admission_id) <= 200 or not 1 <= len(owner_token) <= 200 or not 1 <= len(category) <= 80:
            raise ToolError("invalid_capacity_request", "Capacity identifiers exceed their bounded lengths.")
        if resource_owner_token is not None and native_resource_owner_alive(resource_owner_token) is False:
            raise ToolError("resource_capability_invalid", "Native resource ownership cannot be verified.")
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if resource_request_id is not None and connection.execute(
                _RESOURCE_CAPABILITY_SQL,
                (resource_request_id, resource_owner_token, now),
            ).fetchone() is None:
                raise ToolError("resource_capability_invalid", "Parent resource ownership is no longer active.")
            row = connection.execute(
                "SELECT * FROM runtime_capacity_requests WHERE admission_id = ?", (admission_id,),
            ).fetchone()
            if row is None:
                self._make_admission_room(connection, "runtime_capacity_requests")
                connection.execute(
                    """
                    INSERT INTO runtime_capacity_requests(
                        admission_id, category, owner_token, weight, limit_snapshot,
                        state, reason, created_at, updated_at, resource_request_id
                    ) VALUES (?, ?, ?, ?, ?, 'pending', NULL, ?, ?, ?)
                    """,
                    (admission_id, category, owner_token, weight, limit, now, now, resource_request_id),
                )
                row = connection.execute(
                    "SELECT * FROM runtime_capacity_requests WHERE admission_id = ?", (admission_id,),
                ).fetchone()
            if row is None:  # pragma: no cover
                raise ToolError("capacity_request_missing", "Capacity request could not be persisted.")
            if (
                str(row["owner_token"]) != owner_token
                or str(row["category"]) != category
                or int(row["weight"]) != weight
                or row["resource_request_id"] != resource_request_id
            ):
                raise ToolError("capacity_request_conflict", "Capacity admission id was reused with different inputs.")
            state = str(row["state"])
            if state == "active":
                connection.commit()
                return {"admitted": True, "admission_id": admission_id, "state": state, "reason": None}
            if state in {"quarantined", "released"}:
                connection.commit()
                return {
                    "admitted": False,
                    "admission_id": admission_id,
                    "state": state,
                    "reason": "capacity_quarantined" if state == "quarantined" else "capacity_request_terminal",
                }
            ticket = int(row["ticket"])
            older_pending = connection.execute(
                """
                SELECT 1 FROM runtime_capacity_requests
                WHERE category = ? AND state = 'pending' AND ticket < ?
                ORDER BY ticket LIMIT 1
                """,
                (category, ticket),
            ).fetchone()
            active_weight = int(
                connection.execute(
                    """
                    SELECT COALESCE(SUM(weight), 0) FROM runtime_capacity_requests
                    WHERE category = ? AND state IN ('active', 'quarantined')
                    """,
                    (category,),
                ).fetchone()[0]
            )
            if older_pending is not None or active_weight + weight > limit:
                reason = "older_capacity_request" if older_pending is not None else "capacity_exhausted"
                connection.execute(
                    "UPDATE runtime_capacity_requests SET reason = ?, limit_snapshot = ?, updated_at = ? WHERE admission_id = ?",
                    (reason, limit, now, admission_id),
                )
                connection.commit()
                return {"admitted": False, "admission_id": admission_id, "state": "pending", "reason": reason}
            connection.execute(
                """
                UPDATE runtime_capacity_requests
                SET state = 'active', reason = NULL, limit_snapshot = ?, updated_at = ?
                WHERE admission_id = ? AND state = 'pending'
                """,
                (limit, now, admission_id),
            )
            connection.commit()
        return {"admitted": True, "admission_id": admission_id, "state": "active", "reason": None}

    def quarantine_capacity(self, admission_id: str, *, owner_token: str) -> None:
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner_token, state, resource_request_id FROM runtime_capacity_requests WHERE admission_id=?",
                (admission_id,),
            ).fetchone()
            if row is None or row["owner_token"] != owner_token or row["state"] == "released":
                raise ToolError("capacity_owner_mismatch", "Capacity ownership cannot be quarantined.")
            connection.execute(
                "UPDATE runtime_capacity_requests SET state='quarantined', reason='process_cleanup_unresolved', "
                "updated_at=? WHERE admission_id=?", (now, admission_id),
            )
            connection.execute(
                "UPDATE workflow_resource_requests SET state='quarantined', reason='child_capacity_cleanup_unresolved', "
                "updated_at=? WHERE request_id=? AND state='active'", (now, row["resource_request_id"]),
            )
            connection.commit()

    def release_capacity(self, admission_id: str, *, owner_token: str) -> bool:
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, owner_token FROM runtime_capacity_requests WHERE admission_id = ?", (admission_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return False
            if str(row["owner_token"]) != owner_token:
                raise ToolError("capacity_owner_mismatch", "Capacity owner token does not match.")
            if str(row["state"]) not in {"active", "pending"}:
                connection.commit()
                return False
            connection.execute(
                """
                UPDATE runtime_capacity_requests
                SET state = 'released', reason = NULL, updated_at = ?
                WHERE admission_id = ? AND owner_token = ? AND state IN ('active', 'pending')
                """,
                (now, admission_id, owner_token),
            )
            connection.commit()
        return True

    def release_process_capacity(self, pid: int, created: float) -> int:
        """Release permits only after the caller confirms owned process-tree cleanup."""

        if pid < 1 or created <= 0:
            raise ValueError("A positive process identity is required.")
        owner_prefix = f"pid-{pid}-{created:.6f}-%"
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = connection.execute(
                """
                UPDATE runtime_capacity_requests
                SET state = 'released', reason = 'owned_process_cleanup_complete', updated_at = ?
                WHERE owner_token LIKE ? AND state IN ('active', 'pending', 'quarantined')
                """,
                (_now(), owner_prefix),
            ).rowcount
            connection.commit()
        return count

    def claim_ready_operation(
        self,
        operation_id: str,
        *,
        lease_token: str,
        owner_id: str,
        execution_id: str,
        global_limit: int,
        category: str = "workflow_step",
        weight: int = 1,
    ) -> dict[str, Any] | None:
        """Atomically reserve one dependency-ready operation without dispatching it."""

        if global_limit < 1 or weight < 1:
            raise ToolError("invalid_workflow_admission_limit", "Workflow admission limits must be positive.")
        now = _now()
        attempt_token = uuid.uuid4().hex
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT * FROM workflow_operations WHERE operation_id = ?", (operation_id,),
            ).fetchone()
            if operation is None:
                raise ToolError("workflow_operation_not_found", "Workflow operation was not found.")
            workflow_id = str(operation["workflow_id"])
            workflow = connection.execute(
                "SELECT state, cancel_requested_at, max_parallel_steps FROM workflows WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            if workflow is None:
                raise ToolError("workflow_not_found", f"Workflow {workflow_id!r} was not found.")
            if str(workflow["state"]) not in {WorkflowState.RUNNING.value, WorkflowState.CANCELLING.value}:
                connection.commit()
                return None
            if workflow["cancel_requested_at"] or str(workflow["state"]) == WorkflowState.CANCELLING.value:
                connection.commit()
                return None
            lease = connection.execute(
                "SELECT lease_token, expires_at FROM workflow_leases WHERE workflow_id = ?", (workflow_id,),
            ).fetchone()
            if lease is None or str(lease["lease_token"]) != lease_token:
                raise ToolError("workflow_lease_token_mismatch", "Workflow lease token does not match the current owner.")
            if str(lease["expires_at"]) <= now:
                raise ToolError("workflow_lease_expired", "Workflow lease has expired.")
            if str(operation["state"]) not in {OperationState.CREATED.value, OperationState.FAILED.value}:
                connection.commit()
                return None
            if connection.execute(
                "SELECT 1 FROM workflow_execution_admissions WHERE operation_id = ?", (operation_id,),
            ).fetchone() is not None:
                connection.commit()
                return None

            parent_rows = connection.execute(
                """
                SELECT parent.state
                FROM workflow_step_dependencies dep
                JOIN workflow_operations parent
                  ON parent.workflow_id = dep.workflow_id
                 AND parent.step_index = dep.dependency_step_index
                 AND parent.operation_index = 0
                WHERE dep.workflow_id = ? AND dep.step_index = ?
                """,
                (workflow_id, int(operation["step_index"])),
            ).fetchall()
            if any(
                str(parent["state"]) not in {OperationState.SUCCEEDED.value, OperationState.ACKNOWLEDGED.value}
                for parent in parent_rows
            ):
                connection.commit()
                return None

            global_active = int(connection.execute(
                "SELECT COALESCE(SUM(weight), 0) FROM workflow_execution_admissions WHERE category = ?",
                (category,),
            ).fetchone()[0])
            workflow_active = int(connection.execute(
                "SELECT COALESCE(SUM(weight), 0) FROM workflow_execution_admissions WHERE workflow_id = ? AND category = ?",
                (workflow_id, category),
            ).fetchone()[0])
            workflow_limit = int(workflow["max_parallel_steps"] or 1)
            if global_active + weight > global_limit or workflow_active + weight > workflow_limit:
                connection.commit()
                return None

            connection.execute(
                """
                INSERT INTO workflow_execution_admissions(
                    operation_id, workflow_id, attempt_token, execution_id, lease_token,
                    owner_id, category, weight, admitted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    workflow_id,
                    attempt_token,
                    execution_id,
                    lease_token,
                    owner_id[:200],
                    category,
                    weight,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE workflow_operations
                SET attempt_token = ?, execution_id = ?, dispatch_phase = 'reserved',
                    attempt_generation = attempt_generation + 1, version = version + 1, updated_at = ?
                WHERE operation_id = ?
                """,
                (attempt_token, execution_id, now, operation_id),
            )
            connection.execute(
                """
                INSERT INTO workflow_events(
                    workflow_id, step_index, operation_id, event_type, state, metadata_json, created_at
                ) VALUES (?, ?, ?, 'operation_reserved', 'reserved', ?, ?)
                """,
                (
                    workflow_id,
                    int(operation["step_index"]),
                    operation_id,
                    _canonical({"execution_id": execution_id, "category": category, "weight": weight}),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM workflow_operations WHERE operation_id = ?", (operation_id,),
            ).fetchone()
            connection.commit()
        assert row is not None
        return self.get_operation(operation_id)

    def prepare_operation_attempt(
        self,
        operation_id: str,
        *,
        expected_version: int,
        attempt_token: str,
        lease_token: str,
        intent_evidence: dict[str, Any] | None,
        postcondition: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Persist prepared intent under the active fenced attempt."""

        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT * FROM workflow_operations WHERE operation_id = ?", (operation_id,),
            ).fetchone()
            if operation is None:
                raise ToolError("workflow_operation_not_found", "Workflow operation was not found.")
            workflow_id = str(operation["workflow_id"])
            if int(operation["version"]) != expected_version or str(operation["attempt_token"] or "") != attempt_token:
                raise ToolError("workflow_attempt_conflict", "Workflow operation attempt changed before preparation.")
            admission = connection.execute(
                "SELECT lease_token FROM workflow_execution_admissions WHERE operation_id = ? AND attempt_token = ?",
                (operation_id, attempt_token),
            ).fetchone()
            if admission is None or str(admission["lease_token"]) != lease_token:
                raise ToolError("workflow_attempt_conflict", "Workflow admission no longer owns this attempt.")
            self._require_current_lease_row(connection, workflow_id, lease_token, now)
            workflow = connection.execute(
                "SELECT cancel_requested_at, state FROM workflows WHERE workflow_id = ?", (workflow_id,),
            ).fetchone()
            if workflow is None or workflow["cancel_requested_at"] or str(workflow["state"]) == WorkflowState.CANCELLING.value:
                raise ToolError("workflow_cancelled_before_dispatch", "Workflow cancellation won before dispatch.")
            connection.execute(
                """
                UPDATE workflow_operations
                SET dispatch_phase = 'prepared', intent_evidence_json = ?,
                    postcondition_json = COALESCE(?, postcondition_json), version = version + 1, updated_at = ?
                WHERE operation_id = ? AND version = ? AND attempt_token = ?
                """,
                (
                    _canonical(intent_evidence) if intent_evidence is not None else None,
                    _canonical(postcondition) if postcondition is not None else None,
                    now,
                    operation_id,
                    expected_version,
                    attempt_token,
                ),
            )
            row = connection.execute("SELECT * FROM workflow_operations WHERE operation_id = ?", (operation_id,)).fetchone()
            connection.commit()
        assert row is not None
        return self.get_operation(operation_id)

    @staticmethod
    def _require_current_lease_row(
        connection: sqlite3.Connection,
        workflow_id: str,
        lease_token: str,
        now: str,
    ) -> sqlite3.Row:
        lease = connection.execute(
            "SELECT * FROM workflow_leases WHERE workflow_id = ?", (workflow_id,),
        ).fetchone()
        if lease is None or str(lease["lease_token"]) != lease_token:
            raise ToolError("workflow_lease_token_mismatch", "Workflow lease token does not match the current owner.")
        if str(lease["expires_at"]) <= now:
            raise ToolError("workflow_lease_expired", "Workflow lease has expired.")
        return lease

    def mark_operation_may_dispatch(
        self,
        operation_id: str,
        *,
        expected_version: int,
        attempt_token: str,
        lease_token: str,
    ) -> dict[str, Any]:
        """Cross the durable side-effect barrier for exactly one current attempt."""

        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT * FROM workflow_operations WHERE operation_id = ?", (operation_id,),
            ).fetchone()
            if operation is None:
                raise ToolError("workflow_operation_not_found", "Workflow operation was not found.")
            workflow_id = str(operation["workflow_id"])
            if int(operation["version"]) != expected_version or str(operation["attempt_token"] or "") != attempt_token:
                raise ToolError("workflow_attempt_conflict", "Workflow operation attempt changed before dispatch.")
            admission = connection.execute(
                "SELECT lease_token FROM workflow_execution_admissions WHERE operation_id = ? AND attempt_token = ?",
                (operation_id, attempt_token),
            ).fetchone()
            if admission is None or str(admission["lease_token"]) != lease_token:
                raise ToolError("workflow_attempt_conflict", "Workflow admission no longer owns this attempt.")
            self._require_current_lease_row(connection, workflow_id, lease_token, now)
            workflow = connection.execute(
                "SELECT cancel_requested_at, state FROM workflows WHERE workflow_id = ?", (workflow_id,),
            ).fetchone()
            if workflow is None or workflow["cancel_requested_at"] or str(workflow["state"]) == WorkflowState.CANCELLING.value:
                raise ToolError("workflow_cancelled_before_dispatch", "Workflow cancellation won before dispatch.")
            connection.execute(
                """
                UPDATE workflow_operations
                SET state = 'running', dispatch_phase = 'may_dispatch', attempts = attempts + 1,
                    version = version + 1, updated_at = ?, started_at = COALESCE(started_at, ?)
                WHERE operation_id = ? AND version = ? AND attempt_token = ?
                """,
                (now, now, operation_id, expected_version, attempt_token),
            )
            connection.execute(
                """
                UPDATE workflow_steps SET state = 'running', attempts = attempts + 1,
                    started_at = COALESCE(started_at, ?)
                WHERE workflow_id = ? AND step_index = ?
                """,
                (now, workflow_id, int(operation["step_index"])),
            )
            connection.execute(
                """
                INSERT INTO workflow_events(
                    workflow_id, step_index, operation_id, event_type, state, metadata_json, created_at
                ) VALUES (?, ?, ?, 'operation_may_dispatch', 'running', ?, ?)
                """,
                (workflow_id, int(operation["step_index"]), operation_id, _canonical({"attempt_token": attempt_token}), now),
            )
            row = connection.execute("SELECT * FROM workflow_operations WHERE operation_id = ?", (operation_id,)).fetchone()
            connection.commit()
        assert row is not None
        return self.get_operation(operation_id)

    def complete_operation_attempt(
        self,
        operation_id: str,
        *,
        expected_version: int,
        attempt_token: str,
        lease_token: str,
        final_state: OperationState,
        result: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Atomically settle operation, step, event and admission for one fenced attempt."""

        if final_state not in {
            OperationState.SUCCEEDED,
            OperationState.FAILED,
            OperationState.UNCERTAIN,
            OperationState.CANCELLED,
        }:
            raise ToolError("invalid_operation_resolution", "Unsupported workflow attempt result.")
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT * FROM workflow_operations WHERE operation_id = ?", (operation_id,),
            ).fetchone()
            if operation is None:
                raise ToolError("workflow_operation_not_found", "Workflow operation was not found.")
            workflow_id = str(operation["workflow_id"])
            if int(operation["version"]) != expected_version or str(operation["attempt_token"] or "") != attempt_token:
                raise ToolError("workflow_attempt_conflict", "Late workflow result does not own the current attempt.")
            admission = connection.execute(
                "SELECT lease_token FROM workflow_execution_admissions WHERE operation_id = ? AND attempt_token = ?",
                (operation_id, attempt_token),
            ).fetchone()
            if admission is None or str(admission["lease_token"]) != lease_token:
                raise ToolError("workflow_attempt_conflict", "Late workflow result no longer owns admission.")
            self._require_current_lease_row(connection, workflow_id, lease_token, now)
            current_operation_state = OperationState(str(operation["state"]))
            dispatch_phase = str(operation["dispatch_phase"] or "")
            if not (
                current_operation_state is OperationState.CREATED
                and dispatch_phase in {"reserved", "prepared"}
                and final_state in {OperationState.FAILED, OperationState.CANCELLED}
            ):
                validate_operation_transition(current_operation_state, final_state)
            if connection.execute(
                """
                SELECT 1 FROM workflow_resource_requests r
                WHERE r.operation_id=? AND r.attempt_token=?
                  AND (r.state='quarantined' OR EXISTS (
                      SELECT 1 FROM runtime_capacity_requests c
                      WHERE c.resource_request_id=r.request_id AND c.state!='released'
                  )) LIMIT 1
                """,
                (operation_id, attempt_token),
            ).fetchone() is not None:
                final_state = OperationState.UNCERTAIN
                error = "workflow_child_ownership_unresolved"
                evidence = {**(evidence or {}), "conclusive": False, "satisfied": False, "ownership_unresolved": True}
            step_state = {
                OperationState.SUCCEEDED: "completed",
                OperationState.FAILED: "failed",
                OperationState.UNCERTAIN: "uncertain",
                OperationState.CANCELLED: "cancelled",
            }[final_state]
            connection.execute(
                """
                UPDATE workflow_operations
                SET state = ?, dispatch_phase = 'settled', version = version + 1,
                    result_json = ?, evidence_json = ?, error = ?, updated_at = ?, finished_at = ?
                WHERE operation_id = ? AND version = ? AND attempt_token = ?
                """,
                (
                    final_state.value,
                    _canonical(redact_inputs(result)) if result is not None else None,
                    _canonical(redact_inputs(evidence)) if evidence is not None else None,
                    error[:2_000] if error else None,
                    now,
                    now,
                    operation_id,
                    expected_version,
                    attempt_token,
                ),
            )
            connection.execute(
                """
                UPDATE workflow_steps SET state = ?, evidence_json = ?, error = ?, finished_at = ?
                WHERE workflow_id = ? AND step_index = ?
                """,
                (
                    step_state,
                    _canonical(redact_inputs(evidence)) if evidence is not None else None,
                    error[:2_000] if error else None,
                    now,
                    workflow_id,
                    int(operation["step_index"]),
                ),
            )
            connection.execute(
                """
                INSERT INTO workflow_events(
                    workflow_id, step_index, operation_id, event_type, state, metadata_json, created_at
                ) VALUES (?, ?, ?, 'operation_settled', ?, ?, ?)
                """,
                (
                    workflow_id,
                    int(operation["step_index"]),
                    operation_id,
                    final_state.value,
                    _canonical({"attempt_token": attempt_token}),
                    now,
                ),
            )
            connection.execute(
                "DELETE FROM workflow_execution_admissions WHERE operation_id = ? AND attempt_token = ?",
                (operation_id, attempt_token),
            )
            row = connection.execute("SELECT * FROM workflow_operations WHERE operation_id = ?", (operation_id,)).fetchone()
            connection.commit()
        assert row is not None
        return self.get_operation(operation_id)

    def recompute_workflow_aggregate(
        self,
        workflow_id: str,
        *,
        lease_token: str,
        stop_admission_reason: str | None = None,
        keep_running: bool = False,
    ) -> dict[str, Any]:
        """Derive DAG aggregate state from canonical operation/admission facts."""

        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            workflow = connection.execute(
                "SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,),
            ).fetchone()
            if workflow is None:
                raise ToolError("workflow_not_found", f"Workflow {workflow_id!r} was not found.")
            self._require_current_lease_row(connection, workflow_id, lease_token, now)
            operations = connection.execute(
                """
                SELECT step_index, state, error FROM workflow_operations
                WHERE workflow_id = ? AND operation_index = 0
                ORDER BY step_index
                """,
                (workflow_id,),
            ).fetchall()
            active_count = int(connection.execute(
                "SELECT COUNT(*) FROM workflow_execution_admissions WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()[0])
            states = [str(operation["state"]) for operation in operations]
            unresolved = any(state in {OperationState.UNCERTAIN.value, OperationState.RECONCILING.value} for state in states)
            failed = any(state in {OperationState.FAILED.value, OperationState.UNRESOLVABLE.value} for state in states)
            cancelled = bool(workflow["cancel_requested_at"]) or any(state == OperationState.CANCELLED.value for state in states)
            all_success = bool(operations) and all(
                state in {OperationState.SUCCEEDED.value, OperationState.ACKNOWLEDGED.value} for state in states
            )
            unfinished_indices = [
                int(operation["step_index"])
                for operation in operations
                if str(operation["state"]) not in {OperationState.SUCCEEDED.value, OperationState.ACKNOWLEDGED.value}
            ]
            current_step = min(unfinished_indices) if unfinished_indices else len(operations)

            if active_count > 0:
                target = WorkflowState.CANCELLING if cancelled else WorkflowState.RUNNING
            elif unresolved:
                target = WorkflowState.UNCERTAIN
            elif failed:
                target = WorkflowState.FAILED
            elif cancelled:
                target = WorkflowState.CANCELLED
            elif all_success:
                target = WorkflowState.COMPLETED
            elif keep_running:
                target = WorkflowState.RUNNING
            else:
                target = WorkflowState.PAUSED

            primary_error = None
            if failed:
                primary = next(
                    (
                        operation
                        for operation in operations
                        if str(operation["state"]) in {OperationState.FAILED.value, OperationState.UNRESOLVABLE.value}
                    ),
                    None,
                )
                if primary is not None:
                    primary_error = str(primary["error"] or "workflow operation failed")[:2_000]
            elif unresolved:
                primary_error = "workflow has unresolved operation outcome"
            elif stop_admission_reason:
                primary_error = stop_admission_reason[:2_000]

            connection.execute(
                """
                UPDATE workflows
                SET state = ?, current_step = ?, version = version + 1, updated_at = ?, last_error = ?
                WHERE workflow_id = ?
                """,
                (target.value, current_step, now, primary_error, workflow_id),
            )
            connection.execute(
                """
                INSERT INTO workflow_events(workflow_id, step_index, operation_id, event_type, state, metadata_json, created_at)
                VALUES (?, NULL, NULL, 'workflow_aggregate_recomputed', ?, ?, ?)
                """,
                (
                    workflow_id,
                    target.value,
                    _canonical({"active_count": active_count, "stop_admission_reason": stop_admission_reason}),
                    now,
                ),
            )
            connection.commit()
        return self.get(workflow_id)

    def active_admissions(self, workflow_id: str | None = None, *, category: str | None = None) -> list[dict[str, Any]]:
        filters: list[str] = []
        parameters: list[Any] = []
        if workflow_id is not None:
            filters.append("workflow_id = ?")
            parameters.append(workflow_id)
        if category is not None:
            filters.append("category = ?")
            parameters.append(category)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM workflow_execution_admissions {where} ORDER BY admitted_at, operation_id",
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def acquire_lease(
        self,
        workflow_id: str,
        owner_id: str,
        ttl_sec: float,
        *,
        allow_reentry: bool = True,
    ) -> WorkflowLease:
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
                if str(current["owner_id"]) != owner_id or not allow_reentry:
                    raise ToolError("workflow_already_claimed", "Workflow is already claimed by another execution.")
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
        with closing(self._connect()) as connection:
            native_rows = connection.execute(
                "SELECT request_id,owner_token FROM workflow_resource_requests "
                "WHERE workflow_id IS NULL AND state IN ('active','pending') AND owner_token LIKE 'native-owner-%'",
            ).fetchall()
        owner_snapshots: dict[str, bool | None] = {}
        stale_native: list[tuple[str, str]] = []
        for native in native_rows:
            owner = str(native["owner_token"])
            identity = owner.rsplit("-", 1)[0]
            if identity not in owner_snapshots:
                owner_snapshots[identity] = native_resource_owner_alive(owner)
            if owner_snapshots[identity] is False:
                stale_native.append((str(native["request_id"]), owner))
        native_count = 0
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for request_id, owner in stale_native:
                native_count += connection.execute(
                    "UPDATE workflow_resource_requests SET "
                    "state=CASE WHEN state='pending' THEN 'released' ELSE 'quarantined' END, "
                    "reason='native_owner_identity_unavailable', updated_at=? "
                    "WHERE request_id=? AND owner_token=? AND workflow_id IS NULL AND state IN ('active','pending')",
                    (now, request_id, owner),
                ).rowcount
            rows = connection.execute(
                """
                SELECT workflows.workflow_id, workflows.current_step, workflows.state,
                       workflows.cancel_requested_at
                FROM workflows
                LEFT JOIN workflow_leases ON workflow_leases.workflow_id = workflows.workflow_id
                WHERE workflows.state IN ('running','cancelling')
                  AND (workflow_leases.workflow_id IS NULL OR workflow_leases.expires_at <= ?)
                """,
                (now,),
            ).fetchall()
            for row in rows:
                workflow_id = str(row["workflow_id"])
                step_index = int(row["current_step"])
                workflow_state = str(row["state"])
                connection.execute(
                    """
                    UPDATE workflow_resource_requests
                    SET state = 'quarantined', reason = ?, updated_at = ?
                    WHERE workflow_id = ? AND state = 'active'
                    """,
                    ("workflow_lease_lost_before_resource_reconciliation", now, workflow_id),
                )
                connection.execute(
                    """
                    UPDATE workflow_resource_requests
                    SET state = 'released', reason = 'recovery_cancelled_pending', updated_at = ?
                    WHERE workflow_id = ? AND state = 'pending'
                    """,
                    (now, workflow_id),
                )
                operation = connection.execute(
                    """
                    SELECT operation_id, state, attempts, started_at, evidence_json, error
                    FROM workflow_operations
                    WHERE workflow_id = ? AND step_index = ? AND operation_index = 0
                    """,
                    (workflow_id, step_index),
                ).fetchone()
                step_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM workflow_steps WHERE workflow_id = ?",
                        (workflow_id,),
                    ).fetchone()[0]
                )

                if operation is not None and str(operation["state"]) == OperationState.SUCCEEDED.value:
                    next_step = step_index + 1
                    target = (
                        WorkflowState.CANCELLED.value
                        if workflow_state == WorkflowState.CANCELLING.value or row["cancel_requested_at"] is not None
                        else WorkflowState.COMPLETED.value if next_step >= step_count
                        else WorkflowState.PAUSED.value
                    )
                    connection.execute(
                        """
                        UPDATE workflow_steps
                        SET state = 'completed', finished_at = COALESCE(finished_at, ?),
                            evidence_json = COALESCE(evidence_json, ?), error = NULL
                        WHERE workflow_id = ? AND step_index = ?
                        """,
                        (now, operation["evidence_json"], workflow_id, step_index),
                    )
                    connection.execute(
                        """
                        UPDATE workflows
                        SET state = ?, current_step = ?, version = version + 1,
                            updated_at = ?, last_error = NULL
                        WHERE workflow_id = ?
                        """,
                        (target, next_step, now, workflow_id),
                    )
                elif operation is not None and str(operation["state"]) in {
                    OperationState.FAILED.value,
                    OperationState.UNRESOLVABLE.value,
                }:
                    error = str(operation["error"] or "operation_failed_before_aggregate_checkpoint")
                    connection.execute(
                        """
                        UPDATE workflow_steps
                        SET state = 'failed', finished_at = COALESCE(finished_at, ?),
                            error = COALESCE(error, ?)
                        WHERE workflow_id = ? AND step_index = ?
                        """,
                        (now, error[:2_000], workflow_id, step_index),
                    )
                    connection.execute(
                        """
                        UPDATE workflows
                        SET state = 'failed', version = version + 1, updated_at = ?,
                            last_error = ?
                        WHERE workflow_id = ?
                        """,
                        (now, error[:2_000], workflow_id),
                    )
                elif operation is not None and str(operation["state"]) == OperationState.CANCELLED.value:
                    connection.execute(
                        """
                        UPDATE workflow_steps
                        SET state = 'cancelled', finished_at = COALESCE(finished_at, ?),
                            error = COALESCE(error, 'workflow cancellation requested')
                        WHERE workflow_id = ? AND step_index = ?
                        """,
                        (now, workflow_id, step_index),
                    )
                    connection.execute(
                        """
                        UPDATE workflows
                        SET state = 'cancelled', version = version + 1, updated_at = ?
                        WHERE workflow_id = ?
                        """,
                        (now, workflow_id),
                    )
                elif (
                    workflow_state == WorkflowState.RUNNING.value
                    and row["cancel_requested_at"] is None
                    and operation is not None
                    and str(operation["state"]) == OperationState.CREATED.value
                    and int(operation["attempts"]) == 0
                    and operation["started_at"] is None
                    and connection.execute(
                        "SELECT 1 FROM workflow_steps WHERE workflow_id = ? AND step_index = ? "
                        "AND state = 'created' AND attempts = 0 AND started_at IS NULL",
                        (workflow_id, step_index),
                    ).fetchone() is not None
                ):
                    # The executor checkpoints the operation before running an action.
                    # A created step with no attempt can be queued again safely.
                    connection.execute(
                        "UPDATE workflows SET state = 'paused', version = version + 1, updated_at = ? "
                        "WHERE workflow_id = ?",
                        (now, workflow_id),
                    )
                else:
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
                        WHERE workflow_id = ? AND step_index = ?
                          AND state IN ('created','running','waiting')
                        """,
                        (now, "runtime_ended_without_checkpoint", workflow_id, step_index),
                    )
                    if operation is not None and str(operation["state"]) in {
                        OperationState.CREATED.value,
                        OperationState.RUNNING.value,
                        OperationState.WAITING.value,
                    }:
                        connection.execute(
                            """
                            UPDATE workflow_operations SET state = 'uncertain', version = version + 1,
                                updated_at = ?, finished_at = ?, error = ?
                            WHERE operation_id = ?
                            """,
                            (now, now, "runtime_ended_without_checkpoint", operation["operation_id"]),
                        )

                connection.execute(
                    "DELETE FROM workflow_leases WHERE workflow_id = ? AND expires_at <= ?",
                    (workflow_id, now),
                )
                connection.execute(
                    """
                    INSERT INTO workflow_events(
                        workflow_id, step_index, operation_id, event_type, state, metadata_json, created_at
                    ) VALUES (?, ?, ?, 'workflow_restart_recovered',
                              (SELECT state FROM workflows WHERE workflow_id = ?), ?, ?)
                    """,
                    (
                        workflow_id,
                        step_index,
                        operation["operation_id"] if operation is not None else None,
                        workflow_id,
                        _canonical({"previous_state": workflow_state}),
                        now,
                    ),
                )
            connection.commit()
        return len(rows) + native_count

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
                ORDER BY created_at ASC, rowid ASC
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
            resource_request_counts = {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    "SELECT state, COUNT(*) AS count FROM workflow_resource_requests GROUP BY state ORDER BY state"
                ).fetchall()
            }
            quarantined_resources = connection.execute(
                """
                SELECT category, state, reason, created_at, updated_at
                FROM workflow_resource_requests
                WHERE state = 'quarantined'
                ORDER BY updated_at ASC LIMIT ?
                """,
                (bounded,),
            ).fetchall()
            capacity_counts = {
                f"{row['category']}:{row['state']}": int(row["count"])
                for row in connection.execute(
                    """
                    SELECT category, state, COUNT(*) AS count
                    FROM runtime_capacity_requests
                    GROUP BY category, state
                    ORDER BY category, state
                    """
                ).fetchall()
            }
            analyzer_active_weight = int(
                connection.execute(
                    """
                    SELECT COALESCE(SUM(weight), 0)
                    FROM runtime_capacity_requests
                    WHERE category = 'analyzer' AND state IN ('active','quarantined')
                    """
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
        operation_total = sum(operation_counts.values())
        workflow_total = sum(workflow_counts.values())
        return {
            "workflow_counts": workflow_counts,
            "workflow_total": workflow_total,
            "operation_counts": operation_counts,
            "operation_total": operation_total,
            "workflow_operation_total": operation_total,
            "event_total": event_total,
            "workflow_event_total": event_total,
            "workflow_db_bytes": db_bytes,
            "queued_workflows": workflow_counts.get("queued", 0),
            "running_workflows": workflow_counts.get("running", 0) + workflow_counts.get("cancelling", 0),
            "uncertain_workflows": workflow_counts.get("uncertain", 0) + workflow_counts.get("reconciling", 0),
            "unresolved_operation_count": unresolved_total,
            "unresolved_workflow_operations": unresolved_total,
            "oldest_unresolved_operations": [dict(row) for row in unresolved],
            "active_lease_count": lease_count,
            "active_workflow_leases": lease_count,
            "resource_request_counts": resource_request_counts,
            "active_resource_request_count": resource_request_counts.get("active", 0),
            "pending_resource_request_count": resource_request_counts.get("pending", 0),
            "quarantined_resource_request_count": resource_request_counts.get("quarantined", 0),
            "oldest_quarantined_resources": [dict(row) for row in quarantined_resources],
            "runtime_capacity_counts": capacity_counts,
            "active_analyzer_capacity_weight": analyzer_active_weight,
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
