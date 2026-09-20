from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.errors import ToolError
from core.workflow_actions import ACTION_HANDLERS, SideEffectUncertain
from core.workflow_reconciliation import acknowledge_operation, reconcile_operation
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowExecutor, WorkflowState, WorkflowStore


def _uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    postcondition: dict[str, object],
) -> tuple[WorkflowStore, dict[str, Any]]:
    store = WorkflowStore(tmp_path / "workflows.db")

    def interrupted(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        del arguments, timeout
        raise SideEffectUncertain("lost result")

    monkeypatch.setitem(ACTION_HANDLERS, "check_file", interrupted)
    created = store.create(
        WorkflowDefinition("uncertain", (StepDefinition("effect", "check_file", {"path": "unused"}, postcondition=postcondition),)),
        initial_state=WorkflowState.QUEUED,
    )
    WorkflowExecutor(store).execute(created["workflow_id"], owner_id="test")
    return store, store.list_operations(created["workflow_id"])["items"][0]


def test_reconciliation_satisfied_completes_aggregate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "done.txt"
    target.write_text("done", encoding="utf-8")
    store, operation = _uncertain(
        tmp_path, monkeypatch, {"kind": "file_exists", "expected": {"path": str(target)}},
    )

    result = reconcile_operation(store, operation["operation_id"], expected_version=operation["version"])

    assert result["operation"]["state"] == "succeeded"
    assert result["workflow"]["state"] == "completed"


def test_reconciliation_unsatisfied_marks_failed_and_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, operation = _uncertain(
        tmp_path, monkeypatch, {"kind": "file_exists", "expected": {"path": str(tmp_path / "missing")}},
    )

    result = reconcile_operation(store, operation["operation_id"], expected_version=operation["version"])

    assert result["operation"]["state"] == "failed"
    assert result["workflow"]["state"] == "failed"


def test_inconclusive_reconciliation_stays_uncertain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, operation = _uncertain(
        tmp_path, monkeypatch, {"kind": "browser_state", "expected": {"session_id": "missing"}},
    )

    result = reconcile_operation(store, operation["operation_id"], expected_version=operation["version"])

    assert result["operation"]["state"] == "uncertain"
    assert result["workflow"]["state"] == "uncertain"


def test_manual_acknowledgement_is_cas_guarded_and_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, operation = _uncertain(tmp_path, monkeypatch, {"kind": "file_exists", "expected": {"path": "unused"}})
    with pytest.raises(ToolError, match="reason and actor"):
        acknowledge_operation(
            store, operation["operation_id"], expected_version=operation["version"],
            resolution="resolved_completed", reason="", actor="operator",
        )

    result = acknowledge_operation(
        store, operation["operation_id"], expected_version=operation["version"],
        resolution="resolved_completed", reason="verified externally", actor="operator",
    )
    assert result["operation"]["state"] == "acknowledged"
    assert result["operation"]["evidence"]["source"] == "operator_assertion"
    with pytest.raises(ToolError, match="Only an uncertain operation"):
        acknowledge_operation(
            store, operation["operation_id"], expected_version=result["operation"]["version"],
            resolution="resolved_failed", reason="changed", actor="operator",
        )
