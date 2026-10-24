"""Change-aware orchestration for syntax, lint, type, and test verification."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from contextvars import copy_context
from functools import partial
from pathlib import Path
from typing import Any, Literal

from core.config import SETTINGS
from core.errors import ToolError
from core.recovery import bind_operation_recovery, mark_native_test_dispatch, native_test_dispatched, run_native_operation
from core.resource_claims import coordination_claim, current_resource_capability, managed_resource_claims, path_claim
from core.runtime_capacity import acquire_runtime_capacity
from tools.project.project_types import detect_project_types
from tools.testing.impact import discover_changed_paths, select_affected_tests
from tools.testing.project_runners import RunnerSpec, bind_runner_runtime, discover_test_runners, validate_runner_execution
from tools.testing.runners import run_mypy_summary, run_project_tests, run_pytest_summary, run_ruff_summary

Check = Literal["syntax", "ruff", "mypy", "pytest"]
_CHECKS: tuple[Check, ...] = ("syntax", "ruff", "mypy", "pytest")


def build_verification_plan(
    repo: Path,
    *,
    changed_paths: list[str] | None = None,
    base: str | None = None,
    affected_only: bool = True,
    fallback_policy: Literal["report", "select_full_suite"] = "select_full_suite",
    test_backend: Literal["python", "auto"] = "python",
    deadline: float | None = None,
) -> dict[str, Any]:
    """Build a verification plan without executing repository code."""
    resolved = repo.resolve(strict=False)
    if test_backend not in {"python", "auto"}:
        raise ValueError("Unknown test backend")
    records = discover_changed_paths(resolved, base, deadline=deadline).items if changed_paths is None else ()
    paths = changed_paths if changed_paths is not None else [item.path for item in records]
    snapshot_paths = sorted(set(paths) | {item.old_path for item in records if item.old_path is not None})
    impact = select_affected_tests(
        resolved, changed_paths=snapshot_paths, base=base, fallback_policy=fallback_policy, test_backend=test_backend, deadline=deadline,
    )
    full = not affected_only or impact["decision"] in {"full_suite", "focused_plus_full_recommended"}
    mode = "full" if full else ("focused" if impact.get("tests") else "checks_only")
    python_targets = [path for path in paths if Path(path).suffix.casefold() in {".py", ".pyi"} and (resolved / path).is_file()]
    plan: dict[str, Any] = {
        "changed_paths": paths, "affected": impact, "full": full, "mode": mode, "python_targets": python_targets,
        "test_backend": test_backend, "package_selections": [], "runner_missing_inputs": [],
        "snapshot_input_paths": snapshot_paths,
    }
    if test_backend == "auto":
        inventory = detect_project_types(resolved)
        plan["inventory_complete"] = inventory.complete
        plan["has_python"] = "python" in inventory.language_ids or bool(python_targets)
        plan["runner_missing_inputs"] = list(inventory.missing_inputs)
        try:
            catalog = discover_test_runners(resolved, inventory).runners
        except ToolError as error:
            catalog = ()
            plan["runner_missing_inputs"].append(error.code)
        plan["runner_catalog"] = catalog
        selections = [dict(item) for item in impact.get("package_selections", [])]
        if not affected_only:
            keys = {(item["package_root"], item["runner"]) for item in selections}
            for spec in catalog:
                relative = spec.package_root.relative_to(resolved).as_posix()
                if (relative, spec.runner) not in keys:
                    selections.append({"package_root": relative, "runner": spec.runner, "available": spec.complete})
            represented = {item["package_root"] for item in selections}
            for package in inventory.packages:
                relative = package.root.relative_to(resolved).as_posix()
                if relative not in represented:
                    selections.append({"package_root": relative, "runner": "unknown", "available": False})
        if full:
            for selection in selections:
                selection.update(full_suite=True, tests=[])
        plan["package_selections"] = selections
    return plan


def bind_verification_runners(repo: Path, plan: dict[str, Any]) -> tuple[RunnerSpec, ...]:
    """Bind only the known eligible selections at the authorized native handoff."""
    if plan.get("test_backend") != "auto":
        return ()
    root = repo.resolve(strict=True)
    result: list[RunnerSpec] = []
    for selection in plan["package_selections"]:
        if not selection.get("available") or selection["runner"] == "unknown":
            continue
        package_root = root / selection["package_root"]
        spec = next((item for item in plan.get("runner_catalog", ())
                     if item.package_root == package_root and item.runner == selection["runner"]), None)
        if spec is None:
            continue
        try:
            result.append(bind_runner_runtime(spec, workspace_root=root))
        except ToolError:
            continue
    return tuple(result)


def _stage(
    name: str, status: str, started: float, *, targets: list[str], reason: str = "", result: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "status": status,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "targets": targets,
    }
    if reason:
        payload["reason"] = reason
    if result:
        payload.update({key: result[key] for key in (
            "exit_code", "summary", "diagnostics", "required_unknown", "execution_uncertain", "tests_executed",
        ) if key in result})
    return payload


def _status(result: dict[str, Any]) -> str:
    if result.get("timed_out"):
        return "timed_out"
    if result.get("required_unknown") or result.get("unavailable") or result.get("exit_code") is None:
        return "unavailable"
    return "passed" if result.get("ok") else "failed"


def _syntax(repo: Path, targets: list[str], diagnostic_cap: int) -> dict[str, Any]:
    diagnostics: list[dict[str, Any]] = []
    for target in targets:
        path = repo / target
        if path.suffix.casefold() not in {".py", ".pyi"} or not path.is_file():
            continue
        try:
            compile(path.read_text(encoding="utf-8-sig"), str(path), "exec")
        except (OSError, SyntaxError, UnicodeError) as exc:
            diagnostics.append({"file": target, "severity": "error", "message": str(exc), "source": "python"})
            if len(diagnostics) >= diagnostic_cap:
                break
    return {
        "ok": not diagnostics,
        "exit_code": 0 if not diagnostics else 1,
        "timed_out": False,
        "summary": "syntax ok" if not diagnostics else f"{len(diagnostics)} syntax error(s)",
        "diagnostics": diagnostics,
    }


def verify_changed_repository(
    repo: Path,
    *,
    changed_paths: list[str] | None = None,
    base: str | None = None,
    checks: list[Check] | None = None,
    affected_only: bool = True,
    fallback_policy: Literal["report", "select_full_suite"] = "select_full_suite",
    fail_fast: bool = True,
    maxfail: int = 1,
    stage_timeout_sec: float = 600,
    total_timeout_sec: float = 1800,
    ruff_fix: bool = False,
    diagnostic_cap: int = 30,
    stage_callback: Callable[[str, dict[str, Any]], None] | None = None,
    pytest_extra_args: list[str] | None = None,
    max_parallel_checks: int = 1,
    analyzer_capacity_db: Path | None = None,
    analyzer_capacity_limit: int = 3,
    test_backend: Literal["python", "auto"] = "python",
    runner_specs: tuple[RunnerSpec, ...] | None = None,
) -> dict[str, Any]:
    overall_started = time.monotonic()
    selected_checks = list(_CHECKS if checks is None else checks)
    unknown = sorted(set(selected_checks) - set(_CHECKS))
    if unknown:
        raise ValueError(f"Unknown verification check(s): {', '.join(unknown)}")
    if not 1 <= max_parallel_checks <= 3 or (max_parallel_checks > 1 and (fail_fast or ruff_fix)):
        raise ValueError("parallel verification requires capacity 1..3, fail_fast=false and ruff_fix=false")
    if analyzer_capacity_db is not None and analyzer_capacity_limit < 1:
        raise ValueError("analyzer_capacity_limit must be positive when durable capacity is enabled")
    if test_backend not in {"python", "auto"}:
        raise ValueError("Unknown test backend")
    caps = (600, 1800) if test_backend == "auto" else (3600, 7200)
    if any(type(value) not in {float, int} or not math.isfinite(value) or not 0 < value <= maximum
           for value, maximum in zip((stage_timeout_sec, total_timeout_sec), caps, strict=True)):
        raise ValueError("Verification time budgets must be finite, positive, and within backend limits")
    if type(diagnostic_cap) is not int or not 1 <= diagnostic_cap <= 200 or type(maxfail) is not int or not 1 <= maxfail <= 100:
        raise ValueError("Verification diagnostic and failure limits are invalid")
    if runner_specs is not None and (
        not isinstance(runner_specs, tuple) or len(runner_specs) > 1000
        or any(not isinstance(spec, RunnerSpec) for spec in runner_specs)
    ):
        raise ValueError("Verification runtime references are invalid")
    resolved = repo.resolve(strict=False)
    deadline = overall_started + total_timeout_sec
    capacity_db = analyzer_capacity_db or SETTINGS.workflow_db
    capacity_limit = analyzer_capacity_limit if analyzer_capacity_db is not None else SETTINGS.max_running_analyzers

    def execute() -> dict[str, Any]:
        claims = [path_claim(resolved, "write", scope="subtree")]
        if current_resource_capability() is None:
            claims.insert(0, coordination_claim())
        with managed_resource_claims(
            claims, category="native.testing.verify",
            timeout_sec=max(0.001, deadline - time.monotonic()), db_path=capacity_db,
        ):
            if repo.resolve(strict=False) != resolved:
                raise ToolError("resource_path_changed", "Verification repository changed during admission.")
            return orchestrate()

    def orchestrate() -> dict[str, Any]:
        plan = build_verification_plan(
            resolved, changed_paths=changed_paths, base=base, affected_only=affected_only,
            fallback_policy=fallback_policy, test_backend=test_backend, deadline=deadline,
        )
        paths, impact, full = plan["changed_paths"], plan["affected"], plan["full"]
        python_targets = plan["python_targets"]
        bindings = runner_specs
        if bindings is not None:
            # A persisted pin is authoritative only for the workspace that bound it.
            bindings = tuple(spec for spec in bindings if spec.workspace_root == resolved)
        if test_backend == "auto" and "pytest" in selected_checks and bindings is None:
            bindings = bind_verification_runners(resolved, plan)
        bound = {(str(spec.package_root), spec.runner): spec for spec in bindings or ()}
        explicit_unittest: set[str] = set()
        if test_backend == "auto" and "pytest" in selected_checks:
            for selection in plan["package_selections"]:
                relative = selection["package_root"]
                spec = bound.get((str(resolved / relative), "unittest"))
                candidates = [item for item in plan["runner_catalog"]
                              if item.package_root == resolved / relative and item.runner == "unknown"]
                if selection["runner"] != "unknown" or spec is None or len(candidates) != 1:
                    continue
                try:
                    validate_runner_execution(spec)
                except ToolError:
                    continue
                selection.update(runner="unittest", available=True)
                explicit_unittest.add(relative)
        callback_lock = threading.Lock()

        def notify(event: str, data: dict[str, Any]) -> None:
            if stage_callback:
                with callback_lock:
                    stage_callback(event, data)

        def unavailable() -> dict[str, Any]:
            return {"ok": False, "exit_code": None, "required_unknown": True, "execution_uncertain": False,
                    "tests_executed": False, "summary": "Required test runner is unavailable or stale.", "diagnostics": []}

        def package_test(selection: dict[str, Any], stage_deadline: float) -> dict[str, Any]:
            if time.monotonic() >= stage_deadline:
                return {**unavailable(), "timed_out": True, "summary": "stage timed out"}
            package_root = resolved / selection["package_root"]
            spec = bound.get((str(package_root), selection["runner"]))
            if spec is None or not selection.get("available"):
                return unavailable()
            try:
                validate_runner_execution(spec)
            except ToolError:
                return unavailable()
            result = run_project_tests(
                spec, targets=[] if selection.get("full_suite") else [resolved / path for path in selection.get("tests", [])],
                deadline=stage_deadline, diagnostic_cap=diagnostic_cap,
            )
            result["tests_executed"] = any(result.get(key, 0) for key in ("passed", "failed", "errors", "skipped"))
            result.setdefault("execution_uncertain", bool(result.get("required_unknown") and native_test_dispatched()))
            return result

        def analyze(check: Check, targets: list[str], stage_deadline: float) -> dict[str, Any]:
            remaining = min(deadline, stage_deadline) - time.monotonic()
            if remaining <= 0:
                return {"ok": False, "exit_code": None, "timed_out": True, "summary": "stage timed out", "diagnostics": []}
            timeout = min(stage_timeout_sec, remaining)
            if check == "syntax":
                return _syntax(resolved, targets, diagnostic_cap)
            if check == "ruff":
                return run_ruff_summary(resolved, targets=targets, fix=ruff_fix, timeout_sec=timeout, diagnostic_cap=diagnostic_cap)
            if check == "mypy":
                return run_mypy_summary(resolved, targets=targets, timeout_sec=timeout, diagnostic_cap=diagnostic_cap)
            mark_native_test_dispatch()
            result = run_pytest_summary(
                resolved, targets=targets, maxfail=maxfail, timeout_sec=timeout,
                diagnostic_cap=diagnostic_cap, extra_args=pytest_extra_args,
            )
            result["tests_executed"] = any(result.get(key, 0) for key in ("passed", "failed", "errors", "skipped"))
            return result

        def python_packages(selections: list[dict[str, Any]], stage_deadline: float) -> dict[str, Any]:
            results = [package_test(selection, stage_deadline) for selection in selections]
            return {
                "ok": all(result.get("ok") for result in results),
                "exit_code": 0 if all(result.get("exit_code") == 0 for result in results) else 1,
                "required_unknown": any(result.get("required_unknown") for result in results),
                "timed_out": any(result.get("timed_out") for result in results),
                "execution_uncertain": any(result.get("execution_uncertain") for result in results),
                "tests_executed": any(result.get("tests_executed") for result in results),
                "summary": "Python package test checks completed.",
                "diagnostics": [item for result in results for item in result.get("diagnostics", [])][:diagnostic_cap],
            }

        tasks: list[tuple[str, list[str], Callable[[float], dict[str, Any]] | None]] = []
        selections = plan["package_selections"]
        for check in _CHECKS:
            if check not in selected_checks:
                continue
            name = ("pytest_full" if full else "pytest_focused") if check == "pytest" else check
            if test_backend == "auto" and check == "pytest":
                python = [selection for selection in selections if selection["runner"] in {"pytest", "unittest"}]
                if python:
                    tasks.append((name, [], partial(python_packages, python)))
                for selection in selections:
                    if selection["runner"] in {"pytest", "unittest"}:
                        continue
                    stage_name = f"package_test:{selection['runner']}:{selection['package_root']}"
                    def foreign(stage_deadline: float, item: dict[str, Any] = selection) -> dict[str, Any]:
                        return package_test(item, stage_deadline)
                    tasks.append((stage_name, selection.get("tests", []), foreign))
                continue
            if check == "pytest":
                targets = [] if full else [item["path"] if isinstance(item, dict) else str(item) for item in impact.get("tests", [])]
            elif full and check != "syntax":
                targets = ["."] if test_backend == "python" or plan.get("has_python") else []
            else:
                targets = python_targets
            runnable = bool(targets) or (check == "pytest" and full)
            tasks.append((name, targets, partial(analyze, check, targets) if runnable else None))

        def run(task: tuple[str, list[str], Callable[[float], dict[str, Any]] | None]) -> dict[str, Any]:
            name, targets, operation = task
            started = time.perf_counter()
            stage_deadline = min(deadline, time.monotonic() + stage_timeout_sec)
            if operation is None:
                reason = "no_affected_tests" if name.startswith("pytest") else "no_python_targets"
                return _stage(name, "skipped", started, targets=targets, reason=reason)
            if time.monotonic() >= deadline:
                return _stage(name, "timed_out", started, targets=targets, reason="total_timeout")

            def execute_stage() -> dict[str, Any]:
                permit = None
                try:
                    if name != "syntax":
                        permit = acquire_runtime_capacity(capacity_db, category="analyzer", limit=capacity_limit, deadline=stage_deadline)
                        if permit is None:
                            return _stage(name, "timed_out", started, targets=targets, reason="analyzer_capacity_timeout")
                    with permit if permit is not None else nullcontext():
                        notify("stage_started", {"name": name})
                        result = operation(stage_deadline)
                    result.setdefault("execution_uncertain", native_test_dispatched() and _status(result) in {"unavailable", "timed_out"})
                except Exception as exc:
                    if isinstance(exc, ToolError) and exc.code == "process_cleanup_incomplete":
                        raise
                    result = {
                        "ok": False, "exit_code": None, "unavailable": True, "required_unknown": True,
                        "execution_uncertain": native_test_dispatched(),
                        "summary": (f"{type(exc).__name__}: {exc}" if test_backend == "python" else "Verification stage is unavailable."),
                        "diagnostics": [],
                    }
                stage = _stage(name, _status(result), started, targets=targets, result=result)
                notify("stage_finished", stage)
                return stage

            return run_native_operation("verify_changes", str(resolved), execute_stage)

        stages: list[dict[str, Any]] = []
        if max_parallel_checks > 1:
            if tasks and tasks[0][0] == "syntax":
                stages.append(run(tasks.pop(0)))
                if stages[0]["status"] not in {"passed", "skipped"}:
                    stages.extend(_stage(name, "skipped", time.perf_counter(), targets=targets, reason="syntax_failed")
                                  for name, targets, _ in tasks)
                    tasks = []
            with ThreadPoolExecutor(max_workers=max_parallel_checks, thread_name_prefix="verification") as pool:
                futures = [pool.submit(copy_context().run, bind_operation_recovery(partial(run, task))) for task in tasks]
                stages.extend(future.result() for future in futures)
        else:
            halted = False
            for task in tasks:
                if halted:
                    stages.append(_stage(task[0], "skipped", time.perf_counter(), targets=task[1], reason="fail_fast"))
                else:
                    stages.append(run(task))
                    halted = fail_fast and stages[-1]["status"] not in {"passed", "skipped"}
        required_unknown = any(stage["status"] in {"timed_out", "unavailable"} or stage.get("required_unknown") for stage in stages)
        if test_backend == "auto" and "pytest" in selected_checks:
            missing = set(impact.get("missing_inputs", [])) | set(plan["runner_missing_inputs"])
            if explicit_unittest and all(selection["runner"] != "unknown" for selection in selections):
                missing.discard("runner_required")
            catalog = set()
            for spec in plan["runner_catalog"]:
                relative = spec.package_root.relative_to(resolved).as_posix()
                runner = "unittest" if spec.runner == "unknown" and relative in explicit_unittest else spec.runner
                catalog.add((relative, runner))
            selected = {(selection["package_root"], selection["runner"]) for selection in selections}
            full_coverage = bool(
                selections and catalog <= selected and plan["inventory_complete"] and impact.get("source_fingerprint")
                and not impact.get("scan_truncated") and not impact.get("results_truncated") and not impact.get("parse_errors")
                and all(selection.get("full_suite") and selection.get("available") for selection in selections)
                and all(stage["status"] == "passed" for stage in stages if stage["name"].startswith(("pytest_", "package_test:")))
            )
            if missing - {"semantic_test_coverage_unavailable", "cross_package_dependencies_unknown"} or (missing and not full_coverage):
                required_unknown = True
            try:
                fresh = select_affected_tests(
                    resolved, changed_paths=plan["snapshot_input_paths"], base=base,
                    fallback_policy=fallback_policy, test_backend="auto", deadline=deadline,
                )
            except (ToolError, TimeoutError):
                fresh = {}
            if impact.get("source_fingerprint") != fresh.get("source_fingerprint") or fresh.get("source_fingerprint") is None:
                required_unknown = True
        tests_executed = any(stage.get("tests_executed") for stage in stages)
        return {
            "ok": all(stage["status"] in {"passed", "skipped"} for stage in stages) and not required_unknown,
            "mode": plan["mode"], "changed_paths": paths, "affected": impact, "stages": stages,
            "required_unknown": required_unknown, "execution_uncertain": any(stage.get("execution_uncertain") for stage in stages),
            "tests_executed": tests_executed, "claim_testing": tests_executed,
            "duration_ms": round((time.monotonic() - overall_started) * 1000, 3),
        }

    return run_native_operation("verify_changes", str(resolved), execute)
