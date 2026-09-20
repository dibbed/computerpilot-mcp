"""Side-effect-free postcondition evaluators for uncertain operations."""

from __future__ import annotations

import hashlib
import importlib.metadata
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from core.config import resolve_path
from core.errors import ToolError
from core.jobs import JobStore
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


def _process_exited(expected: dict[str, Any]) -> Evidence:
    pid = expected.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        raise ToolError("invalid_postcondition", "process_exited requires a positive pid.")
    exists = psutil.pid_exists(pid)
    return Evidence("process", {"conclusive": True, "satisfied": not exists, "pid": pid, "pid_exists": exists})


def _package_version(expected: dict[str, Any]) -> Evidence:
    name = _required_string(expected, "name")
    wanted = expected.get("version")
    try:
        actual = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return Evidence("package", {"conclusive": True, "satisfied": False, "name": name, "installed": False})
    satisfied = wanted is None or actual == wanted
    return Evidence("package", {"conclusive": True, "satisfied": satisfied, "name": name, "version": actual})


def _git_index_contains(expected: dict[str, Any]) -> Evidence:
    repo = resolve_path(_required_string(expected, "repo"))
    paths = expected.get("paths")
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        raise ToolError("invalid_postcondition", "git_index_contains requires a paths list.")
    completed = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode:
        return Evidence("git", {"conclusive": False, "satisfied": False, "reason": "git_index_unavailable"})
    staged = set(completed.stdout.splitlines())
    return Evidence("git", {"conclusive": True, "satisfied": set(paths) <= staged, "staged_paths": sorted(staged)})


def _job_state(expected: dict[str, Any]) -> Evidence:
    job_id = _required_string(expected, "job_id")
    wanted = str(expected.get("status", "succeeded"))
    try:
        job = JobStore().get(job_id)
    except Exception as exc:
        return Evidence("durable_job", {"conclusive": False, "satisfied": False, "reason": type(exc).__name__})
    return Evidence("durable_job", {"conclusive": True, "satisfied": job["status"] == wanted, "job_id": job_id, "status": job["status"]})


def _http_response(expected: dict[str, Any]) -> Evidence:
    url = _required_string(expected, "url")
    wanted_status = int(expected.get("status", 200))
    try:
        with urllib.request.urlopen(url, timeout=float(expected.get("timeout_sec", 10))) as response:
            body = response.read(1_048_577)
            status = int(response.status)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return Evidence("http", {"conclusive": False, "satisfied": False, "reason": type(exc).__name__})
    wanted_hash = expected.get("sha256")
    actual_hash = hashlib.sha256(body).hexdigest()
    satisfied = status == wanted_status and (wanted_hash is None or actual_hash == wanted_hash)
    return Evidence("http", {"conclusive": True, "satisfied": satisfied, "status": status, "sha256": actual_hash})


def _semantic_runtime_unavailable(expected: dict[str, Any]) -> Evidence:
    return Evidence(
        "semantic_runtime",
        {"conclusive": False, "satisfied": False, "runtime_id": expected.get("runtime_id"), "reason": "runtime_adapter_unavailable"},
    )


def _ui_element_state(expected: dict[str, Any]) -> Evidence:
    from tools.desktop.uia import UI_AUTOMATION, ElementLocator, WindowLocator

    try:
        element = UI_AUTOMATION.get_element(
            WindowLocator(**dict(expected.get("window", {}))),
            ElementLocator(**dict(expected.get("element", {}))),
        )
    except (ToolError, OSError) as exc:
        return Evidence(
            "uia", {"conclusive": False, "satisfied": False, "reason": exc.code if isinstance(exc, ToolError) else type(exc).__name__}
        )
    wanted = dict(expected.get("properties", {}))
    return Evidence(
        "uia", {"conclusive": True, "satisfied": all(element.get(key) == value for key, value in wanted.items()), "element": element}
    )


def _browser_state(expected: dict[str, Any]) -> Evidence:
    from tools.browser.manager import MANAGER

    session_id = expected.get("session_id")
    session = MANAGER._sessions.get(session_id) if isinstance(session_id, str) else None
    if session is None or not MANAGER._session_usable(session):
        return Evidence("browser", {"conclusive": False, "satisfied": False, "reason": "session_unavailable"})
    wanted_url = expected.get("url")
    actual_url = str(session.page.url)
    return Evidence("browser", {"conclusive": True, "satisfied": wanted_url is None or actual_url == wanted_url, "url": actual_url})


@dataclass(frozen=True, slots=True)
class PostconditionDescriptor:
    kind: str
    evaluator: Callable[[dict[str, Any]], Evidence]
    source: str
    safe_for_reconciliation: bool = True


_DESCRIPTORS = {
    item.kind: item
    for item in (
        PostconditionDescriptor("file_exists", _file_exists, "filesystem"),
        PostconditionDescriptor("file_absent", _file_absent, "filesystem"),
        PostconditionDescriptor("file_sha256", _file_sha256, "filesystem"),
        PostconditionDescriptor("git_head", _git_head, "git"),
        PostconditionDescriptor("git_index_contains", _git_index_contains, "git"),
        PostconditionDescriptor("process_identity", _process_identity, "process"),
        PostconditionDescriptor("process_exited", _process_exited, "process"),
        PostconditionDescriptor("job_state", _job_state, "durable_job"),
        PostconditionDescriptor("package_version", _package_version, "package"),
        PostconditionDescriptor("http_response", _http_response, "http"),
        PostconditionDescriptor("ui_element_state", _ui_element_state, "uia"),
        PostconditionDescriptor("browser_state", _browser_state, "browser"),
    )
}


def get_postcondition_descriptor(kind: str) -> PostconditionDescriptor:
    descriptor = _DESCRIPTORS.get(kind)
    if descriptor is None:
        raise ToolError("unsupported_postcondition", f"Unsupported postcondition {kind!r}.")
    return descriptor


def evaluate_postcondition(postcondition: Postcondition) -> Evidence:
    """Evaluate one allowlisted postcondition without replaying its operation."""

    return get_postcondition_descriptor(postcondition.kind).evaluator(postcondition.expected)
