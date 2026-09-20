"""MCP registration for pytest, Ruff, and mypy summaries."""

from __future__ import annotations

import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from core.artifacts import deliver_text
from core.audit import audit_action
from core.config import PROJECT_ROOT, resolve_path
from core.executor import run_bounded
from core.response import bounded_text
from core.tooling import OPEN_WORLD_WRITE, READ_ONLY, compact_errors
from tools.testing.impact import select_affected_tests

SUMMARY_KEYS = ("passed", "failed", "skipped", "errors", "xfailed", "xpassed", "warnings")


def _python_for(cwd: Path) -> str:
    candidates = [
        cwd / ".venv" / "Scripts" / "python.exe",
        cwd / "venv" / "Scripts" / "python.exe",
        cwd / ".venv" / "bin" / "python",
        cwd / "venv" / "bin" / "python",
    ]
    return str(next((candidate for candidate in candidates if candidate.is_file()), Path(sys.executable)))


def _cwd(value: str | None) -> Path:
    return resolve_path(value) if value else PROJECT_ROOT


def _summary_tail(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(lines[-6:])


def _pytest_counts(text: str) -> dict[str, int]:
    counts = {key: 0 for key in SUMMARY_KEYS}
    aliases = {"error": "errors", "errors": "errors", "warning": "warnings", "warnings": "warnings"}
    for number, label in re.findall(r"(\d+)\s+(passed|failed|skipped|errors?|xfailed|xpassed|warnings?)\b", text):
        key = aliases.get(label, label)
        counts[key] = max(counts[key], int(number))
    return counts


def _junit_summary(path: Path) -> tuple[dict[str, int], list[str]] | None:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        return None
    tests = sum(int(suite.attrib.get("tests", 0)) for suite in suites)
    failed = sum(int(suite.attrib.get("failures", 0)) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", 0)) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", 0)) for suite in suites)
    failures = []
    for case in root.iter("testcase"):
        if case.find("failure") is None and case.find("error") is None:
            continue
        qualified = "::".join(part for part in (case.attrib.get("classname"), case.attrib.get("name")) if part)
        if qualified:
            failures.append(qualified)
        if len(failures) >= 20:
            break
    return {
        "passed": max(tests - failed - errors - skipped, 0),
        "failed": failed,
        "skipped": skipped,
        "errors": errors,
    }, failures


def _count_summary(counts: dict[str, int]) -> str:
    primary = [f"{counts[key]} {key}" for key in ("passed", "failed", "errors", "skipped")]
    extras = [f"{counts[key]} {key}" for key in ("xfailed", "xpassed", "warnings") if counts[key]]
    return ", ".join([*primary, *extras])


def register(mcp: MCPServer) -> None:
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
        selected_targets = targets or []
        selected_extra_args = extra_args or []
        descriptor, report_name = tempfile.mkstemp(prefix="ali-mcp-pytest-", suffix=".xml")
        os.close(descriptor)
        report_path = Path(report_name)
        command = [_python_for(working), "-m", "pytest", "-q", "--disable-warnings", f"--maxfail={maxfail}"]
        if keyword:
            command.extend(["-k", keyword])
        command.extend(selected_targets)
        command.extend(selected_extra_args)
        command.append(f"--junitxml={report_path}")
        audit_action(
            "run_pytest",
            target=working,
            details={"target_count": len(selected_targets), "extra_arg_count": len(selected_extra_args)},
        )
        try:
            result = run_bounded(
                command,
                cwd=working,
                timeout_sec=timeout_sec,
                stdout_limit=None,
                stderr_limit=None,
                output_mode="tail",
                encoding="utf-8",
            )
            junit = _junit_summary(report_path)
        finally:
            report_path.unlink(missing_ok=True)
        combined = result["stdout"]["text"] + "\n" + result["stderr"]["text"]
        counts = _pytest_counts(combined)
        failures = [line.removeprefix("FAILED ").strip() for line in combined.splitlines() if line.startswith("FAILED ")][:20]
        if junit is not None:
            junit_counts, junit_failures = junit
            counts.update(junit_counts)
            failures = junit_failures or failures
        response: dict[str, Any] = {
            "ok": result["ok"],
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
            "duration_ms": result["duration_ms"],
            **counts,
            "failures": failures,
            "summary": _count_summary(counts),
            "output_truncated": bool(result["stdout"]["truncated"] or result["stderr"]["truncated"]),
        }
        if include_output:
            response["output"] = bounded_text(combined, output_max_chars, "tail")
            if delivery != "inline":
                output = response["output"]
                delivered = deliver_text(output["text"], delivery)
                if delivered["delivery"] == "file":
                    output.pop("text")
                    output["artifact"] = delivered
        elif not result["ok"]:
            response["diagnostic_tail"] = _summary_tail(combined)
        audit_action("run_pytest", target=working, outcome="completed", details={"exit_code": result["exit_code"]})
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
        selected_targets = targets or ["."]
        selected_extra_args = extra_args or []
        command = [_python_for(working), "-m", "ruff", "check", "--output-format", "concise"]
        if fix:
            command.append("--fix")
        command.extend(selected_extra_args)
        command.extend(selected_targets)
        audit_action("run_ruff", target=working, details={"target_count": len(selected_targets), "fix": fix})
        result = run_bounded(
            command,
            cwd=working,
            timeout_sec=timeout_sec,
            stdout_limit=None,
            stderr_limit=None,
            output_mode="head",
            encoding="utf-8",
        )
        lines = [line for line in result["stdout"]["text"].splitlines() if re.search(r":\d+:\d+:\s+[A-Z]+\d+\s", line)]
        codes: dict[str, int] = {}
        items: list[dict[str, Any]] = []
        for line in lines:
            match = re.search(r"^(.*?):(\d+):(\d+):\s+([A-Z]+\d+)\s+(.*)$", line)
            if not match:
                continue
            file_name, line_no, column, code, message = match.groups()
            codes[code] = codes.get(code, 0) + 1
            if len(items) < max_items:
                items.append({"file": file_name, "line": int(line_no), "column": int(column), "code": code, "message": message})
        response = {
            "ok": result["ok"],
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
            "duration_ms": result["duration_ms"],
            "violations": len(lines),
            "codes": dict(sorted(codes.items())),
            "items": items,
            "truncated": len(lines) > max_items or result["stdout"]["truncated"],
            "summary": _summary_tail(result["stderr"]["text"] or result["stdout"]["text"]),
        }
        audit_action("run_ruff", target=working, outcome="completed", details={"exit_code": result["exit_code"], "fix": fix})
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
        selected_targets = targets or ["."]
        selected_extra_args = extra_args or []
        command = [
            _python_for(working),
            "-m",
            "mypy",
            "--no-color-output",
            "--show-error-codes",
            *selected_extra_args,
            *selected_targets,
        ]
        audit_action("run_mypy", target=working, details={"target_count": len(selected_targets)})
        result = run_bounded(
            command,
            cwd=working,
            timeout_sec=timeout_sec,
            stdout_limit=None,
            stderr_limit=None,
            output_mode="head",
            encoding="utf-8",
        )
        combined = result["stdout"]["text"] + "\n" + result["stderr"]["text"]
        error_lines = [line for line in combined.splitlines() if ": error:" in line]
        note_lines = [line for line in combined.splitlines() if ": note:" in line]
        response = {
            "ok": result["ok"],
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
            "duration_ms": result["duration_ms"],
            "errors": len(error_lines),
            "notes": len(note_lines),
            "items": error_lines[:max_items],
            "truncated": len(error_lines) > max_items or result["stdout"]["truncated"],
            "summary": _summary_tail(combined),
        }
        audit_action("run_mypy", target=working, outcome="completed", details={"exit_code": result["exit_code"]})
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
