from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.errors import ToolError
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowState, WorkflowStore


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition("verify", (StepDefinition("test", "verify_changes", {"cwd": "C:/repo"}),))


def test_create_is_idempotent_and_redacts_inputs(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    first = store.create(_definition(), {"api_token": "secret", "branch": "main"}, idempotency_key="same")
    second = store.create(_definition(), {"api_token": "different"}, idempotency_key="same")
    assert first["workflow_id"] == second["workflow_id"]
    assert second["inputs"]["api_token"] == "<redacted>"


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
