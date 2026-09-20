"""Change-aware orchestration for syntax, lint, type, and test verification."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal

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
    python_targets = [path for path in paths if Path(path).suffix in {".py", ".pyi"} and (resolved / path).is_file()]
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
        if path.suffix not in {".py", ".pyi"} or not path.is_file():
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
) -> dict[str, Any]:
    selected_checks = list(_CHECKS if checks is None else checks)
    unknown = sorted(set(selected_checks) - set(_CHECKS))
    if unknown:
        raise ValueError(f"Unknown verification check(s): {', '.join(unknown)}")
    resolved = repo.resolve(strict=False)
    plan = build_verification_plan(
        resolved,
        changed_paths=changed_paths,
        base=base,
        affected_only=affected_only,
        fallback_policy=fallback_policy,
    )
    paths = plan["changed_paths"]
    impact = plan["affected"]
    full = plan["full"]
    mode = plan["mode"]
    python_targets = plan["python_targets"]
    stages: list[dict[str, Any]] = []
    halted = False
    required_unknown = False
    overall_started = time.perf_counter()

    def bounded_stage_timeout() -> float:
        remaining = total_timeout_sec - (time.perf_counter() - overall_started)
        return max(0.001, min(stage_timeout_sec, remaining))

    def execute(name: str, targets: list[str], operation: Any) -> None:
        nonlocal halted, required_unknown
        started = time.perf_counter()
        if halted:
            stages.append(_stage(name, "skipped", started, targets=targets, reason="fail_fast"))
            return
        if time.perf_counter() - overall_started >= total_timeout_sec:
            stages.append(_stage(name, "timed_out", started, targets=targets, reason="total_timeout"))
            halted = True
            required_unknown = True
            return
        try:
            result = operation()
        except (ModuleNotFoundError, FileNotFoundError) as exc:
            result = {"ok": False, "exit_code": None, "unavailable": True, "summary": str(exc), "diagnostics": []}
        status = _status(result)
        stages.append(_stage(name, status, started, targets=targets, result=result))
        required_unknown = required_unknown or status in {"timed_out", "unavailable"}
        if status != "passed" and fail_fast:
            halted = True

    if "syntax" in selected_checks:
        execute("syntax", python_targets, lambda: _syntax(resolved, python_targets, diagnostic_cap))
    if "ruff" in selected_checks:
        ruff_targets = python_targets or (["."] if full else [])
        if ruff_targets:
            execute(
                "ruff",
                ruff_targets,
                lambda: run_ruff_summary(
                    resolved,
                    targets=ruff_targets,
                    fix=ruff_fix,
                    timeout_sec=bounded_stage_timeout(),
                    diagnostic_cap=diagnostic_cap,
                ),
            )
        else:
            stages.append(_stage("ruff", "skipped", time.perf_counter(), targets=[], reason="no_python_targets"))
    if "mypy" in selected_checks:
        mypy_targets = python_targets or (["."] if full else [])
        if mypy_targets:
            execute(
                "mypy",
                mypy_targets,
                lambda: run_mypy_summary(
                    resolved,
                    targets=mypy_targets,
                    timeout_sec=bounded_stage_timeout(),
                    diagnostic_cap=diagnostic_cap,
                ),
            )
        else:
            stages.append(_stage("mypy", "skipped", time.perf_counter(), targets=[], reason="no_python_targets"))
    if "pytest" in selected_checks:
        test_targets = [] if full else list(impact.get("tests", []))
        name = "pytest_full" if full else "pytest_focused"
        if not full and not test_targets:
            started = time.perf_counter()
            stages.append(_stage(name, "skipped", started, targets=[], reason="no_affected_tests"))
        else:
            execute(
                name,
                test_targets,
                lambda: run_pytest_summary(
                    resolved,
                    targets=test_targets,
                    maxfail=maxfail,
                    timeout_sec=bounded_stage_timeout(),
                    diagnostic_cap=diagnostic_cap,
                ),
            )
    ok = all(stage["status"] in {"passed", "skipped"} for stage in stages) and not required_unknown
    return {
        "ok": ok,
        "mode": mode,
        "changed_paths": paths,
        "affected": impact,
        "stages": stages,
        "required_unknown": required_unknown,
        "duration_ms": round((time.perf_counter() - overall_started) * 1000, 3),
    }
