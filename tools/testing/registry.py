"""MCP registration for Python testing and verification workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from core.artifacts import deliver_text
from core.audit import audit_action
from core.config import PROJECT_ROOT, resolve_path
from core.tooling import OPEN_WORLD_WRITE, READ_ONLY, compact_errors
from tools.testing import runners as _runners
from tools.testing.diagnostics import merge_diagnostics
from tools.testing.impact import select_affected_tests
from tools.testing.runners import run_mypy_summary, run_pytest_summary, run_ruff_summary
from tools.testing.verification import Check, verify_changed_repository

# Kept as compatibility aliases for existing local consumers.
_junit_summary = _runners._junit_summary
_pytest_counts = _runners._pytest_counts


def _cwd(value: str | None) -> Path:
    return resolve_path(value) if value else PROJECT_ROOT


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("collect_diagnostics")
    def collect_diagnostics(
        cwd: Annotated[str | None, Field(max_length=32_767)] = None,
        backends: Annotated[list[Literal["ruff", "mypy", "pytest"]] | None, Field(max_length=3)] = None,
        targets: Annotated[list[str] | None, Field(max_length=100)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=3_600)] = 600,
        max_items: Annotated[int, Field(ge=1, le=1_000)] = 200,
    ) -> dict[str, Any]:
        """Run selected analyzers and return one stable, deduplicated diagnostic stream."""
        working = _cwd(cwd)
        selected = backends or ["ruff", "mypy", "pytest"]
        stages: list[dict[str, Any]] = []
        groups: list[list[dict[str, Any]]] = []
        for backend in selected:
            if backend == "ruff":
                result = run_ruff_summary(working, targets=targets, timeout_sec=timeout_sec, diagnostic_cap=max_items)
            elif backend == "mypy":
                result = run_mypy_summary(working, targets=targets, timeout_sec=timeout_sec, diagnostic_cap=max_items)
            else:
                result = run_pytest_summary(working, targets=targets, timeout_sec=timeout_sec, diagnostic_cap=max_items)
            groups.append(result.get("diagnostics", []))
            stages.append(
                {
                    "backend": backend,
                    "ok": bool(result.get("ok")),
                    "unavailable": bool(result.get("unavailable")),
                    "timed_out": bool(result.get("timed_out")),
                    "diagnostic_count": len(result.get("diagnostics", [])),
                }
            )
        diagnostics, truncated = merge_diagnostics(groups, cap=max_items)
        required_unknown = any(stage["unavailable"] or stage["timed_out"] for stage in stages)
        return {
            "ok": all(stage["ok"] for stage in stages) and not required_unknown,
            "diagnostics": diagnostics,
            "count": len(diagnostics),
            "truncated": truncated,
            "stages": stages,
            "required_unknown": required_unknown,
        }

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("run_pytest")
    def run_pytest(
        cwd: Annotated[str | None, Field(max_length=32_767)] = None,
        targets: Annotated[list[str] | None, Field(max_length=100)] = None,
        keyword: Annotated[str | None, Field(max_length=1_000)] = None,
        maxfail: Annotated[int, Field(ge=1, le=100)] = 1,
        timeout_sec: Annotated[float, Field(gt=0, le=3_600)] = 600,
        include_output: bool = False,
        output_max_chars: Annotated[int | None, Field(ge=1)] = None,
        delivery: Literal["inline", "file", "auto"] = "inline",
        extra_args: Annotated[list[str] | None, Field(max_length=50)] = None,
    ) -> dict[str, Any]:
        """Run pytest quietly and return counts, duration, failures, and optional full output."""
        working = _cwd(cwd)
        audit_action("run_pytest", target=working, details={"target_count": len(targets or []), "extra_arg_count": len(extra_args or [])})
        response = run_pytest_summary(
            working,
            targets=targets,
            keyword=keyword,
            maxfail=maxfail,
            timeout_sec=timeout_sec,
            include_output=include_output,
            output_max_chars=output_max_chars,
            delivery=delivery,
            extra_args=extra_args,
        )
        audit_action("run_pytest", target=working, outcome="completed", details={"exit_code": response["exit_code"]})
        return response

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("run_ruff")
    def run_ruff(
        cwd: Annotated[str | None, Field(max_length=32_767)] = None,
        targets: Annotated[list[str] | None, Field(max_length=100)] = None,
        fix: bool = False,
        timeout_sec: Annotated[float, Field(gt=0, le=1_800)] = 300,
        max_items: Annotated[int, Field(ge=1, le=200)] = 30,
        extra_args: Annotated[list[str] | None, Field(max_length=50)] = None,
    ) -> dict[str, Any]:
        """Run Ruff and return violation counts plus a paginated sample."""
        working = _cwd(cwd)
        audit_action("run_ruff", target=working, details={"target_count": len(targets or ["."]), "fix": fix})
        response = run_ruff_summary(working, targets=targets, fix=fix, timeout_sec=timeout_sec, max_items=max_items, extra_args=extra_args)
        audit_action("run_ruff", target=working, outcome="completed", details={"exit_code": response["exit_code"], "fix": fix})
        return response

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("run_mypy")
    def run_mypy(
        cwd: Annotated[str | None, Field(max_length=32_767)] = None,
        targets: Annotated[list[str] | None, Field(max_length=100)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=3_600)] = 600,
        max_items: Annotated[int, Field(ge=1, le=200)] = 30,
        extra_args: Annotated[list[str] | None, Field(max_length=50)] = None,
    ) -> dict[str, Any]:
        """Run mypy and return error and note counts with a paginated diagnostic sample."""
        working = _cwd(cwd)
        audit_action("run_mypy", target=working, details={"target_count": len(targets or ["."])})
        response = run_mypy_summary(working, targets=targets, timeout_sec=timeout_sec, max_items=max_items, extra_args=extra_args)
        audit_action("run_mypy", target=working, outcome="completed", details={"exit_code": response["exit_code"]})
        return response

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("affected_tests")
    def affected_tests(
        repo: Annotated[str | None, Field(max_length=32_767)] = None,
        changed_paths: Annotated[list[str] | None, Field(max_length=1_000)] = None,
        base: Annotated[str | None, Field(min_length=1, max_length=500)] = None,
        max_files: Annotated[int, Field(ge=1, le=50_000)] = 5_000,
        max_depth: Annotated[int, Field(ge=0, le=20)] = 5,
        max_results: Annotated[int, Field(ge=1, le=5_000)] = 500,
        include_changed_tests: bool = True,
        fallback_policy: Literal["report", "select_full_suite"] = "report",
    ) -> dict[str, Any]:
        """Select tests affected by changed Python modules and report conservative full-suite fallbacks."""
        return select_affected_tests(
            _cwd(repo),
            changed_paths=changed_paths,
            base=base,
            max_files=max_files,
            max_depth=max_depth,
            max_results=max_results,
            include_changed_tests=include_changed_tests,
            fallback_policy=fallback_policy,
        )

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("verify_changes")
    def verify_changes(
        repo: Annotated[str | None, Field(max_length=32_767)] = None,
        changed_paths: Annotated[list[str] | None, Field(max_length=1_000)] = None,
        base: Annotated[str | None, Field(min_length=1, max_length=500)] = None,
        checks: Annotated[list[Check] | None, Field(max_length=4)] = None,
        affected_only: bool = True,
        fallback_policy: Literal["report", "select_full_suite"] = "select_full_suite",
        fail_fast: bool = True,
        maxfail: Annotated[int, Field(ge=1, le=100)] = 1,
        stage_timeout_sec: Annotated[float, Field(gt=0, le=3_600)] = 600,
        total_timeout_sec: Annotated[float, Field(gt=0, le=7_200)] = 1_800,
        ruff_fix: bool = False,
        diagnostic_cap: Annotated[int, Field(ge=1, le=200)] = 30,
        delivery: Literal["inline", "file", "auto"] = "inline",
    ) -> dict[str, Any]:
        """Verify changed Python code with syntax, Ruff, mypy, and the narrowest sound pytest scope."""
        working = _cwd(repo)
        audit_action(
            "verify_changes",
            target=working,
            details={
                "path_count": len(changed_paths or []),
                "check_count": len(checks or []),
                "affected_only": affected_only,
                "ruff_fix": ruff_fix,
            },
        )
        response = verify_changed_repository(
            working,
            changed_paths=changed_paths,
            base=base,
            checks=checks,
            affected_only=affected_only,
            fallback_policy=fallback_policy,
            fail_fast=fail_fast,
            maxfail=maxfail,
            stage_timeout_sec=stage_timeout_sec,
            total_timeout_sec=total_timeout_sec,
            ruff_fix=ruff_fix,
            diagnostic_cap=diagnostic_cap,
        )
        if delivery != "inline":
            delivered = deliver_text(json.dumps(response, ensure_ascii=False, indent=2), delivery)
            if delivered["delivery"] == "file":
                response["artifact"] = delivered
        audit_action("verify_changes", target=working, outcome="completed", details={"ok": response["ok"], "mode": response["mode"]})
        return response
