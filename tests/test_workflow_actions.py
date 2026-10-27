from __future__ import annotations

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest

from core.errors import ToolError
from core.workflow_actions import ACTION_DESCRIPTORS, execute_action, get_action_descriptor, validate_operation


@pytest.mark.parametrize("status,expected", [(200, 200), (404, 404), (503, 503), (404, 200)])
def test_http_check_compares_actual_response_status(status: int, expected: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(status)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/"
        try:
            if status == expected:
                assert execute_action("check_http", {"url": url, "status": expected}, 5)["status"] == status
            else:
                with pytest.raises(ToolError) as raised:
                    execute_action("check_http", {"url": url, "status": expected}, 5)
                assert raised.value.code == "workflow_http_check_failed"
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_http_check_observes_redirect_status_without_following() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/final")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/redirect"
        try:
            result = execute_action("check_http", {"url": url, "status": 302}, 5)
            assert result["status"] == 302
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_descriptor_rejects_unknown_action() -> None:
    with pytest.raises(ToolError, match="not allowlisted"):
        get_action_descriptor("shell")


def test_descriptor_rejects_unknown_and_missing_fields() -> None:
    with pytest.raises(ToolError, match="arguments"):
        validate_operation("check_file", {"path": "x", "unknown": True}, None)
    with pytest.raises(ToolError, match="arguments"):
        validate_operation("git_commit", {"repo": "."}, {"kind": "git_commit_intent"})


def test_mutating_action_requires_supported_postcondition() -> None:
    with pytest.raises(ToolError, match="postcondition"):
        validate_operation("apply_patch", {"cwd": ".", "patch": "--- a/a\n+++ b/a"}, None)
    with pytest.raises(ToolError, match="postcondition"):
        validate_operation(
            "git_commit",
            {"repo": ".", "message": "feat: test"},
            {"kind": "file_exists"},
        )


def test_declared_literal_secret_fields_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    descriptor = replace(
        get_action_descriptor("check_http"),
        name="secret_probe",
        secret_fields=frozenset({"url"}),
    )
    monkeypatch.setitem(ACTION_DESCRIPTORS, "secret_probe", descriptor)

    with pytest.raises(ToolError) as raised:
        validate_operation("secret_probe", {"url": "https://secret.example"}, None)

    assert raised.value.code == "workflow_secret_literal_not_supported"


def test_validated_arguments_are_typed_and_secrets_are_declared() -> None:
    descriptor, arguments = validate_operation(
        "run_durable_job",
        {"executable": "python", "idempotency_key": "secret-key", "args": []},
        {"kind": "job_request_key_intent"},
    )
    assert arguments["cwd"] == "."
    assert descriptor.mutates is True
    assert descriptor.retry_policy == "never"
    assert descriptor.secret_fields == frozenset()
    assert descriptor.cancel_mode == "cooperative"


@pytest.mark.parametrize(
    "name",
    [
        "apply_patch",
        "affected_tests",
        "verify_changes",
        "git_status",
        "git_stage",
        "git_commit",
        "run_durable_job",
        "check_file",
        "check_http",
        "desktop_semantic_action",
        "browser_action",
    ],
)
def test_approved_action_surface_has_descriptors(name: str) -> None:
    assert get_action_descriptor(name).name == name


def test_verification_model_preserves_python_default_and_validates_auto() -> None:
    from core.workflow_actions import VerifyChangesInput

    assert VerifyChangesInput().test_backend == "python"
    assert VerifyChangesInput(test_backend="auto").test_backend == "auto"


@pytest.mark.parametrize("uncertain", [False, True])
def test_workflow_verification_distinguishes_unknown_execution(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, uncertain: bool,
) -> None:
    from contextlib import nullcontext

    from core import workflow_actions

    captured: dict[str, object] = {}

    def verify(*args: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"ok": False, "required_unknown": True, "execution_uncertain": uncertain}

    monkeypatch.setattr(workflow_actions, "verify_changed_repository", verify)
    monkeypatch.setattr(workflow_actions, "managed_resource_claims", lambda *args, **kwargs: nullcontext())
    error = workflow_actions.SideEffectUncertain if uncertain else ToolError
    with pytest.raises(error):
        workflow_actions._verify_changes({"cwd": str(tmp_path), "test_backend": "auto"}, 5)
    assert captured["test_backend"] == "auto"


@pytest.mark.parametrize("options", [{"stage_timeout_sec": float("nan")}, {"total_timeout_sec": 0},
                                   {"test_backend": "auto", "stage_timeout_sec": 601},
                                   {"stage_timeout_sec": 3601}, {"total_timeout_sec": 7201}])
def test_workflow_verification_rejects_invalid_budget_before_admission(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, object],
) -> None:
    from core import workflow_actions

    monkeypatch.setattr(workflow_actions, "managed_resource_claims", lambda *args, **kwargs: pytest.fail("admitted invalid budget"))
    with pytest.raises(ValueError):
        workflow_actions._verify_changes(options, 5)


def test_workflow_verification_requested_budget_includes_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import Iterator
    from contextlib import contextmanager
    from types import SimpleNamespace

    from core import workflow_actions

    now = [10.0]
    admitted_budgets: list[object] = []

    @contextmanager
    def claims(*args: object, **kwargs: object) -> Iterator[None]:
        admitted_budgets.append(kwargs["timeout_sec"])
        now[0] += 2
        yield

    monkeypatch.setattr(workflow_actions, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(workflow_actions, "managed_resource_claims", claims)
    monkeypatch.setattr(workflow_actions, "verify_changed_repository", lambda *a, **kw: pytest.fail("dispatched after requested deadline"))
    with pytest.raises(TimeoutError, match="admission"):
        workflow_actions._verify_changes({"total_timeout_sec": 1}, 5)
    assert admitted_budgets == [1]


@pytest.mark.parametrize("dispatched", [False, True])
def test_workflow_verifier_exception_preserves_durable_uncertainty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dispatched: bool,
) -> None:
    from core import recovery, workflow_actions
    from core.workflows import NATIVE_ONLY_ROUTING, StepDefinition, WorkflowDefinition, WorkflowExecutor, WorkflowState, WorkflowStore

    repo = tmp_path / "repo"
    repo.mkdir()
    settings = replace(workflow_actions.SETTINGS, state_dir=tmp_path / "state")
    monkeypatch.setattr(workflow_actions, "SETTINGS", settings)
    journal = recovery.OperationRecoveryJournal()
    journal.configure(tmp_path / "recovery.jsonl", "workflow-verification")
    monkeypatch.setattr(recovery, "OPERATION_RECOVERY", journal)
    effects: list[str] = []

    def verify(*args: object, **kwargs: object) -> object:
        def effect() -> object:
            if dispatched:
                recovery.mark_native_test_dispatch()
                effects.append("test")
            raise ToolError("process_cleanup_incomplete" if dispatched else "runner_unavailable", "Test run could not complete.")
        return recovery.run_native_operation("verify_changes", str(repo), effect)

    monkeypatch.setattr(workflow_actions, "verify_changed_repository", verify)
    store = WorkflowStore(settings.workflow_db)
    queued = store.create(WorkflowDefinition("verify", (StepDefinition("tests", "verify_changes", {"cwd": str(repo)}),)),
                          initial_state=WorkflowState.QUEUED)
    result = WorkflowExecutor(store, route_planner=NATIVE_ONLY_ROUTING).run(queued["workflow_id"])
    expected = "uncertain" if dispatched else "failed"
    assert result["state"] == expected
    assert store.list_operations(queued["workflow_id"])["items"][0]["state"] == expected
    assert effects == (["test"] if dispatched else [])
    assert bool(journal.list_operations(state="uncertain")["items"]) == dispatched
