from __future__ import annotations

from pathlib import Path

import pytest

from core.workflow_actions import ACTION_HANDLERS
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowExecutor, WorkflowState, WorkflowStore


def test_executor_checkpoints_operation_before_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    seen: list[str] = []

    def inspect(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        del arguments, timeout
        seen.append(store.list_operations(workflow["workflow_id"])["items"][0]["state"])
        return {"ok": True}

    monkeypatch.setitem(ACTION_HANDLERS, "check_file", inspect)
    workflow = store.create(
        WorkflowDefinition("execute", (StepDefinition("check", "check_file", {"path": "unused"}),)),
        initial_state=WorkflowState.QUEUED,
    )

    result = WorkflowExecutor(store).execute(workflow["workflow_id"], owner_id="test-owner")

    assert seen == ["running"]
    assert result["state"] == "completed"
    assert store.list_operations(workflow["workflow_id"])["items"][0]["state"] == "succeeded"


def test_completed_operation_is_not_replayed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    calls = 0

    def count(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        nonlocal calls
        del arguments, timeout
        calls += 1
        return {"ok": True}

    monkeypatch.setitem(ACTION_HANDLERS, "check_file", count)
    workflow = store.create(
        WorkflowDefinition("once", (StepDefinition("check", "check_file", {"path": "unused"}),)),
        initial_state=WorkflowState.QUEUED,
    )
    executor = WorkflowExecutor(store)
    executor.execute(workflow["workflow_id"], owner_id="test-owner")

    result = executor.execute(workflow["workflow_id"], owner_id="test-owner")

    assert result["state"] == "completed"
    assert calls == 1
