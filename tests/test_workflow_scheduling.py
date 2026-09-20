from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.workflow_actions import execute_action
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowState, WorkflowStore


def test_workflow_durable_job_uses_version_wait_not_fixed_status_polling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[tuple[str, int, float]] = []

    class FakeJobStore:
        def submit(self, command: list[str], cwd: Path, timeout: float, request_key: str, encoding: str) -> dict[str, Any]:
            del command, cwd, timeout, request_key, encoding
            return {"id": "a" * 32, "status": "running", "version": 1, "exit_code": None}

        def wait(self, job_id: str, after_version: int, timeout: float) -> dict[str, Any]:
            waits.append((job_id, after_version, timeout))
            return {
                "id": job_id,
                "status": "succeeded",
                "version": 2,
                "exit_code": 0,
                "changed": True,
                "timed_out": False,
            }

        def cancel(self, job_id: str) -> dict[str, Any]:
            raise AssertionError(f"unexpected cancel: {job_id}")

        def get(self, job_id: str) -> dict[str, Any]:
            raise AssertionError(f"fixed status polling must not be used: {job_id}")

    monkeypatch.setattr("core.workflow_actions.JobStore", FakeJobStore)
    monkeypatch.setattr("core.workflow_actions.ensure_job_scheduler", lambda store: None)

    result = execute_action(
        "run_durable_job",
        {"executable": "python", "idempotency_key": "wait-job", "cwd": str(tmp_path)},
        10,
    )

    assert result["status"] == "succeeded"
    assert waits == [("a" * 32, 1, 1.0)]


def test_list_queued_is_oldest_first_and_not_limited_by_recent_history(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    definition = WorkflowDefinition(
        "queued",
        (StepDefinition("check", "check_file", {"path": "unused"}),),
    )
    ids: list[str] = []
    for _ in range(120):
        created = store.create(definition, initial_state=WorkflowState.QUEUED)
        ids.append(str(created["workflow_id"]))
    for _ in range(25):
        created = store.create(definition, initial_state=WorkflowState.QUEUED)
        current = store.get(str(created["workflow_id"]))
        store.transition(str(created["workflow_id"]), current["version"], WorkflowState.CANCELLED)

    queued = store.list_queued(limit=100)

    assert queued["total_count"] == 120
    assert queued["count"] == 100
    assert queued["has_more"] is True
    assert [item["workflow_id"] for item in queued["items"]] == ids[:100]
