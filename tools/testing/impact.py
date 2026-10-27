"""Conservative affected-test discovery with explicit multilingual coverage gaps."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from core.errors import ToolError
from core.executor import run_bounded
from tools.git.service import command_arguments, command_environment
from tools.project import service
from tools.project.project_types import ProjectInventory, detect_project_types
from tools.testing.project_runners import RunnerSpec, _BoundedReader, discover_test_runners

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


def _remaining(deadline: float | None) -> float:
    remaining = 60.0 if deadline is None else deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Affected-test selection deadline expired.")
    return remaining


def _git(repo: Path, args: list[str], *, deadline: float | None = None) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise ToolError("git_not_found", "Git is not installed or not on PATH.")
    result = run_bounded(
        [executable, *command_arguments(args)],
        cwd=repo,
        env=command_environment(),
        timeout_sec=min(60, _remaining(deadline)),
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


def discover_changed_paths(repo: Path, base: str | None = None, *, deadline: float | None = None) -> ChangedPaths:
    resolved = repo.resolve(strict=False)
    if not (resolved / ".git").exists():
        # Worktrees can have a .git file; git itself remains the final validation.
        _git(resolved, ["rev-parse", "--show-toplevel"], deadline=deadline)
    rows: list[ChangedPath] = []
    if base:
        rows.extend(_parse_name_status(_git(resolved, ["diff", "--name-status", "-z", base], deadline=deadline)))
    else:
        rows.extend(_parse_name_status(_git(resolved, ["diff", "--name-status", "-z"], deadline=deadline)))
        rows.extend(_parse_name_status(_git(resolved, ["diff", "--cached", "--name-status", "-z"], deadline=deadline)))
    untracked = [
        item for item in _git(resolved, ["ls-files", "--others", "--exclude-standard", "-z"], deadline=deadline).split("\0") if item
    ]
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


def _changed_relative(root: Path, value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 32_767 or "\x00" in value:
        raise ToolError("changed_path_invalid", "Changed paths must be bounded, nonempty strings.")
    path = Path(value.replace("\\", "/"))
    if PureWindowsPath(value).drive and not path.is_absolute():
        raise ToolError("project_scope_escape", "Changed paths must stay inside the selected workspace.")
    try:
        resolved = (root / path).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        raise ToolError("project_scope_escape", "A changed path cannot be resolved.") from None
    if not resolved.is_relative_to(root):
        raise ToolError("project_scope_escape", "Changed paths must stay inside the selected workspace.")
    return resolved.relative_to(root).as_posix()


def _package_root(inventory: ProjectInventory, relative: str) -> Path:
    path = inventory.root / relative
    candidates = [package.root for package in inventory.packages if path.is_relative_to(package.root)]
    return max(candidates, key=lambda item: len(item.parts), default=inventory.root)


def _selection_inputs(
    root: Path, paths: list[str], specs: tuple[RunnerSpec, ...], deadline: float | None,
) -> tuple[tuple[str, str], ...]:
    inputs = set(paths)
    for spec in specs:
        inputs.update(spec.config_paths)
        inputs.update(item.removeprefix("installed:") for item in spec.discovery_evidence if item.startswith("installed:"))
        if spec.entrypoint is not None:
            inputs.add(spec.entrypoint.relative_to(root).as_posix())
    reader = _BoundedReader(root)
    result: list[tuple[str, str]] = []
    for relative in sorted(inputs):
        _remaining(deadline)
        path = root / relative
        if path.is_file():
            digest = hashlib.sha256(reader.read(path)).hexdigest()
        else:
            digest = "directory" if path.is_dir() else "missing"
        result.append((relative, digest))
    return tuple(result)


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
    deadline: float | None = None,
    test_backend: Literal["python", "auto"] = "python",
) -> dict[str, Any]:
    if test_backend not in {"python", "auto"} or fallback_policy not in {"report", "select_full_suite"}:
        raise ToolError("test_selection_invalid", "Unsupported test selection backend or fallback policy.")
    if any(type(value) is not int or not lower <= value <= upper for value, lower, upper in (
        (max_files, 1, 50_000), (max_depth, 0, 20), (max_results, 1, 5_000),
    )):
        raise ToolError("test_selection_invalid", "Test selection limits are outside their supported bounds.")
    if changed_paths is not None and (not isinstance(changed_paths, list) or len(changed_paths) > 1_000):
        raise ToolError("test_selection_invalid", "At most 1000 changed paths may be selected.")
    _remaining(deadline)
    root = repo.resolve(strict=False)
    if not root.is_dir():
        raise NotADirectoryError(f"Repository directory not found: {root}")
    head_sha = None
    if (root / ".git").exists():
        try:
            head_sha = _git(root, ["rev-parse", "--verify", "HEAD"], deadline=deadline).strip()
        except ToolError:
            pass
    base_sha = _git(root, ["rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}"], deadline=deadline).strip() if base else None

    if changed_paths is None:
        changed_records = discover_changed_paths(root, base_sha, deadline=deadline).items
    else:
        changed_records = tuple(ChangedPath(path=PurePosixPath(path).as_posix(), status="explicit") for path in changed_paths)
    changed_records = tuple(ChangedPath(
        _changed_relative(root, item.path), item.status,
        _changed_relative(root, item.old_path) if item.old_path is not None else None,
    ) for item in changed_records)
    normalized_changed = sorted({path for item in changed_records for path in (item.path, item.old_path) if path is not None})
    reasons = sorted({reason for path in normalized_changed if (reason := _risk_reason(path)) is not None})
    nonpython_changes = [path for path in normalized_changed if (
        Path(path).suffix.casefold() not in {".py", ".pyi", ".md", ".rst", ".txt", ".adoc"}
        and PurePosixPath(path).name.casefold() not in {"license", "copying", "notice"}
        and _risk_reason(path) is None
    )]
    inventory = detect_project_types(root, fingerprint_sources=True)
    specs: tuple[RunnerSpec, ...] = ()
    missing: list[str] = list(inventory.missing_inputs)
    active_changes = bool(nonpython_changes or reasons or any(
        Path(path).suffix.casefold() in {".py", ".pyi"} for path in normalized_changed
    ))
    if test_backend == "auto" and active_changes:
        try:
            runners = discover_test_runners(root, inventory)
            specs = runners.runners
        except ToolError as error:
            missing.append(error.code)
    try:
        inputs_before = _selection_inputs(root, normalized_changed, specs, deadline)
    except ToolError as error:
        inputs_before = None
        missing.append(error.code)
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
        _remaining(deadline)
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
    changed_outside_scan = any(path not in module_by_path and (root / path).is_file() for path in executable_changes)
    if changed_outside_scan:
        reasons.append("changed_python_outside_scan")
    unmappable = any(module is None for module in (_module_from_path(path) for path in executable_changes))
    if unmappable:
        reasons.append("unmappable_python_change")

    distance: dict[str, int] = {str(module): 0 for module in changed_modules}
    queue: deque[str] = deque(distance)
    depth_truncated = False
    while queue:
        _remaining(deadline)
        module = queue.popleft()
        depth = distance[module]
        if depth >= max_depth:
            if any(dependent not in distance for dependent in reverse.get(module, ())):
                depth_truncated = True
            continue
        for dependent in sorted(reverse.get(module, ())):
            if dependent not in distance or depth + 1 < distance[dependent]:
                distance[dependent] = depth + 1
                queue.append(dependent)

    evidence: dict[str, set[str]] = defaultdict(set)
    depths: dict[str, int] = {}
    changed_names = {PurePosixPath(path).stem.removeprefix("test_") for path in executable_changes}
    for module, depth in distance.items():
        _remaining(deadline)
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
                if (root / changed_path).is_file():
                    evidence[changed_path].add("changed_test")
                    depths.setdefault(changed_path, 0)
                else:
                    reasons.append("deleted_test_changed")
    for module_path in modules.values():
        _remaining(deadline)
        relative = module_path.relative_to(root).as_posix()
        if _is_test(relative) and PurePosixPath(relative).stem.removeprefix("test_") in changed_names:
            evidence[relative].add("naming_convention")
            depths.setdefault(relative, 0)

    rows: list[dict[str, Any]] = [
        {"path": path, "node_ids": [], "evidence": sorted(values), "dependency_depth": depths.get(path, 0)}
        for path, values in sorted(evidence.items())
    ]
    total_tests = len(rows)
    results_truncated = total_tests > max_results
    rows = rows[:max_results]
    complete = not scan_truncated and not results_truncated and not parse_errors and not changed_outside_scan and not depth_truncated
    if depth_truncated:
        missing.append("dependency_depth_truncated")
    if reasons:
        decision: Decision = "full_suite"
    elif scan_truncated or results_truncated or parse_errors or depth_truncated:
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

    fingerprint: str | None = None
    final_inventory = detect_project_types(root, fingerprint_sources=True)
    try:
        inputs_after = _selection_inputs(root, normalized_changed, specs, deadline)
    except ToolError as error:
        inputs_after = None
        missing.append(error.code)
    if (
        inventory.worktree_id != final_inventory.worktree_id
        or inventory.source_fingerprint != final_inventory.source_fingerprint
        or inputs_before != inputs_after
    ):
        missing.append("stale_snapshot")
    elif inventory.source_fingerprint is not None and inputs_after is not None:
        fingerprint = hashlib.sha256(json.dumps([
            inventory.source_fingerprint, inputs_after, [(item.path, item.status, item.old_path) for item in changed_records],
        ], sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    missing.extend(final_inventory.missing_inputs)
    if fingerprint is None:
        missing.append("source_fingerprint_unavailable")
    package_selections: list[dict[str, Any]] = []
    if nonpython_changes:
        missing.append("semantic_test_coverage_unavailable")
        reasons.append("unknown_coverage")
        if test_backend == "python":
            missing.append("nonpython_test_backend_disabled")
    if test_backend == "auto" and active_changes:
        affected_roots = {_package_root(inventory, path) for path in normalized_changed}
        affected_roots.update(_package_root(inventory, row["path"]) for row in rows)
        if nonpython_changes or reasons or not complete or missing:
            affected_roots.update(package.root for package in inventory.packages)
            affected_roots.update(package.root for package in final_inventory.packages)
            affected_roots.update(spec.package_root for spec in specs)
            if len(affected_roots) > 1:
                missing.append("cross_package_dependencies_unknown")
        if "stale_snapshot" in missing or not inventory.complete or not final_inventory.complete:
            affected_roots.add(root)
        for package_root in sorted(affected_roots, key=lambda item: item.relative_to(root).as_posix()):
            _remaining(deadline)
            package_specs = [spec for spec in specs if spec.package_root == package_root]
            if not package_specs:
                package_specs = [RunnerSpec("unknown", package_root, "", (), None, None, (), False, ("runner_required",))]
            for spec in package_specs:
                missing.extend(spec.missing_inputs)
                full_suite = bool(
                    nonpython_changes or missing or not complete or not spec.complete
                    or decision in {"full_suite", "focused_plus_full_recommended"}
                )
                package_selections.append({
                    "package_root": package_root.relative_to(root).as_posix(), "runner": spec.runner,
                    "tests": [] if full_suite else [row["path"] for row in rows if _package_root(inventory, row["path"]) == package_root],
                    "evidence": [f"changed_path:{path}" for path in normalized_changed if _package_root(inventory, path) == package_root],
                    "confidence": "conservative" if full_suite else "static_resolved",
                    "complete": complete and spec.complete and not nonpython_changes,
                    "fallback_reason": "semantic_coverage_incomplete" if nonpython_changes else (
                        "runner_unavailable" if not spec.complete else ("python_full_suite_required" if full_suite else None)
                    ),
                    "full_suite": full_suite, "available": spec.complete,
                })
    tagged = [(str(_package_root(inventory, row["path"]).relative_to(root)), row["path"], "test", row) for row in rows]
    tagged.extend((item["package_root"], item["runner"], "package", item) for item in package_selections)
    tagged.sort(key=lambda item: item[:3])
    total = total_tests + len(package_selections)
    results_truncated = results_truncated or total > max_results
    retained = tagged[:max_results]
    rows = [item[3] for item in retained if item[2] == "test"]
    package_selections = [item[3] for item in retained if item[2] == "package"]
    if results_truncated:
        missing.append("selection_truncated")
    if not complete:
        missing.append("python_coverage_incomplete")
    unknown_coverage = bool(nonpython_changes or missing)
    if unknown_coverage:
        complete = False
        decision = "full_suite"
        reasons.append("unknown_coverage")
        for selection in package_selections:
            selection["complete"] = False
            selection["full_suite"] = True
            selection["tests"] = []
            if selection["fallback_reason"] is None:
                selection["fallback_reason"] = "selection_evidence_incomplete"

    return {
        "ok": True,
        "repo": str(root),
        "base": base,
        "changed_paths": sorted({item.path for item in changed_records}),
        "changed_count": len({item.path for item in changed_records}),
        "touched_paths": normalized_changed,
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
        "test_backend": test_backend,
        "package_selections": package_selections,
        "unknown_coverage": unknown_coverage,
        "missing_inputs": sorted(set(missing)),
        "base_sha": base_sha, "head_sha": head_sha,
        "worktree_id": inventory.worktree_id,
        "source_fingerprint": fingerprint,
        "total": total, "total_exact": complete,
    }
