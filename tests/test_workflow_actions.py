from __future__ import annotations

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
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
