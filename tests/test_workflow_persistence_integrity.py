from __future__ import annotations

from pathlib import Path

import pytest

from core.workflow_actions import ACTION_HANDLERS
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowExecutor, WorkflowState, WorkflowStore


def test_execution_payload_is_not_redacted_or_truncated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    long_arg = "x" * 10_000
    request_key = "workflow-job-request-a"

    def capture(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        del timeout
        captured.update(arguments)
        return {"job_id": "a" * 32, "status": "succeeded", "exit_code": 0}

    monkeypatch.setitem(ACTION_HANDLERS, "run_durable_job", capture)
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(
        WorkflowDefinition(
            "payload-integrity",
            (
                StepDefinition(
                    "job",
                    "run_durable_job",
                    {
                        "executable": "python",
                        "idempotency_key": request_key,
                        "args": [long_arg],
                    },
                    postcondition={"kind": "job_succeeded_from_result"},
                ),
            ),
        ),
        initial_state=WorkflowState.QUEUED,
    )

    result = WorkflowExecutor(store).execute(workflow["workflow_id"], owner_id="payload-test")

    assert result["state"] == "completed"
    assert captured["idempotency_key"] == request_key
    assert captured["args"] == [long_arg]


def test_independent_durable_job_keys_remain_distinct_after_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def capture(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        del timeout
        seen.append(str(arguments["idempotency_key"]))
        return {"job_id": f"{len(seen):032x}", "status": "succeeded", "exit_code": 0}

    monkeypatch.setitem(ACTION_HANDLERS, "run_durable_job", capture)
    store = WorkflowStore(tmp_path / "workflows.db")
    definitions = [
        WorkflowDefinition(
            f"job-{suffix}",
            (
                StepDefinition(
                    "job",
                    "run_durable_job",
                    {"executable": "python", "idempotency_key": f"request-{suffix}"},
                    postcondition={"kind": "job_succeeded_from_result"},
                ),
            ),
        )
        for suffix in ("a", "b")
    ]
    workflows = [store.create(definition, initial_state=WorkflowState.QUEUED) for definition in definitions]

    reloaded = WorkflowStore(tmp_path / "workflows.db")
    for workflow in workflows:
        WorkflowExecutor(reloaded).execute(workflow["workflow_id"], owner_id=f"worker-{workflow['workflow_id']}")

    assert seen == ["request-a", "request-b"]
