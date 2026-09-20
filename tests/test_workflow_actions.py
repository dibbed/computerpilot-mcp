from __future__ import annotations

import pytest

from core.errors import ToolError
from core.workflow_actions import get_action_descriptor, validate_operation


def test_descriptor_rejects_unknown_action() -> None:
    with pytest.raises(ToolError, match="not allowlisted"):
        get_action_descriptor("shell")


def test_descriptor_rejects_unknown_and_missing_fields() -> None:
    with pytest.raises(ToolError, match="arguments"):
        validate_operation("check_file", {"path": "x", "unknown": True}, None)
    with pytest.raises(ToolError, match="arguments"):
        validate_operation("git_commit", {"repo": "."}, {"kind": "git_head_from_result"})


def test_mutating_action_requires_supported_postcondition() -> None:
    with pytest.raises(ToolError, match="postcondition"):
        validate_operation("apply_patch", {"cwd": ".", "patch": "--- a/a\n+++ b/a"}, None)
    with pytest.raises(ToolError, match="postcondition"):
        validate_operation(
            "git_commit",
            {"repo": ".", "message": "feat: test"},
            {"kind": "file_exists"},
        )


def test_validated_arguments_are_typed_and_secrets_are_declared() -> None:
    descriptor, arguments = validate_operation(
        "run_durable_job",
        {"executable": "python", "idempotency_key": "secret-key", "args": []},
        {"kind": "job_succeeded_from_result"},
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
