from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from core.workflow_actions import ACTION_HANDLERS, ActionCancelled, ActionContext, execute_action
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowExecutor, WorkflowState, WorkflowStore


def test_running_workflow_enters_cancelling_then_stops_after_completed_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition(
            "cancel-deferred",
            (
                StepDefinition("first", "check_file", {"path": "unused"}),
                StepDefinition("second", "check_file", {"path": "unused"}),
            ),
        ),
        initial_state=WorkflowState.QUEUED,
    )
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def deferred(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        nonlocal calls
        del arguments, timeout
        calls += 1
        started.set()
        assert release.wait(timeout=5)
        return {"ok": True}

    monkeypatch.setitem(ACTION_HANDLERS, "check_file", deferred)
    executor = WorkflowExecutor(store)
    workflow_id = str(workflow["workflow_id"])

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(executor.execute, workflow_id, owner_id="worker-a")
        assert started.wait(timeout=5)
        current = store.get(workflow_id)
        requested = store.request_cancel(workflow_id, current["version"], reason="operator request")
        assert requested["state"] == "cancelling"
        assert requested["cancel_requested_at"]
        release.set()
        result = future.result(timeout=10)

    assert result["state"] == "cancelled"
    assert calls == 1
    operations = store.list_operations(workflow_id)["items"]
    assert operations[0]["state"] == "succeeded"
    assert operations[1]["state"] == "created"
    assert store.get(workflow_id)["steps"][0]["state"] == "completed"


def test_action_context_propagates_cooperative_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition(
            "cancel-cooperative",
            (
                StepDefinition(
                    "job",
                    "run_durable_job",
                    {"executable": "python", "idempotency_key": "cancel-job"},
                    postcondition={"kind": "job_succeeded_from_result"},
                ),
            ),
        ),
        initial_state=WorkflowState.QUEUED,
    )
    entered = threading.Event()

    def cooperative(arguments: dict[str, object], timeout: float, context: ActionContext) -> dict[str, object]:
        del arguments, timeout
        entered.set()
        while not context.is_cancel_requested():
            threading.Event().wait(0.01)
        raise ActionCancelled("cancel observed")

    monkeypatch.setitem(ACTION_HANDLERS, "run_durable_job", cooperative)
    workflow_id = str(workflow["workflow_id"])

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(WorkflowExecutor(store).execute, workflow_id, owner_id="worker-a")
        assert entered.wait(timeout=5)
        current = store.get(workflow_id)
        assert store.request_cancel(workflow_id, current["version"])["state"] == "cancelling"
        result = future.result(timeout=10)

    assert result["state"] == "cancelled"
    operation = store.list_operations(workflow_id)["items"][0]
    assert operation["state"] == "cancelled"
    assert store.get(workflow_id)["steps"][0]["state"] == "cancelled"


def test_durable_job_handler_requests_underlying_job_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeJobStore:
        cancelled = False

        def submit(self, command: list[str], cwd: Path, timeout: float, request_key: str, encoding: str) -> dict[str, Any]:
            del command, cwd, timeout, request_key, encoding
            return {"id": "a" * 32, "status": "running", "version": 1, "exit_code": None}

        def get(self, job_id: str) -> dict[str, Any]:
            del job_id
            return {
                "id": "a" * 32,
                "status": "cancelled" if self.cancelled else "running",
                "version": 2 if self.cancelled else 1,
                "exit_code": None,
            }

        def cancel(self, job_id: str) -> dict[str, Any]:
            calls.append(job_id)
            self.cancelled = True
            return self.get(job_id)

        def wait(self, job_id: str, after_version: int, timeout: float) -> dict[str, Any]:
            del after_version, timeout
            return {"changed": False, "timed_out": True, **self.get(job_id)}

    monkeypatch.setattr("core.workflow_actions.JobStore", FakeJobStore)
    monkeypatch.setattr("core.workflow_actions.ensure_job_scheduler", lambda store: None)
    context = ActionContext(
        workflow_id="wf",
        operation_id="op",
        is_cancel_requested=lambda: True,
        persist_external_ref=lambda value: None,
        persist_intent_evidence=lambda value: None,
    )

    with pytest.raises(ActionCancelled):
        execute_action(
            "run_durable_job",
            {"executable": "python", "idempotency_key": "request-key", "cwd": "."},
            5,
            context,
        )

    assert calls == ["a" * 32]
