from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from core.reconcilers import evaluate_postcondition
from core.recovery_models import Postcondition
from core.workflow_actions import ACTION_HANDLERS, ActionContext, SideEffectUncertain, prepare_action_intent
from core.workflow_reconciliation import reconcile_operation
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowExecutor, WorkflowState, WorkflowStore


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Workflow Tests")
    (repo / "value.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "value.txt")
    _git(repo, "commit", "-m", "initial")


def test_git_commit_reconciliation_uses_pre_effect_identity(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "value.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "add", "value.txt")

    postcondition, intent = prepare_action_intent(
        "git_commit",
        {"repo": str(repo), "message": "feat: recoverable commit"},
        {"kind": "git_head_from_result"},
    )

    assert postcondition is not None
    assert postcondition["kind"] == "git_commit_effect"
    assert intent is not None
    before_head = intent["before_head"]
    _git(repo, "commit", "-m", "feat: recoverable commit")

    evidence = evaluate_postcondition(
        Postcondition(str(postcondition["kind"]), dict(postcondition["expected"]))
    )

    assert evidence.data["conclusive"] is True
    assert evidence.data["satisfied"] is True
    assert evidence.data["parent"] == before_head


def test_git_commit_reconciliation_is_inconclusive_after_unrelated_head_move(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "value.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "add", "value.txt")
    postcondition, _ = prepare_action_intent(
        "git_commit",
        {"repo": str(repo), "message": "expected commit"},
        {"kind": "git_head_from_result"},
    )
    assert postcondition is not None

    _git(repo, "commit", "-m", "different commit")
    evidence = evaluate_postcondition(
        Postcondition(str(postcondition["kind"]), dict(postcondition["expected"]))
    )

    assert evidence.data["conclusive"] is False
    assert evidence.data["satisfied"] is False


def test_lost_durable_job_result_reconciles_by_request_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = WorkflowStore(tmp_path / "workflows.db")
    request_key = "durable-request-42"
    workflow = store.create(
        WorkflowDefinition(
            "job-recovery",
            (
                StepDefinition(
                    "job",
                    "run_durable_job",
                    {"executable": "python", "idempotency_key": request_key, "cwd": str(tmp_path)},
                    postcondition={"kind": "job_succeeded_from_result"},
                ),
            ),
        ),
        initial_state=WorkflowState.QUEUED,
    )

    def result_lost(
        arguments: dict[str, object],
        timeout: float,
        context: ActionContext,
    ) -> dict[str, object]:
        del arguments, timeout
        context.persist_external_ref({"job_id": "a" * 32, "request_key": request_key})
        raise SideEffectUncertain("result lost after submit")

    monkeypatch.setitem(ACTION_HANDLERS, "run_durable_job", result_lost)
    result = WorkflowExecutor(store).execute(str(workflow["workflow_id"]), owner_id="worker-a")
    assert result["state"] == "uncertain"

    operation = store.get_operation_for_reconciliation(
        store.list_operations(str(workflow["workflow_id"]))["items"][0]["operation_id"]
    )
    assert operation["postcondition"] == {
        "kind": "job_request_key_state",
        "expected": {"request_key": request_key, "status": "succeeded"},
    }
    assert operation["intent_evidence"] == {"request_key": request_key}
    assert operation["external_ref"] == {"job_id": "a" * 32, "request_key": request_key}

    class RecoveredJobs:
        def get_by_request_key(self, key: str) -> dict[str, Any]:
            assert key == request_key
            return {
                "id": "a" * 32,
                "status": "succeeded",
                "version": 3,
                "exit_code": 0,
            }

    monkeypatch.setattr("core.reconcilers.JobStore", lambda: RecoveredJobs())
    reconciled = reconcile_operation(
        store,
        operation["operation_id"],
        expected_version=operation["version"],
    )

    assert reconciled["operation"]["state"] == "succeeded"
    assert reconciled["workflow"]["state"] == "completed"
    exact = store.get_operation_for_reconciliation(operation["operation_id"])
    assert exact["reconciliation_evidence"]["source"] == "durable_job"
    assert exact["reconciliation_evidence"]["request_key"] == request_key
