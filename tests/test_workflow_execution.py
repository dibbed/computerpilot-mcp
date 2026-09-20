from __future__ import annotations

from pathlib import Path

import pytest

from core.errors import ToolError
from core.workflow_actions import ACTION_HANDLERS, SideEffectUncertain, TransientActionError, execute_action
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowExecutor, WorkflowState, WorkflowStore


def test_unknown_action_is_rejected() -> None:
    with pytest.raises(ToolError, match="not allowlisted"):
        execute_action("shell", {"command": "whoami"}, 1)


def test_executor_checkpoints_success(tmp_path: Path) -> None:
    target = tmp_path / "ready.txt"
    target.write_text("ready", encoding="utf-8")
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition("check", (StepDefinition("file", "check_file", {"path": str(target)}),)),
        initial_state=WorkflowState.QUEUED,
    )
    result = WorkflowExecutor(store).run(workflow["workflow_id"])
    assert result["state"] == "completed"
    assert result["steps"][0]["attempts"] == 1
    assert result["steps"][0]["evidence"]["postcondition"]["conclusive"] is True


def test_dry_run_does_not_advance_state(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition("check", (StepDefinition("file", "check_file", {"path": "missing"}),)),
        initial_state=WorkflowState.QUEUED,
    )
    result = WorkflowExecutor(store).run(workflow["workflow_id"], dry_run=True)
    assert result["dry_run"] is True
    assert store.get(workflow["workflow_id"])["state"] == "queued"


def test_transient_retry_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def flaky(arguments, timeout):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientActionError("temporary")
        return {"ok": True}

    monkeypatch.setitem(ACTION_HANDLERS, "check_file", flaky)
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition("retry", (StepDefinition("check", "check_file", {"path": "unused"}, max_retries=1),)),
        initial_state=WorkflowState.QUEUED,
    )
    result = WorkflowExecutor(store).run(workflow["workflow_id"])
    assert result["state"] == "completed"
    assert calls == 2


def test_uncertain_side_effect_is_never_advanced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def uncertain(arguments, timeout):  # type: ignore[no-untyped-def]
        raise SideEffectUncertain("unknown result")

    monkeypatch.setitem(ACTION_HANDLERS, "run_durable_job", uncertain)
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition(
            "uncertain",
            (
                StepDefinition(
                    "job",
                    "run_durable_job",
                    {"executable": "python", "idempotency_key": "test-job"},
                    postcondition={"kind": "job_succeeded_from_result"},
                ),
            ),
        ),
        initial_state=WorkflowState.QUEUED,
    )
    result = WorkflowExecutor(store).run(workflow["workflow_id"])
    assert result["state"] == "uncertain"
    assert result["current_step"] == 0


def test_cancellation_during_action_stops_before_next_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition(
            "cancel",
            (
                StepDefinition("first", "check_file", {"path": "unused"}),
                StepDefinition("second", "check_file", {"path": "unused"}),
            ),
        ),
        initial_state=WorkflowState.QUEUED,
    )
    calls = 0

    def cancel(arguments, timeout):  # type: ignore[no-untyped-def]
        nonlocal calls
        del arguments, timeout
        calls += 1
        current = store.get(workflow["workflow_id"])
        store.transition(workflow["workflow_id"], current["version"], WorkflowState.CANCELLED)
        return {"ok": True}

    monkeypatch.setitem(ACTION_HANDLERS, "check_file", cancel)

    result = WorkflowExecutor(store).execute(workflow["workflow_id"], owner_id="worker-a")

    assert result["state"] == "cancelled"
    assert calls == 1
    assert store.list_operations(workflow["workflow_id"])["items"][0]["state"] == "succeeded"
    assert store.list_operations(workflow["workflow_id"])["items"][1]["state"] == "created"
