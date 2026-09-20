"""Validated, bounded Git command execution shared by MCP tools."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from core.config import PROJECT_ROOT, resolve_path
from core.errors import ToolError
from core.executor import run_bounded


def repository(value: str | None) -> Path:
    path = resolve_path(value) if value else PROJECT_ROOT
    if not path.is_dir():
        raise NotADirectoryError(f"Repository directory not found: {path}")
    return path


def validate_revision(value: str) -> str:
    if not value or value.startswith("-") or "\x00" in value:
        raise ToolError("invalid_git_revision", "Git revisions must be non-empty and cannot begin with '-'.")
    return value


def validate_paths(paths: list[str]) -> list[str]:
    if not paths or any(not item or item.startswith("-") or "\x00" in item for item in paths):
        raise ToolError("invalid_git_pathspec", "Git paths must be explicit and cannot begin with '-'.")
    return paths


def run_git(repo: Path, args: list[str], timeout_sec: float = 30) -> dict[str, Any]:
    executable = shutil.which("git")
    if executable is None:
        raise ToolError("git_not_found", "Git is not installed or not on PATH.")
    result = run_bounded(
        [executable, "-c", "core.quotepath=false", *args],
        cwd=repo,
        timeout_sec=timeout_sec,
        stdout_limit=None,
        stderr_limit=None,
        output_mode="head",
        encoding="utf-8",
    )
    if result["exit_code"] != 0:
        message = result["stderr"]["text"].strip() or result["stdout"]["text"].strip()
        raise ToolError("git_failed", message or f"Git exited {result['exit_code']}.")
    return result
