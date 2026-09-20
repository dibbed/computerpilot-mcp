"""Conservative Python affected-test discovery for Git worktrees."""

from __future__ import annotations

import shutil
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from core.errors import ToolError
from core.executor import run_bounded
from tools.project import service

Decision = Literal["focused", "focused_plus_full_recommended", "full_suite", "none"]

_FULL_SUITE_NAMES = {
    "pyproject.toml",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "setup.py",
    "requirements.txt",
    "requirements-dev.txt",
    "poetry.lock",
    "uv.lock",
    "pdm.lock",
}


@dataclass(frozen=True, slots=True)
class ChangedPath:
    path: str
    status: str
    old_path: str | None = None


@dataclass(frozen=True, slots=True)
class ChangedPaths:
    items: tuple[ChangedPath, ...]
    base: str | None


def _git(repo: Path, args: list[str]) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise ToolError("git_not_found", "Git is not installed or not on PATH.")
    result = run_bounded(
        [executable, "-c", "core.quotepath=false", *args],
        cwd=repo,
        timeout_sec=60,
        stdout_limit=None,
        stderr_limit=20_000,
        output_mode="head",
        encoding="utf-8",
    )
    if not result["ok"]:
        message = result["stderr"]["text"].strip() or result["stdout"]["text"].strip()
        raise ToolError("git_failed", message or f"Git exited {result['exit_code']}.")
    return str(result["stdout"]["text"])


def _parse_name_status(payload: str) -> list[ChangedPath]:
    fields = payload.split("\0")
    rows: list[ChangedPath] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if not status:
            continue
        if index >= len(fields):
            raise ToolError("git_output_invalid", "Git name-status output ended before a path.")
        if status.startswith(("R", "C")):
            if index + 1 >= len(fields):
                raise ToolError("git_output_invalid", "Git rename output ended before its destination path.")
            old_path, path = fields[index], fields[index + 1]
            index += 2
            rows.append(ChangedPath(path=PurePosixPath(path).as_posix(), status=status, old_path=PurePosixPath(old_path).as_posix()))
        else:
            path = fields[index]
            index += 1
            rows.append(ChangedPath(path=PurePosixPath(path).as_posix(), status=status))
    return rows


def discover_changed_paths(repo: Path, base: str | None = None) -> ChangedPaths:
    resolved = repo.resolve(strict=False)
    if not (resolved / ".git").exists():
        # Worktrees can have a .git file; git itself remains the final validation.
        _git(resolved, ["rev-parse", "--show-toplevel"])
    rows: list[ChangedPath] = []
    if base:
        rows.extend(_parse_name_status(_git(resolved, ["diff", "--name-status", "-z", base])))
    else:
        rows.extend(_parse_name_status(_git(resolved, ["diff", "--name-status", "-z"])))
        rows.extend(_parse_name_status(_git(resolved, ["diff", "--cached", "--name-status", "-z"])))
    untracked = [item for item in _git(resolved, ["ls-files", "--others", "--exclude-standard", "-z"]).split("\0") if item]
    rows.extend(ChangedPath(path=PurePosixPath(path).as_posix(), status="??") for path in untracked)
    merged: dict[str, ChangedPath] = {}
    for row in rows:
        existing = merged.get(row.path)
        if existing is None or row.old_path is not None or existing.status == "??":
            merged[row.path] = row
    return ChangedPaths(items=tuple(sorted(merged.values(), key=lambda item: item.path.casefold())), base=base)


def _is_test(path: str) -> bool:
    pure = PurePosixPath(path)
    parts = {part.casefold() for part in pure.parts}
    name = pure.name.casefold()
    return "tests" in parts or "test" in parts or name.startswith("test_") or name.endswith("_test.py")


def _module_from_path(path: str) -> str | None:
    pure = PurePosixPath(path)
    if pure.suffix not in {".py", ".pyi"}:
        return None
    parts = list(pure.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _risk_reason(path: str) -> str | None:
    pure = PurePosixPath(path)
    name = pure.name.casefold()
    lowered = path.casefold()
    if name == "conftest.py":
        return "shared_fixture_changed"
    if name in _FULL_SUITE_NAMES or (name.startswith("requirements") and name.endswith(".txt")):
        return "test_or_dependency_configuration_changed"
    if lowered in {"tests/plugins.py", "test/plugins.py"} or name in {"pytest_plugin.py", "pytest_plugins.py"}:
        return "shared_test_plugin_changed"
    if name == "__init__.py" and len(pure.parts) == 1:
        return "root_package_initialization_changed"
    return None


def _import_targets(module: str, metadata: Any) -> set[str]:
    targets: set[str] = set()
    for item in metadata.imports:
        parent = module.split(".")[:-1]
        if item.level:
            keep = max(len(parent) - item.level + 1, 0)
            base_parts = parent[:keep]
            if item.source:
                base_parts.extend(item.source.split("."))
            base = ".".join(base_parts)
        else:
            base = item.source or ""
        if item.source is None:
            targets.update(item.names)
        elif item.names:
            targets.update(".".join(part for part in (base, name) if part) for name in item.names if name != "*")
            if base:
                targets.add(base)
        elif base:
            targets.add(base)
    return targets


def select_affected_tests(
    repo: Path,
    *,
    changed_paths: list[str] | None = None,
    base: str | None = None,
    max_files: int = 5_000,
    max_depth: int = 5,
    max_results: int = 500,
    include_changed_tests: bool = True,
    fallback_policy: Literal["report", "select_full_suite"] = "report",
) -> dict[str, Any]:
    root = repo.resolve(strict=False)
    if not root.is_dir():
        raise NotADirectoryError(f"Repository directory not found: {root}")
    if changed_paths is None:
        changed_records = discover_changed_paths(root, base).items
    else:
        changed_records = tuple(ChangedPath(path=PurePosixPath(path).as_posix(), status="explicit") for path in changed_paths)
    normalized_changed = sorted({item.path for item in changed_records})
    reasons = sorted({reason for path in normalized_changed if (reason := _risk_reason(path)) is not None})
    changed_modules = {
        module
        for path in normalized_changed
        if Path(path).suffix.casefold() in {".py", ".pyi"}
        if (module := _module_from_path(path)) is not None
    }

    files, scan_truncated = service.iter_python_files(root, max_files)
    modules: dict[str, Path] = {service.module_name(root, path): path for path in files}
    module_by_path = {path.relative_to(root).as_posix(): module for module, path in modules.items()}
    known_modules = sorted(set(modules) | changed_modules, key=len, reverse=True)
    reverse: dict[str, set[str]] = defaultdict(set)
    parse_errors: list[dict[str, str]] = []
    for module, path in modules.items():
        metadata = service.python_metadata(path)
        if metadata.parse_error:
            if len(parse_errors) < 20:
                parse_errors.append({"file": path.relative_to(root).as_posix(), "error": metadata.parse_error})
            continue
        for target in _import_targets(module, metadata):
            internal = next((candidate for candidate in known_modules if target == candidate or target.startswith(candidate + ".")), None)
            if internal:
                reverse[internal].add(module)

    changed_modules.update(module_by_path[path] for path in normalized_changed if path in module_by_path)
    executable_changes = [path for path in normalized_changed if Path(path).suffix.casefold() in {".py", ".pyi"}]
    unmappable = any(module is None for module in (_module_from_path(path) for path in executable_changes))
    if unmappable:
        reasons.append("unmappable_python_change")

    distance: dict[str, int] = {str(module): 0 for module in changed_modules}
    queue: deque[str] = deque(distance)
    while queue:
        module = queue.popleft()
        depth = distance[module]
        if depth >= max_depth:
            continue
        for dependent in sorted(reverse.get(module, ())):
            if dependent not in distance or depth + 1 < distance[dependent]:
                distance[dependent] = depth + 1
                queue.append(dependent)

    evidence: dict[str, set[str]] = defaultdict(set)
    depths: dict[str, int] = {}
    changed_names = {PurePosixPath(path).stem.removeprefix("test_") for path in executable_changes}
    for module, depth in distance.items():
        module_path = modules.get(module)
        if module_path is None:
            continue
        relative = module_path.relative_to(root).as_posix()
        if not _is_test(relative):
            continue
        if relative in normalized_changed and include_changed_tests:
            evidence[relative].add("changed_test")
        evidence[relative].add("direct_import" if depth == 1 else "reverse_dependency")
        depths[relative] = depth
    if include_changed_tests:
        for changed_path in normalized_changed:
            if _is_test(changed_path) and Path(changed_path).suffix.casefold() in {".py", ".pyi"}:
                evidence[changed_path].add("changed_test")
                depths.setdefault(changed_path, 0)
    for module_path in modules.values():
        relative = module_path.relative_to(root).as_posix()
        if _is_test(relative) and PurePosixPath(relative).stem.removeprefix("test_") in changed_names:
            evidence[relative].add("naming_convention")
            depths.setdefault(relative, 0)

    rows = [
        {"path": path, "node_ids": [], "evidence": sorted(values), "dependency_depth": depths.get(path, 0)}
        for path, values in sorted(evidence.items())
    ]
    results_truncated = len(rows) > max_results
    rows = rows[:max_results]
    complete = not scan_truncated and not results_truncated and not parse_errors
    if reasons:
        decision: Decision = "full_suite"
    elif scan_truncated or results_truncated or parse_errors:
        decision = "focused_plus_full_recommended" if rows else "full_suite"
    elif rows:
        decision = "focused"
    elif executable_changes:
        decision = "full_suite"
        reasons.append("no_reliable_test_mapping")
    else:
        decision = "none"
    if fallback_policy == "select_full_suite" and decision == "focused_plus_full_recommended":
        decision = "full_suite"
        reasons.append("fallback_policy_selected_full_suite")

    return {
        "ok": True,
        "repo": str(root),
        "base": base,
        "changed_paths": normalized_changed,
        "changed_count": len(normalized_changed),
        "tests": rows,
        "test_count": len(rows),
        "decision": decision,
        "full_suite_reasons": sorted(set(reasons)),
        "modules_traversed": len(distance),
        "files_scanned": len(files),
        "scan_truncated": scan_truncated,
        "results_truncated": results_truncated,
        "parse_errors": parse_errors,
        "complete": complete,
    }
