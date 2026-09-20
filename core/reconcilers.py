"""Side-effect-free postcondition evaluators for uncertain operations."""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil

from core.config import resolve_path
from core.errors import ToolError
from core.recovery_models import Evidence, Postcondition


def _required_string(values: dict[str, Any], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolError("invalid_postcondition", f"Postcondition requires non-empty {name!r}.")
    return value


def _file_exists(expected: dict[str, Any]) -> Evidence:
    path = resolve_path(_required_string(expected, "path"))
    exists = path.exists()
    return Evidence("filesystem", {"conclusive": True, "satisfied": exists, "exists": exists, "path": str(path)})


def _file_absent(expected: dict[str, Any]) -> Evidence:
    path = resolve_path(_required_string(expected, "path"))
    exists = path.exists()
    return Evidence("filesystem", {"conclusive": True, "satisfied": not exists, "exists": exists, "path": str(path)})


def _file_sha256(expected: dict[str, Any]) -> Evidence:
    path = resolve_path(_required_string(expected, "path"))
    wanted = _required_string(expected, "sha256").casefold()
    if len(wanted) != 64 or any(character not in "0123456789abcdef" for character in wanted):
        raise ToolError("invalid_postcondition", "sha256 must be a 64-character hexadecimal digest.")
    if not path.is_file():
        return Evidence(
            "filesystem",
            {"conclusive": True, "satisfied": False, "exists": path.exists(), "path": str(path)},
        )
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    return Evidence(
        "filesystem",
        {"conclusive": True, "satisfied": actual == wanted, "path": str(path), "sha256": actual},
    )


def _git_head(expected: dict[str, Any]) -> Evidence:
    repo = resolve_path(_required_string(expected, "repo"))
    wanted = _required_string(expected, "commit")
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        return Evidence(
            "git",
            {
                "conclusive": False,
                "satisfied": False,
                "repo": str(repo),
                "reason": "git_head_unavailable",
                "exit_code": completed.returncode,
            },
        )
    actual = completed.stdout.strip()
    return Evidence(
        "git",
        {"conclusive": True, "satisfied": actual.casefold() == wanted.casefold(), "repo": str(repo), "head": actual},
    )


def _process_identity(expected: dict[str, Any]) -> Evidence:
    pid = expected.get("pid")
    create_time = expected.get("create_time")
    executable = expected.get("executable")
    if not isinstance(pid, int) or pid <= 0 or not isinstance(create_time, (int, float)):
        raise ToolError("invalid_postcondition", "process_identity requires positive pid and numeric create_time.")
    if not isinstance(executable, str) or not executable.strip():
        raise ToolError("invalid_postcondition", "process_identity requires executable.")
    try:
        process = psutil.Process(pid)
        actual_created = process.create_time()
        actual_executable = process.exe()
    except psutil.NoSuchProcess:
        return Evidence(
            "process",
            {"conclusive": True, "satisfied": False, "pid": pid, "pid_exists": False},
        )
    except (psutil.AccessDenied, OSError) as exc:
        return Evidence(
            "process",
            {"conclusive": False, "satisfied": False, "pid": pid, "reason": type(exc).__name__},
        )
    same_created = abs(actual_created - float(create_time)) < 0.01
    same_executable = Path(actual_executable).resolve(strict=False) == Path(executable).resolve(strict=False)
    return Evidence(
        "process",
        {
            "conclusive": True,
            "satisfied": same_created and same_executable,
            "pid": pid,
            "pid_exists": True,
            "create_time_matches": same_created,
            "executable_matches": same_executable,
        },
    )


_EVALUATORS: dict[str, Callable[[dict[str, Any]], Evidence]] = {
    "file_exists": _file_exists,
    "file_absent": _file_absent,
    "file_sha256": _file_sha256,
    "git_head": _git_head,
    "process_identity": _process_identity,
}


def evaluate_postcondition(postcondition: Postcondition) -> Evidence:
    """Evaluate one allowlisted postcondition without replaying its operation."""

    evaluator = _EVALUATORS.get(postcondition.kind)
    if evaluator is None:
        raise ToolError(
            "unsupported_postcondition",
            f"Unsupported postcondition {postcondition.kind!r}.",
            hint=f"Use one of: {', '.join(sorted(_EVALUATORS))}.",
        )
    return evaluator(postcondition.expected)
