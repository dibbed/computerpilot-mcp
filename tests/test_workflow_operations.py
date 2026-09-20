from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from core.errors import ToolError
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowStore


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        "operations",
        (
            StepDefinition(
                "first",
                "run_durable_job",
                {"executable": "python", "idempotency_key": "secret-one"},
                postcondition={"kind": "job_succeeded_from_result"},
            ),
            StepDefinition(
                "second",
                "run_durable_job",
                {"executable": "python", "idempotency_key": "secret-two"},
                postcondition={"kind": "job_succeeded_from_result"},
            ),
        ),
    )


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
        serialized = " ".join(
            str(value)
            for table in ("workflows", "workflow_operations", "workflow_events")
            for row in connection.execute(f"SELECT * FROM {table}")
            for value in row
            if value is not None
        )
    assert "arguments_json" not in columns
    assert "secret" not in serialized


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
