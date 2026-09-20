"""Fixed allowlist of typed workflow actions."""

from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from core.config import resolve_path
from core.errors import ToolError
from core.job_scheduler import ensure_job_scheduler
from core.jobs import JobStore
from tools.testing.verification import verify_changed_repository

ActionHandler = Callable[[dict[str, Any], float], dict[str, Any]]


class TransientActionError(RuntimeError):
    pass


class SideEffectUncertain(RuntimeError):
    pass


def _run_git(
    arguments: dict[str, Any], timeout_sec: float, *command: str, uncertain_on_timeout: bool = False,
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
        if key in {
            "changed_paths", "base", "checks", "affected_only", "fallback_policy", "fail_fast", "maxfail",
            "stage_timeout_sec", "total_timeout_sec", "ruff_fix", "diagnostic_cap",
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


def _run_durable_job(arguments: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
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
    deadline = time.monotonic() + timeout_sec
    while True:
        status = store.get(str(job["id"]))
        if status["status"] in {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}:
            if status["status"] != "succeeded":
                raise ToolError("workflow_job_failed", f"Durable job ended as {status['status']}.")
            return {"job_id": status["id"], "status": status["status"], "exit_code": status.get("exit_code")}
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


ACTION_HANDLERS: dict[str, ActionHandler] = {
    "verify_changes": _verify_changes,
    "git_status": _git_status,
    "git_stage": _git_stage,
    "git_commit": _git_commit,
    "run_durable_job": _run_durable_job,
    "check_file": _check_file,
    "check_http": _check_http,
}


def execute_action(name: str, arguments: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    handler = ACTION_HANDLERS.get(name)
    if handler is None:
        raise ToolError(
            "workflow_action_not_allowed",
            f"Workflow action {name!r} is not allowlisted.",
            hint=f"Use one of: {', '.join(sorted(ACTION_HANDLERS))}.",
        )
    return handler(arguments, timeout_sec)
