"""Deterministic built-in development workflow definitions."""

from __future__ import annotations

from typing import Any, Protocol

from core.errors import ToolError
from core.workflows import StepDefinition, WorkflowDefinition


class DeploymentAdapter(Protocol):
    def definition(self, parameters: dict[str, Any]) -> WorkflowDefinition: ...


BUILTIN_NAMES = ("implement_and_verify", "safe_git_commit", "prepare_release")


def builtin_workflow(name: str, parameters: dict[str, Any]) -> WorkflowDefinition:
    repo = str(parameters.get("repo", "."))
    changed_paths = parameters.get("changed_paths")
    verify_arguments = {"cwd": repo, "affected_only": True}
    if isinstance(changed_paths, list):
        verify_arguments["changed_paths"] = changed_paths
    if name == "implement_and_verify":
        return WorkflowDefinition(
            name,
            (
                StepDefinition("inspect repository state", "git_status", {"repo": repo}),
                StepDefinition("verify changed code", "verify_changes", verify_arguments, timeout_sec=1_800),
            ),
            "Verify an already-implemented change with repository-aware checks.",
        )
    if name == "safe_git_commit":
        paths = parameters.get("paths")
        message = parameters.get("message")
        if not isinstance(paths, list) or not paths or not isinstance(message, str) or not message.strip():
            raise ToolError("invalid_builtin_parameters", "safe_git_commit requires non-empty paths and message.")
        return WorkflowDefinition(
            name,
            (
                StepDefinition("inspect repository state", "git_status", {"repo": repo}),
                StepDefinition("verify changed code", "verify_changes", verify_arguments, timeout_sec=1_800),
                StepDefinition(
                    "stage explicit paths", "git_stage", {"repo": repo, "paths": paths},
                    postcondition={"kind": "git_stage_intent"},
                ),
                StepDefinition(
                    "create commit", "git_commit", {"repo": repo, "message": message},
                    postcondition={"kind": "git_commit_intent"},
                ),
            ),
            "Verify, explicitly stage, and commit without pushing.",
        )
    if name == "prepare_release":
        version = parameters.get("version")
        tag = parameters.get("tag")
        version_file = parameters.get("version_file")
        if not all(isinstance(value, str) and value for value in (version, tag, version_file)):
            raise ToolError("invalid_builtin_parameters", "prepare_release requires version, tag, and version_file.")
        return WorkflowDefinition(
            name,
            (
                StepDefinition(
                    "validate clean release state", "git_status",
                    {"repo": repo, "require_clean": True, "tag_must_not_exist": tag},
                ),
                StepDefinition(
                    "validate version declaration", "check_file",
                    {"path": str(version_file), "exists": True, "contains": str(version)},
                ),
                StepDefinition("run full verification", "verify_changes", {"cwd": repo, "affected_only": False}, timeout_sec=1_800),
            ),
            "Validate version, tag availability, cleanliness, and the full verification suite without publishing.",
        )
    if name == "deploy_and_healthcheck":
        raise ToolError(
            "deployment_adapter_required",
            "Deployment is project-specific and requires an explicit DeploymentAdapter; blind deployment is disabled.",
        )
    raise ToolError("unknown_builtin_workflow", f"Unknown built-in workflow {name!r}.")

