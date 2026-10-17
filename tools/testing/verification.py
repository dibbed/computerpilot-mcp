"""Change-aware orchestration for syntax, lint, type, and test verification."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from functools import partial
from pathlib import Path
from typing import Any, Literal

from core.runtime_capacity import acquire_runtime_capacity
from tools.testing.impact import discover_changed_paths, select_affected_tests
from tools.testing.runners import run_mypy_summary, run_pytest_summary, run_ruff_summary

Check = Literal["syntax", "ruff", "mypy", "pytest"]
_CHECKS: tuple[Check, ...] = ("syntax", "ruff", "mypy", "pytest")


def build_verification_plan(
    repo: Path,
    *,
    changed_paths: list[str] | None = None,
    base: str | None = None,
    affected_only: bool = True,
    fallback_policy: Literal["report", "select_full_suite"] = "select_full_suite",
) -> dict[str, Any]:
    """Build a verification plan without executing repository code."""
    resolved = repo.resolve(strict=False)
    paths = changed_paths if changed_paths is not None else [item.path for item in discover_changed_paths(resolved, base).items]
    impact = select_affected_tests(resolved, changed_paths=paths, base=base, fallback_policy=fallback_policy)
    full = not affected_only or impact["decision"] in {"full_suite", "focused_plus_full_recommended"}
    mode = "full" if full else ("focused" if impact.get("tests") else "checks_only")
    python_targets = [path for path in paths if Path(path).suffix.casefold() in {".py", ".pyi"} and (resolved / path).is_file()]
    return {"changed_paths": paths, "affected": impact, "full": full, "mode": mode, "python_targets": python_targets}


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
        payload.update({key: result[key] for key in ("exit_code", "summary", "diagnostics") if key in result})
    return payload


def _status(result: dict[str, Any]) -> str:
    if result.get("timed_out"):
        return "timed_out"
    if result.get("unavailable") or result.get("exit_code") is None:
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
) -> dict[str, Any]:
    selected_checks = list(_CHECKS if checks is None else checks)
    unknown = sorted(set(selected_checks) - set(_CHECKS))
    if unknown:
        raise ValueError(f"Unknown verification check(s): {', '.join(unknown)}")
    if not 1 <= max_parallel_checks <= 3 or (max_parallel_checks > 1 and (fail_fast or ruff_fix)):
        raise ValueError("parallel verification requires capacity 1..3, fail_fast=false and ruff_fix=false")
    if analyzer_capacity_db is not None and analyzer_capacity_limit < 1:
        raise ValueError("analyzer_capacity_limit must be positive when durable capacity is enabled")
    resolved = repo.resolve(strict=False)
    plan = build_verification_plan(
        resolved, changed_paths=changed_paths, base=base,
        affected_only=affected_only, fallback_policy=fallback_policy,
    )
    paths, impact, full = plan["changed_paths"], plan["affected"], plan["full"]
    python_targets = plan["python_targets"]
    overall_started = time.perf_counter()
    callback_lock = threading.Lock()

    def notify(event: str, data: dict[str, Any]) -> None:
        if stage_callback:
            with callback_lock:
                stage_callback(event, data)

    def analyze(check: Check, targets: list[str]) -> dict[str, Any]:
        remaining = total_timeout_sec - (time.perf_counter() - overall_started)
        if remaining <= 0:
            return {"ok": False, "exit_code": None, "timed_out": True, "summary": "stage timed out", "diagnostics": []}
        timeout = max(0.001, min(stage_timeout_sec, remaining))
        if check == "syntax":
            return _syntax(resolved, targets, diagnostic_cap)
        if check == "ruff":
            return run_ruff_summary(resolved, targets=targets, fix=ruff_fix, timeout_sec=timeout, diagnostic_cap=diagnostic_cap)
        if check == "mypy":
            return run_mypy_summary(resolved, targets=targets, timeout_sec=timeout, diagnostic_cap=diagnostic_cap)
        return run_pytest_summary(
            resolved, targets=targets, maxfail=maxfail, timeout_sec=timeout,
            diagnostic_cap=diagnostic_cap, extra_args=pytest_extra_args,
        )

    tasks: list[tuple[str, list[str], Callable[[], dict[str, Any]] | None]] = []
    for check in _CHECKS:
        if check not in selected_checks:
            continue
        name = ("pytest_full" if full else "pytest_focused") if check == "pytest" else check
        if check == "pytest":
            targets = [] if full else [item["path"] if isinstance(item, dict) else str(item) for item in impact.get("tests", [])]
        elif full and check != "syntax":
            targets = ["."]
        else:
            targets = python_targets
        runnable = bool(targets) or (check == "pytest" and full)
        tasks.append((name, targets, partial(analyze, check, targets) if runnable else None))

    def run(task: tuple[str, list[str], Callable[[], dict[str, Any]] | None]) -> dict[str, Any]:
        name, targets, operation = task
        started = time.perf_counter()
        if operation is None:
            reason = "no_affected_tests" if name.startswith("pytest") else "no_python_targets"
            return _stage(name, "skipped", started, targets=targets, reason=reason)
        if time.perf_counter() - overall_started >= total_timeout_sec:
            return _stage(name, "timed_out", started, targets=targets, reason="total_timeout")
        permit = None
        if analyzer_capacity_db is not None and name != "syntax":
            permit = acquire_runtime_capacity(
                analyzer_capacity_db,
                category="analyzer",
                limit=analyzer_capacity_limit,
                deadline=overall_started + total_timeout_sec,
            )
            if permit is None:
                return _stage(name, "timed_out", started, targets=targets, reason="analyzer_capacity_timeout")
        notify("stage_started", {"name": name})
        try:
            if permit is None:
                result = operation()
            else:
                with permit:
                    result = operation()
        except (ModuleNotFoundError, FileNotFoundError) as exc:
            result = {"ok": False, "exit_code": None, "unavailable": True, "summary": str(exc), "diagnostics": []}
        except Exception as exc:
            result = {"ok": False, "exit_code": None, "unavailable": True, "summary": f"{type(exc).__name__}: {exc}", "diagnostics": []}
        stage = _stage(name, _status(result), started, targets=targets, result=result)
        notify("stage_finished", stage)
        return stage

    stages: list[dict[str, Any]] = []
    if max_parallel_checks > 1:
        if tasks and tasks[0][0] == "syntax":
            stages.append(run(tasks.pop(0)))
            if stages[0]["status"] not in {"passed", "skipped"}:
                stages.extend(_stage(name, "skipped", time.perf_counter(), targets=targets, reason="syntax_failed")
                              for name, targets, _ in tasks)
                tasks = []
        with ThreadPoolExecutor(max_workers=max_parallel_checks, thread_name_prefix="verification") as pool:
            futures = [pool.submit(copy_context().run, run, task) for task in tasks]
            stages.extend(future.result() for future in futures)
    else:
        halted = False
        for task in tasks:
            if halted:
                stages.append(_stage(task[0], "skipped", time.perf_counter(), targets=task[1], reason="fail_fast"))
            else:
                stages.append(run(task))
                halted = fail_fast and stages[-1]["status"] not in {"passed", "skipped"}
    required_unknown = any(stage["status"] in {"timed_out", "unavailable"} for stage in stages)
    return {
        "ok": all(stage["status"] in {"passed", "skipped"} for stage in stages) and not required_unknown,
        "mode": plan["mode"], "changed_paths": paths, "affected": impact, "stages": stages,
        "required_unknown": required_unknown,
        "duration_ms": round((time.perf_counter() - overall_started) * 1000, 3),
    }
