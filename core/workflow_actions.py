"""Fixed allowlist of typed workflow actions."""

from __future__ import annotations

import hashlib
import inspect
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.config import resolve_path
from core.errors import ToolError
from core.job_scheduler import ensure_job_scheduler
from core.jobs import JobStore
from core.workflow_models import WorkflowDefinition
from tools.filesystem.patches import apply_patch_transaction
from tools.testing.impact import select_affected_tests
from tools.testing.verification import verify_changed_repository

ActionHandler = Callable[..., dict[str, Any]]


class _ActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VerifyChangesInput(_ActionInput):
    cwd: str = "."
    changed_paths: list[str] | None = None
    base: str | None = None
    checks: list[str] | None = None
    affected_only: bool = True
    fallback_policy: str = "select_full_suite"
    fail_fast: bool = False
    maxfail: int = 1
    stage_timeout_sec: float = 300
    total_timeout_sec: float = 1800
    ruff_fix: bool = False
    diagnostic_cap: int = 20


class GitStatusInput(_ActionInput):
    repo: str = "."
    require_clean: bool = False
    tag_must_not_exist: str | None = None


class GitStageInput(_ActionInput):
    repo: str = "."
    paths: list[str] = Field(min_length=1)


class GitCommitInput(_ActionInput):
    repo: str = "."
    message: str = Field(min_length=1, max_length=10_000)


class DurableJobInput(_ActionInput):
    executable: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=500)
    args: list[str] = Field(default_factory=list)
    cwd: str = "."
    encoding: str = "utf-8"


class CheckFileInput(_ActionInput):
    path: str = Field(min_length=1)
    exists: bool = True
    contains: str | None = None


class CheckHttpInput(_ActionInput):
    url: str = Field(pattern=r"^https?://")
    status: int = Field(default=200, ge=100, le=599)


class ApplyPatchInput(_ActionInput):
    cwd: str = "."
    patch: str = Field(min_length=1)
    expected_sha256: dict[str, str] | None = None
    backup: bool = True
    encoding: str = "auto"


class AffectedTestsInput(_ActionInput):
    cwd: str = "."
    changed_paths: list[str] | None = None
    base: str | None = None
    fallback_policy: Literal["report", "select_full_suite"] = "report"


class SemanticActionInput(_ActionInput):
    action: str = Field(min_length=1)
    automation_id: str | None = None
    control_type: str | None = None
    name: str | None = None
    value: str | None = None


class BrowserActionInput(_ActionInput):
    action: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    selector: str | None = None
    value: str | None = None


@dataclass(frozen=True, slots=True)
class ActionDescriptor:
    name: str
    input_model: type[BaseModel]
    handler: ActionHandler
    mutates: bool
    retry_policy: Literal["never", "transient"]
    allowed_postconditions: frozenset[str]
    secret_fields: frozenset[str] = frozenset()
    cancel_mode: Literal["immediate", "cooperative", "deferred"] = "deferred"


@dataclass(frozen=True, slots=True)
class ActionContext:
    workflow_id: str
    operation_id: str
    is_cancel_requested: Callable[[], bool]
    persist_external_ref: Callable[[dict[str, Any]], None]
    persist_intent_evidence: Callable[[dict[str, Any]], None]


class ActionCancelled(RuntimeError):
    pass


class TransientActionError(RuntimeError):
    pass


class SideEffectUncertain(RuntimeError):
    pass


def _run_git(
    arguments: dict[str, Any],
    timeout_sec: float,
    *command: str,
    uncertain_on_timeout: bool = False,
) -> dict[str, Any]:
    repo = resolve_path(str(arguments.get("repo", ".")))
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *command],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        if uncertain_on_timeout:
            raise SideEffectUncertain("Git mutation timed out without a known result.") from exc
        raise TransientActionError("Git read timed out.") from exc
    if completed.returncode != 0:
        raise ToolError("workflow_git_failed", completed.stderr.strip()[:2_000] or "Git command failed.")
    return {"repo": str(repo), "stdout": completed.stdout[:20_000], "exit_code": completed.returncode}


def _verify_changes(arguments: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    allowed = {
        key: value
        for key, value in arguments.items()
        if key
        in {
            "changed_paths",
            "base",
            "checks",
            "affected_only",
            "fallback_policy",
            "fail_fast",
            "maxfail",
            "stage_timeout_sec",
            "total_timeout_sec",
            "ruff_fix",
            "diagnostic_cap",
        }
    }
    allowed["total_timeout_sec"] = min(float(allowed.get("total_timeout_sec", timeout_sec)), timeout_sec)
    result = verify_changed_repository(resolve_path(str(arguments.get("cwd", "."))), **allowed)
    if not result.get("ok"):
        raise ToolError("workflow_verification_failed", "Change-aware verification did not pass.")
    return result


def _git_status(arguments: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    result = _run_git(arguments, timeout_sec, "status", "--short", "--branch")
    lines = result["stdout"].splitlines()
    dirty = any(line and not line.startswith("##") for line in lines)
    if arguments.get("require_clean") is True and dirty:
        raise ToolError("workflow_git_dirty", "Repository must be clean for this workflow.")
    tag = arguments.get("tag_must_not_exist")
    if isinstance(tag, str) and tag:
        tag_result = _run_git(arguments, timeout_sec, "tag", "--list", tag)
        if tag_result["stdout"].strip():
            raise ToolError("workflow_tag_exists", f"Git tag {tag!r} already exists.")
    result["dirty"] = dirty
    return result


def _git_stage(arguments: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    paths = arguments.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(item, str) and item for item in paths):
        raise ToolError("invalid_workflow_action", "git_stage requires a non-empty paths list.")
    result = _run_git(arguments, timeout_sec, "add", "--", *paths, uncertain_on_timeout=True)
    staged = _run_git(arguments, timeout_sec, "diff", "--cached", "--name-only")["stdout"].splitlines()
    result["requested_paths"] = paths
    result["staged_paths"] = staged
    return result


def _git_commit(arguments: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    message = arguments.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > 10_000:
        raise ToolError("invalid_workflow_action", "git_commit requires a bounded non-empty message.")
    result = _run_git(arguments, timeout_sec, "commit", "-m", message, uncertain_on_timeout=True)
    head = _run_git(arguments, timeout_sec, "rev-parse", "HEAD")["stdout"].strip()
    result["head"] = head
    return result


def _run_durable_job(
    arguments: dict[str, Any], timeout_sec: float, context: ActionContext | None = None
) -> dict[str, Any]:
    executable = arguments.get("executable")
    idempotency_key = arguments.get("idempotency_key")
    if not isinstance(executable, str) or not executable or not isinstance(idempotency_key, str) or not idempotency_key:
        raise ToolError("invalid_workflow_action", "run_durable_job requires executable and idempotency_key.")
    args = arguments.get("args", [])
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ToolError("invalid_workflow_action", "run_durable_job args must be a string list.")
    store = JobStore()
    ensure_job_scheduler(store)
    job = store.submit(
        [executable, *args],
        resolve_path(str(arguments.get("cwd", "."))),
        timeout_sec,
        idempotency_key,
        str(arguments.get("encoding", "utf-8")),
    )
    job_id = str(job["id"])
    if context is not None:
        context.persist_external_ref({"job_id": job_id, "request_key": idempotency_key})
    deadline = time.monotonic() + timeout_sec
    terminal = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}
    while True:
        status = store.get(job_id)
        if status["status"] in terminal:
            if status["status"] == "cancelled" and context is not None and context.is_cancel_requested():
                raise ActionCancelled("Durable job was cancelled with its workflow.")
            if status["status"] != "succeeded":
                raise ToolError("workflow_job_failed", f"Durable job ended as {status['status']}.")
            return {"job_id": status["id"], "status": status["status"], "exit_code": status.get("exit_code")}
        if context is not None and context.is_cancel_requested():
            store.cancel(job_id)
            while time.monotonic() < deadline:
                settled = store.get(job_id)
                if settled["status"] in terminal:
                    if settled["status"] == "cancelled":
                        raise ActionCancelled("Durable job was cancelled with its workflow.")
                    raise SideEffectUncertain(
                        f"Durable job cancellation settled as {settled['status']} rather than cancelled."
                    )
                time.sleep(0.1)
            raise SideEffectUncertain("Durable job cancellation did not reach a terminal state before the workflow deadline.")
        if time.monotonic() >= deadline:
            raise SideEffectUncertain("Durable job did not reach a terminal state before the workflow deadline.")
        time.sleep(0.1)


def _check_file(arguments: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    del timeout_sec
    path = resolve_path(str(arguments.get("path", "")))
    exists = path.exists()
    expected = bool(arguments.get("exists", True))
    if exists != expected:
        raise ToolError("workflow_file_check_failed", f"File existence was {exists}, expected {expected}.")
    contains = arguments.get("contains")
    if contains is not None:
        if not isinstance(contains, str) or not path.is_file():
            raise ToolError("invalid_workflow_action", "check_file contains requires an existing text file.")
        if contains not in path.read_text(encoding="utf-8", errors="replace"):
            raise ToolError("workflow_file_check_failed", "Expected text was not found in the file.")
    return {"path": str(path), "exists": exists, "contains_matched": contains is not None}


def _check_http(arguments: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    url = arguments.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ToolError("invalid_workflow_action", "check_http requires an HTTP(S) URL.")
    expected = int(arguments.get("status", 200))
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            status = int(response.status)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise TransientActionError(str(exc)) from exc
    if status != expected:
        raise ToolError("workflow_http_check_failed", f"HTTP status was {status}, expected {expected}.")
    return {"url": url, "status": status}


def _apply_patch(arguments: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    return apply_patch_transaction(
        resolve_path(str(arguments["cwd"])),
        str(arguments["patch"]),
        expected_sha256=arguments.get("expected_sha256"),
        backup=bool(arguments["backup"]),
        timeout_sec=timeout_sec,
        encoding=str(arguments["encoding"]),
    )


def _affected_tests(arguments: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    del timeout_sec
    return select_affected_tests(
        resolve_path(str(arguments["cwd"])),
        changed_paths=arguments.get("changed_paths"),
        base=arguments.get("base"),
        fallback_policy=arguments["fallback_policy"],
    )


def _adapter_required(arguments: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    del arguments, timeout_sec
    raise ToolError("workflow_adapter_required", "This action requires an active semantic runtime adapter.")


ACTION_HANDLERS: dict[str, ActionHandler] = {
    "apply_patch": _apply_patch,
    "affected_tests": _affected_tests,
    "verify_changes": _verify_changes,
    "git_status": _git_status,
    "git_stage": _git_stage,
    "git_commit": _git_commit,
    "run_durable_job": _run_durable_job,
    "check_file": _check_file,
    "check_http": _check_http,
    "desktop_semantic_action": _adapter_required,
    "browser_action": _adapter_required,
}

ACTION_DESCRIPTORS: dict[str, ActionDescriptor] = {
    "apply_patch": ActionDescriptor(
        "apply_patch", ApplyPatchInput, _apply_patch, True, "never", frozenset({"file_exists", "file_absent", "file_sha256"})
    ),
    "affected_tests": ActionDescriptor("affected_tests", AffectedTestsInput, _affected_tests, False, "never", frozenset()),
    "verify_changes": ActionDescriptor("verify_changes", VerifyChangesInput, _verify_changes, False, "never", frozenset()),
    "git_status": ActionDescriptor("git_status", GitStatusInput, _git_status, False, "transient", frozenset()),
    "git_stage": ActionDescriptor("git_stage", GitStageInput, _git_stage, True, "never", frozenset({"git_index_contains_from_result"})),
    "git_commit": ActionDescriptor("git_commit", GitCommitInput, _git_commit, True, "never", frozenset({"git_head_from_result"})),
    "run_durable_job": ActionDescriptor(
        "run_durable_job",
        DurableJobInput,
        _run_durable_job,
        True,
        "never",
        frozenset({"job_succeeded_from_result"}),
        frozenset(),
        "cooperative",
    ),
    "check_file": ActionDescriptor("check_file", CheckFileInput, _check_file, False, "never", frozenset()),
    "check_http": ActionDescriptor("check_http", CheckHttpInput, _check_http, False, "transient", frozenset()),
    "desktop_semantic_action": ActionDescriptor(
        "desktop_semantic_action", SemanticActionInput, _adapter_required, True, "never", frozenset({"ui_element_state"})
    ),
    "browser_action": ActionDescriptor(
        "browser_action", BrowserActionInput, _adapter_required, True, "never", frozenset({"browser_state"})
    ),
}


def get_action_descriptor(name: str) -> ActionDescriptor:
    descriptor = ACTION_DESCRIPTORS.get(name)
    if descriptor is None:
        raise ToolError(
            "workflow_action_not_allowed",
            f"Workflow action {name!r} is not allowlisted.",
            hint=f"Use one of: {', '.join(sorted(ACTION_DESCRIPTORS))}.",
        )
    return descriptor


def validate_operation(
    name: str,
    arguments: dict[str, Any],
    postcondition: dict[str, Any] | None,
) -> tuple[ActionDescriptor, dict[str, Any]]:
    descriptor = get_action_descriptor(name)
    try:
        validated = descriptor.input_model.model_validate(arguments).model_dump()
    except ValidationError as exc:
        raise ToolError("invalid_workflow_arguments", f"Invalid {name} arguments: {exc.errors(include_url=False)}") from exc
    if descriptor.mutates:
        kind = str((postcondition or {}).get("kind", ""))
        if kind not in descriptor.allowed_postconditions:
            raise ToolError(
                "workflow_postcondition_required",
                f"Mutating action {name!r} requires a supported postcondition.",
            )
    return descriptor, validated


def validate_workflow_definition(definition: WorkflowDefinition) -> None:
    for step in definition.steps:
        validate_operation(step.action, step.arguments, step.postcondition)


def prepare_action_intent(
    name: str,
    arguments: dict[str, Any],
    postcondition: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Materialize result-independent recovery intent before a side effect begins."""

    descriptor, validated = validate_operation(name, arguments, postcondition)
    if not descriptor.mutates:
        return postcondition, None
    if name == "git_stage":
        canonical = {
            "kind": "git_index_contains",
            "expected": {
                "repo": str(resolve_path(str(validated["repo"]))),
                "paths": list(validated["paths"]),
            },
        }
        return canonical, {"requested_paths": list(validated["paths"])}
    if name == "git_commit":
        repo = str(resolve_path(str(validated["repo"])))
        git_args = {"repo": repo}
        before_head = _run_git(git_args, 10, "rev-parse", "HEAD")["stdout"].strip()
        staged_tree = _run_git(git_args, 10, "write-tree")["stdout"].strip()
        message_hash = hashlib.sha256(str(validated["message"]).strip().encode("utf-8")).hexdigest()
        expected = {
            "repo": repo,
            "before_head": before_head,
            "staged_tree": staged_tree,
            "message_sha256": message_hash,
        }
        return {"kind": "git_commit_effect", "expected": expected}, expected
    if name == "run_durable_job":
        request_key = str(validated["idempotency_key"])
        expected = {"request_key": request_key, "status": "succeeded"}
        return {"kind": "job_request_key_state", "expected": expected}, {"request_key": request_key}
    return postcondition, None


def execute_action(
    name: str,
    arguments: dict[str, Any],
    timeout_sec: float,
    context: ActionContext | None = None,
) -> dict[str, Any]:
    descriptor = get_action_descriptor(name)
    try:
        validated = descriptor.input_model.model_validate(arguments).model_dump()
    except ValidationError as exc:
        raise ToolError("invalid_workflow_arguments", f"Invalid {name} arguments: {exc.errors(include_url=False)}") from exc
    handler = ACTION_HANDLERS[name]
    if context is not None and "context" in inspect.signature(handler).parameters:
        return handler(validated, timeout_sec, context)
    return handler(validated, timeout_sec)
