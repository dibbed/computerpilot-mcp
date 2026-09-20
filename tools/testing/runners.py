"""Reusable bounded runners for Python verification tools."""

from __future__ import annotations

import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Literal, TypedDict

from core.artifacts import deliver_text
from core.executor import run_bounded
from core.response import bounded_text

SUMMARY_KEYS = ("passed", "failed", "skipped", "errors", "xfailed", "xpassed", "warnings")


class Diagnostic(TypedDict, total=False):
    file: str
    line: int
    column: int
    severity: Literal["error", "warning", "note"]
    code: str
    message: str
    source: Literal["python", "ruff", "mypy", "pytest"]


def python_for(cwd: Path) -> str:
    candidates = (
        cwd / ".venv" / "Scripts" / "python.exe",
        cwd / "venv" / "Scripts" / "python.exe",
        cwd / ".venv" / "bin" / "python",
        cwd / "venv" / "bin" / "python",
    )
    return str(next((candidate for candidate in candidates if candidate.is_file()), Path(sys.executable)))


def summary_tail(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(lines[-6:])


def _module_unavailable(text: str, module: str) -> bool:
    lowered = text.casefold()
    return "no module named" in lowered and module.casefold() in lowered


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
    failures: list[str] = []
    for case in root.iter("testcase"):
        if case.find("failure") is None and case.find("error") is None:
            continue
        qualified = "::".join(part for part in (case.attrib.get("classname"), case.attrib.get("name")) if part)
        if qualified:
            failures.append(qualified)
        if len(failures) >= 20:
            break
    return {"passed": max(tests - failed - errors - skipped, 0), "failed": failed, "skipped": skipped, "errors": errors}, failures


def _count_summary(counts: dict[str, int]) -> str:
    primary = [f"{counts[key]} {key}" for key in ("passed", "failed", "errors", "skipped")]
    extras = [f"{counts[key]} {key}" for key in ("xfailed", "xpassed", "warnings") if counts[key]]
    return ", ".join([*primary, *extras])


def run_pytest_summary(
    cwd: Path,
    *,
    targets: list[str] | None = None,
    keyword: str | None = None,
    maxfail: int = 1,
    timeout_sec: float = 600,
    include_output: bool = False,
    output_max_chars: int | None = None,
    delivery: Literal["inline", "file", "auto"] = "inline",
    extra_args: list[str] | None = None,
    diagnostic_cap: int = 30,
) -> dict[str, Any]:
    selected_targets = targets or []
    descriptor, report_name = tempfile.mkstemp(prefix="ali-mcp-pytest-", suffix=".xml")
    os.close(descriptor)
    report_path = Path(report_name)
    command = [python_for(cwd), "-m", "pytest", "-q", "--disable-warnings", f"--maxfail={maxfail}"]
    if keyword:
        command.extend(["-k", keyword])
    command.extend(selected_targets)
    command.extend(extra_args or [])
    command.append(f"--junitxml={report_path}")
    try:
        result = run_bounded(
            command, cwd=cwd, timeout_sec=timeout_sec, stdout_limit=None, stderr_limit=None, output_mode="tail", encoding="utf-8"
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
    diagnostics: list[Diagnostic] = [{"severity": "error", "message": failure, "source": "pytest"} for failure in failures[:diagnostic_cap]]
    response: dict[str, Any] = {
        "ok": result["ok"],
        "exit_code": result["exit_code"],
        "timed_out": result["timed_out"],
        "duration_ms": result["duration_ms"],
        **counts,
        "failures": failures,
        "summary": _count_summary(counts),
        "output_truncated": bool(result["stdout"]["truncated"] or result["stderr"]["truncated"]),
        "diagnostics": diagnostics,
    }
    if _module_unavailable(combined, "pytest"):
        response["unavailable"] = True
        response["exit_code"] = None
    if include_output:
        response["output"] = bounded_text(combined, output_max_chars, "tail")
        if delivery != "inline":
            output = response["output"]
            delivered = deliver_text(output["text"], delivery)
            if delivered["delivery"] == "file":
                output.pop("text")
                output["artifact"] = delivered
    elif not result["ok"]:
        response["diagnostic_tail"] = summary_tail(combined)
    return response


def run_ruff_summary(
    cwd: Path,
    *,
    targets: list[str] | None = None,
    fix: bool = False,
    timeout_sec: float = 300,
    max_items: int = 30,
    extra_args: list[str] | None = None,
    diagnostic_cap: int | None = None,
) -> dict[str, Any]:
    selected_targets = targets or ["."]
    command = [python_for(cwd), "-m", "ruff", "check", "--output-format", "concise"]
    if fix:
        command.append("--fix")
    command.extend(extra_args or [])
    command.extend(selected_targets)
    result = run_bounded(
        command, cwd=cwd, timeout_sec=timeout_sec, stdout_limit=None, stderr_limit=None, output_mode="head", encoding="utf-8"
    )
    pattern = re.compile(r"^(.*?):(\d+):(\d+):\s+([A-Z]+\d+)\s+(.*)$")
    parsed = [match for line in result["stdout"]["text"].splitlines() if (match := pattern.search(line))]
    combined = result["stdout"]["text"] + "\n" + result["stderr"]["text"]
    codes: dict[str, int] = {}
    items: list[dict[str, Any]] = []
    diagnostics: list[Diagnostic] = []
    cap = diagnostic_cap if diagnostic_cap is not None else max_items
    for match in parsed:
        file_name, line_no, column, code, message = match.groups()
        codes[code] = codes.get(code, 0) + 1
        item = {"file": file_name, "line": int(line_no), "column": int(column), "code": code, "message": message}
        if len(items) < max_items:
            items.append(item)
        if len(diagnostics) < cap:
            diagnostics.append({**item, "severity": "error", "source": "ruff"})  # type: ignore[typeddict-item]
    response = {
        "ok": result["ok"],
        "exit_code": result["exit_code"],
        "timed_out": result["timed_out"],
        "duration_ms": result["duration_ms"],
        "violations": len(parsed),
        "codes": dict(sorted(codes.items())),
        "items": items,
        "diagnostics": diagnostics,
        "truncated": len(parsed) > max_items or result["stdout"]["truncated"],
        "summary": summary_tail(result["stderr"]["text"] or result["stdout"]["text"]),
    }
    if _module_unavailable(combined, "ruff"):
        response["unavailable"] = True
        response["exit_code"] = None
    return response


def run_mypy_summary(
    cwd: Path,
    *,
    targets: list[str] | None = None,
    timeout_sec: float = 600,
    max_items: int = 30,
    extra_args: list[str] | None = None,
    diagnostic_cap: int | None = None,
) -> dict[str, Any]:
    selected_targets = targets or ["."]
    command = [python_for(cwd), "-m", "mypy", "--no-color-output", "--show-error-codes", *(extra_args or []), *selected_targets]
    result = run_bounded(
        command, cwd=cwd, timeout_sec=timeout_sec, stdout_limit=None, stderr_limit=None, output_mode="head", encoding="utf-8"
    )
    combined = result["stdout"]["text"] + "\n" + result["stderr"]["text"]
    error_lines = [line for line in combined.splitlines() if ": error:" in line]
    note_lines = [line for line in combined.splitlines() if ": note:" in line]
    pattern = re.compile(r"^(.*?):(\d+)(?::(\d+))?: (error|note): (.*?)(?:\s+\[([^]]+)\])?$")
    diagnostics: list[Diagnostic] = []
    cap = diagnostic_cap if diagnostic_cap is not None else max_items
    for line in [*error_lines, *note_lines]:
        match = pattern.match(line)
        if not match or len(diagnostics) >= cap:
            continue
        file_name, line_no, column, severity, message, code = match.groups()
        normalized_severity: Literal["error", "note"] = "error" if severity == "error" else "note"
        item: Diagnostic = {
            "file": file_name,
            "line": int(line_no),
            "severity": normalized_severity,
            "message": message,
            "source": "mypy",
        }
        if column:
            item["column"] = int(column)
        if code:
            item["code"] = code
        diagnostics.append(item)
    response = {
        "ok": result["ok"],
        "exit_code": result["exit_code"],
        "timed_out": result["timed_out"],
        "duration_ms": result["duration_ms"],
        "errors": len(error_lines),
        "notes": len(note_lines),
        "items": error_lines[:max_items],
        "diagnostics": diagnostics,
        "truncated": len(error_lines) > max_items or result["stdout"]["truncated"],
        "summary": summary_tail(combined),
    }
    if _module_unavailable(combined, "mypy"):
        response["unavailable"] = True
        response["exit_code"] = None
    return response
