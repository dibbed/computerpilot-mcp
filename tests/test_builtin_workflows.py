from __future__ import annotations

import pytest

from core.errors import ToolError
from core.workflow_actions import validate_workflow_definition
from tools.workflows.builtins import builtin_workflow


def test_implement_and_verify_is_deterministic() -> None:
    first = builtin_workflow("implement_and_verify", {"repo": "C:/repo", "changed_paths": ["core/a.py"]})
    second = builtin_workflow("implement_and_verify", {"repo": "C:/repo", "changed_paths": ["core/a.py"]})
    assert first == second
    assert [step.action for step in first.steps] == ["git_status", "verify_changes"]


def test_safe_git_commit_uses_explicit_paths_and_postconditions() -> None:
    definition = builtin_workflow(
        "safe_git_commit", {"repo": "C:/repo", "paths": ["core/a.py"], "message": "feat: update a"},
    )
    assert definition.steps[2].arguments["paths"] == ["core/a.py"]
    assert definition.steps[2].postcondition == {"kind": "git_index_contains_from_result"}
    assert definition.steps[3].postcondition == {"kind": "git_head_from_result"}
    validate_workflow_definition(definition)


def test_prepare_release_has_version_and_tag_preconditions() -> None:
    definition = builtin_workflow(
        "prepare_release", {"repo": "C:/repo", "version": "0.2.4", "tag": "v0.2.4", "version_file": "C:/repo/core/config.py"},
    )
    assert definition.steps[0].arguments["tag_must_not_exist"] == "v0.2.4"
    assert definition.steps[1].arguments["contains"] == "0.2.4"
    assert definition.steps[-1].arguments["affected_only"] is False


def test_deployment_requires_project_adapter() -> None:
    with pytest.raises(ToolError, match="requires an explicit"):
        builtin_workflow("deploy_and_healthcheck", {"repo": "C:/repo"})
