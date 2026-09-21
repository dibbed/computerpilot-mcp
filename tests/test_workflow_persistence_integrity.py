from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from core.workflow_actions import ACTION_HANDLERS
from core.workflow_store import canonical_execution_definition
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowExecutor, WorkflowState, WorkflowStore


def test_execution_payload_is_not_redacted_or_truncated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    long_arg = "x" * (100 * 1024)
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
                    postcondition={"kind": "job_request_key_intent"},
                ),
            ),
        ),
        initial_state=WorkflowState.QUEUED,
    )
    public_arg = workflow["definition"]["steps"][0]["arguments"]["args"][0]
    assert len(public_arg) == 2_000
    assert public_arg == long_arg[:2_000]
    assert workflow["definition"]["steps"][0]["arguments"]["idempotency_key"] == request_key

    result = WorkflowExecutor(store).execute(workflow["workflow_id"], owner_id="payload-test")

    assert result["state"] == "completed"
    assert captured["idempotency_key"] == request_key
    assert captured["args"] == [long_arg]


def test_large_apply_patch_payload_survives_public_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = "p" * 5_000
    patch = (
        "diff --git a/large.txt b/large.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/large.txt\n"
        "@@ -0,0 +1 @@\n"
        f"+{content}\n"
    )
    original = ACTION_HANDLERS["apply_patch"]
    captured: dict[str, object] = {}

    def capture(arguments: dict[str, object], timeout: float) -> dict[str, object]:
        captured.update(arguments)
        return original(arguments, timeout)

    monkeypatch.setitem(ACTION_HANDLERS, "apply_patch", capture)
    definition = WorkflowDefinition(
        "large-patch-payload",
        (
            StepDefinition(
                "patch",
                "apply_patch",
                {"cwd": str(tmp_path), "patch": patch, "backup": False},
                postcondition={"kind": "patch_effect_intent"},
            ),
        ),
    )
    submitted_payload = canonical_execution_definition(definition)
    submitted_hash = hashlib.sha256(submitted_payload.encode("utf-8")).hexdigest()
    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = store.create(definition, initial_state=WorkflowState.QUEUED)
    with sqlite3.connect(store.path) as connection:
        durable_payload, durable_hash = connection.execute(
            "SELECT execution_definition_json, execution_definition_hash FROM workflows WHERE workflow_id = ?",
            (workflow["workflow_id"],),
        ).fetchone()
    assert durable_payload == submitted_payload
    assert durable_hash == submitted_hash

    reloaded = WorkflowStore(store.path)
    result = WorkflowExecutor(reloaded).execute(str(workflow["workflow_id"]), owner_id="patch-payload-test")

    assert result["state"] == "completed"
    assert len(patch) > 2_000
    assert captured["patch"] == patch
    assert (tmp_path / "large.txt").read_text(encoding="utf-8") == content + "\n"


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
                    postcondition={"kind": "job_request_key_intent"},
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
