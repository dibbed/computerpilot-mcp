from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from core.errors import ToolError
from core.workflow_models import OperationState
from core.workflow_reconciliation import reconcile_operation
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowExecutor, WorkflowState, WorkflowStore


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        "operations",
        (
            StepDefinition(
                "first",
                "run_durable_job",
                {"executable": "python", "idempotency_key": "secret-one"},
                postcondition={"kind": "job_request_key_intent"},
            ),
            StepDefinition(
                "second",
                "run_durable_job",
                {"executable": "python", "idempotency_key": "secret-two"},
                postcondition={"kind": "job_request_key_intent"},
            ),
        ),
    )


def test_direct_store_operations_beyond_first_page_are_retrievable_and_executable(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition(
            "many-steps",
            tuple(StepDefinition(f"check-{index}", "check_file", {"path": str(tmp_path)}) for index in range(101)),
        ),
        initial_state=WorkflowState.QUEUED,
    )
    workflow_id = str(workflow["workflow_id"])
    last = store.list_operations(workflow_id, offset=100, limit=1)["items"][0]

    assert store.get_operation(last["operation_id"])["step_index"] == 100
    result = WorkflowExecutor(store).execute(workflow_id, owner_id="many-steps-test")
    assert result["state"] == "completed"
    assert result["current_step"] == 101


def test_create_materializes_one_operation_per_legacy_step(tmp_path: Path) -> None:
    path = tmp_path / "workflows.db"
    store = WorkflowStore(path)
    workflow = store.create(_definition())

    result = store.list_operations(workflow["workflow_id"])

    assert result["total_count"] == 2
    assert [item["operation_index"] for item in result["items"]] == [0, 0]
    assert [item["step_index"] for item in result["items"]] == [0, 1]
    assert all(item["state"] == "created" for item in result["items"])
    assert all(len(item["definition_hash"]) == 64 for item in result["items"])
    assert all(len(item["arguments_fingerprint"]) == 64 for item in result["items"])
    with sqlite3.connect(path) as connection:
        metadata = [json.loads(row[0]) for row in connection.execute("SELECT metadata_json FROM workflow_events")]
    assert all(item == {"legacy": False} for item in metadata)


def test_operation_listing_is_paginated_and_redacted(tmp_path: Path) -> None:
    path = tmp_path / "workflows.db"
    store = WorkflowStore(path)
    workflow = store.create(_definition())

    page = store.list_operations(workflow["workflow_id"], offset=0, limit=1)

    assert page["count"] == 1
    assert page["total_count"] == 2
    assert page["has_more"] is True
    assert "arguments" not in page["items"][0]
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(workflow_operations)")}
        workflow_columns = {row[1] for row in connection.execute("PRAGMA table_info(workflows)")}
        execution_definition = connection.execute(
            "SELECT execution_definition_json FROM workflows WHERE workflow_id = ?",
            (workflow["workflow_id"],),
        ).fetchone()[0]
    assert "arguments_json" not in columns
    assert "execution_definition_json" in workflow_columns
    assert "secret-one" in execution_definition
    assert "secret-two" in execution_definition


def test_checkpoint_can_skip_unneeded_post_commit_operation_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(_definition(), initial_state=WorkflowState.QUEUED)
    workflow_id = str(workflow["workflow_id"])
    operation_id = store.list_operations(workflow_id)["items"][0]["operation_id"]
    lease = store.acquire_lease(workflow_id, "worker", 30)
    selects: list[str] = []
    connect = store._connect

    def traced() -> sqlite3.Connection:
        connection = connect()
        connection.set_trace_callback(lambda sql: selects.append(sql) if sql.startswith("SELECT") else None)
        return connection

    monkeypatch.setattr(store, "_connect", traced)
    result = store.checkpoint_operation(
        operation_id, OperationState.RUNNING, lease_token=lease.lease_token,
        increment_attempt=True, return_operation=False,
    )

    assert result is None
    assert len(selects) == 2  # Operation state and lease ownership only.


def test_materialization_validation_can_skip_unused_operation_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(_definition())
    selects: list[str] = []
    connect = store._connect

    def traced() -> sqlite3.Connection:
        connection = connect()
        connection.set_trace_callback(lambda sql: selects.append(sql) if sql.startswith("SELECT") else None)
        return connection

    monkeypatch.setattr(store, "_connect", traced)
    assert store.materialize_operations(workflow["workflow_id"], return_operations=False) is None
    assert not any("COUNT(*) FROM workflow_operations" in sql for sql in selects)


def test_materialization_detects_definition_conflict(tmp_path: Path) -> None:
    path = tmp_path / "workflows.db"
    store = WorkflowStore(path)
    workflow = store.create(_definition())
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE workflow_operations SET definition_hash = ? WHERE workflow_id = ? AND step_index = 0",
            ("0" * 64, workflow["workflow_id"]),
        )

    with pytest.raises(ToolError, match="definition"):
        store.materialize_operations(workflow["workflow_id"])


def test_execution_rejects_corrupt_materialized_operation_after_backfill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.workflow_actions import ACTION_HANDLERS

    path = tmp_path / "workflows.db"
    store = WorkflowStore(path)
    workflow = store.create(
        WorkflowDefinition("check", (StepDefinition("check", "check_file", {"path": str(tmp_path)}),)),
        initial_state=WorkflowState.QUEUED,
    )
    workflow_id = str(workflow["workflow_id"])
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE workflow_operations SET definition_hash = ? WHERE workflow_id = ?",
            ("0" * 64, workflow_id),
        )
    monkeypatch.setitem(ACTION_HANDLERS, "check_file", lambda *args: pytest.fail("Corrupt workflow executed"))

    reopened = WorkflowStore(path)
    with pytest.raises(ToolError, match="definition"):
        WorkflowExecutor(reopened).execute(workflow_id, owner_id="integrity-test")
    assert reopened.get(workflow_id)["state"] == "queued"


def test_reconciliation_rejects_corrupt_materialized_operation_after_backfill(tmp_path: Path) -> None:
    path = tmp_path / "workflows.db"
    store = WorkflowStore(path)
    workflow = store.create(_definition())
    operation = store.list_operations(workflow["workflow_id"])["items"][0]
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE workflow_operations SET state = 'uncertain', definition_hash = ? WHERE operation_id = ?",
            ("0" * 64, operation["operation_id"]),
        )

    reopened = WorkflowStore(path)
    with pytest.raises(ToolError, match="definition"):
        reconcile_operation(reopened, operation["operation_id"], expected_version=operation["version"])
