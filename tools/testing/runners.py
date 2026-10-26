"""Reusable bounded runners for Python verification tools and package tests."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Literal

from core.artifacts import deliver_text
from core.errors import ToolError
from core.executor import run_bounded
from core.process_ownership import NativeOwnershipRequiredError
from core.recovery import mark_native_test_dispatch, run_native_operation
from core.resource_claims import managed_path_claims, resource_requires_native_ownership
from core.response import bounded_text
from tools.testing.diagnostics import Diagnostic
from tools.testing.project_runners import RunnerSpec, _reject_constant, _unique_object, validate_runner_execution

SUMMARY_KEYS = ("passed", "failed", "skipped", "errors", "xfailed", "xpassed", "warnings")


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


_REPORT_BYTES = 4 * 1024 * 1024
_SUPPORTED_RUNNERS = {"jest": 29, "vitest": 3, "phpunit": 11, "pest": 3}


def _runner_error(code: str) -> ToolError:
    return ToolError(code, "The selected test execution inputs are invalid or unsupported.")


def _runner_targets(spec: RunnerSpec, targets: list[Path]) -> list[Path]:
    selected: list[Path] = []
    if len(targets) > 1000:
        raise _runner_error("test_runner_argv_limit")
    for path in targets:
        try:
            absolute = (spec.package_root / path).resolve(strict=True)
            if not absolute.is_relative_to(spec.package_root) or not (absolute.is_file() or absolute.is_dir()):
                raise _runner_error("test_target_scope_escape")
            if "\x00" in str(absolute):
                raise _runner_error("invalid_test_target")
        except (OSError, RuntimeError):
            raise _runner_error("invalid_test_target") from None
        selected.append(absolute)
    if spec.runner == "unittest" and not selected:
        directories = [value.removeprefix("tests_directory:") for value in spec.discovery_evidence
                       if value.startswith("tests_directory:")]
        if len(directories) == 1:
            return _runner_targets(spec, [Path(directories[0])])
    return selected


def build_runner_argv(spec: RunnerSpec, targets: list[Path], report: Path) -> list[str]:
    """Build direct, fixed runner argv; an execution-bound spec is required."""
    if spec.runtime is None or not spec.runtime.path.is_absolute() or not report.is_absolute() or "\x00" in str(report):
        raise _runner_error("invalid_test_runner")
    if spec.runtime.path.suffix.casefold() in {".cmd", ".bat", ".ps1"}:
        raise _runner_error("invalid_test_runner")
    selected = _runner_targets(spec, targets)
    command = [str(spec.runtime.path)]
    if spec.runner in _SUPPORTED_RUNNERS:
        match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", spec.version or "")
        if match is None or int(match[1]) != _SUPPORTED_RUNNERS[spec.runner]:
            raise _runner_error("unsupported_runner_version")
        if (
            spec.entrypoint is None or not spec.entrypoint.is_absolute()
            or spec.entrypoint.suffix.casefold() in {".cmd", ".bat", ".ps1"}
        ):
            raise _runner_error("invalid_test_runner")
        command.append(str(spec.entrypoint))
    if spec.runner == "jest":
        command.extend(["--runInBand", "--json", "--outputFile", str(report)])
        if selected:
            command.append("--runTestsByPath")
    elif spec.runner == "vitest":
        command.extend(["run", "--reporter=json", "--outputFile", str(report), "--maxWorkers=1", "--no-file-parallelism"])
    elif spec.runner in {"phpunit", "pest"}:
        if len(selected) > 1:
            raise _runner_error("multiple_php_targets_require_separate_runs")
        command.extend(["--log-junit", str(report)])
    elif spec.runner == "pytest":
        command.extend(["-m", "pytest", "-q", "--disable-warnings", f"--junitxml={report}"])
    elif spec.runner == "unittest":
        approved = [spec.package_root / value.removeprefix("tests_directory:") for value in spec.discovery_evidence
                    if value.startswith("tests_directory:")]
        if len(selected) != 1 or not selected[0].is_dir() or selected[0] not in approved:
            raise _runner_error("unittest_requires_test_directory")
        command.extend(["-m", "unittest", "discover", "-s"])
    else:
        raise _runner_error("unsupported_test_runner")
    command.extend(str(path) for path in selected)
    if len(command) > 1000 or len(subprocess.list2cmdline(command).encode("utf-16-le")) // 2 + 1 > 32768:
        raise _runner_error("test_runner_argv_limit")
    return command


def _read_runner_report(report: Path, owned_root: Path, identity: tuple[int, int]) -> bytes:
    try:
        directory = owned_root.lstat()
        if (
            not stat.S_ISDIR(directory.st_mode) or owned_root.resolve(strict=True) != owned_root
            or (directory.st_dev, directory.st_ino) != identity or report.parent != owned_root
            or report.resolve(strict=True).parent != owned_root
        ):
            raise ValueError
        before = report.lstat()
        if (
            not stat.S_ISREG(before.st_mode) or before.st_size > _REPORT_BYTES or before.st_nlink != 1
            or int(getattr(before, "st_file_attributes", 0)) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise ValueError
        descriptor = os.open(report, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            data = stream.read(_REPORT_BYTES + 1)
            finished = os.fstat(stream.fileno())
        after = report.lstat()
        identities = {(item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns) for item in (before, opened, finished, after)}
        directory_after = owned_root.lstat()
        if (
            len(identities) != 1 or len(data) != before.st_size or len(data) > _REPORT_BYTES
            or (directory_after.st_dev, directory_after.st_ino) != identity
            or report.resolve(strict=True).parent != owned_root
        ):
            raise ValueError
        return data
    except (OSError, ValueError, RuntimeError):
        raise _runner_error("invalid_test_report") from None


def _json_report_counts(data: bytes, runner: str) -> tuple[dict[str, int], bool]:
    document = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    pending: list[tuple[object, int]] = [(document, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > 16:
            raise ValueError
        if isinstance(value, dict):
            if len(value) > 10000:
                raise ValueError
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            if len(value) > 10000:
                raise ValueError
            pending.extend((child, depth + 1) for child in value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError
    if not isinstance(document, dict) or type(document.get("success")) is not bool:
        raise ValueError
    keys = ("numTotalTests", "numPassedTests", "numFailedTests", "numPendingTests")
    numbers = [document.get(key) for key in keys]
    if any(type(value) is not int or not 0 <= value <= 1000000 for value in numbers):
        raise ValueError
    total, passed, failed, skipped = (document[key] for key in keys)
    todo = document.get("numTodoTests", 0)
    if type(todo) is not int or todo < 0 or passed + failed + skipped + todo != total:
        raise ValueError
    if "testResults" in document:
        results = document["testResults"]
        if not isinstance(results, list):
            raise ValueError
        observed = {"passed": 0, "failed": 0, "skipped": 0, "todo": 0}
        statuses = {"passed": "passed", "failed": "failed", "skipped": "skipped", "disabled": "skipped", "todo": "todo"}
        # Vitest maps unfinished tasks to pending; Jest uses pending for skipped tests.
        if runner == "jest":
            statuses["pending"] = "skipped"
        for file_result in results:
            if not isinstance(file_result, dict) or not isinstance(file_result.get("assertionResults"), list):
                raise ValueError
            for assertion in file_result["assertionResults"]:
                if not isinstance(assertion, dict) or assertion.get("status") not in statuses:
                    raise ValueError
                observed[statuses[assertion["status"]]] += 1
        if observed != {"passed": passed, "failed": failed, "skipped": skipped, "todo": todo}:
            raise ValueError
    return {"passed": passed, "failed": failed, "errors": 0, "skipped": skipped + todo}, document["success"]


def _xml_report_counts(data: bytes) -> tuple[dict[str, int], bool]:
    text = data.decode("utf-8-sig", errors="strict")
    if "\x00" in text or "<!doctype" in text.casefold() or "<!entity" in text.casefold():
        raise ValueError
    root = ET.fromstring(text)
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    cases: dict[ET.Element, str] = {}
    suites: list[ET.Element] = []
    pending = [(root, 0)]
    seen = 0
    while pending:
        element, depth = pending.pop()
        seen += 1
        if depth > 16 or seen > 10000:
            raise ValueError
        pending.extend((child, depth + 1) for child in element)
        if element.tag in {"testsuite", "testsuites"}:
            suites.append(element)
        if element.tag == "testcase":
            key = next((name for tag, name in (("error", "errors"), ("failure", "failed"), ("skipped", "skipped"))
                        if element.find(tag) is not None), "passed")
            counts[key] += 1
            cases[element] = key
    for suite in suites:
        local = dict.fromkeys(counts, 0)
        for case in suite.iter("testcase"):
            local[cases[case]] += 1
        for attribute, count_key in (("tests", None), ("failures", "failed"), ("errors", "errors"), ("skipped", "skipped")):
            if attribute in suite.attrib:
                number = suite.attrib[attribute]
                if not number.isdecimal() or int(number) != (sum(local.values()) if count_key is None else local[count_key]):
                    raise ValueError
    return counts, not counts["failed"] and not counts["errors"]


def _test_environment(home: Path) -> dict[str, str]:
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE"}
    # run_bounded merges env; explicitly empty inherited secrets and loader injection variables.
    result = {key: value if key.upper() in allowed else "" for key, value in os.environ.items()}
    # Empty home variables break Node module resolution; never expose the operator's home.
    result.update(HOME=str(home), USERPROFILE=str(home))
    result["PYTHONNOUSERSITE"] = "1"
    return result


def run_project_tests(
    spec: RunnerSpec, *, targets: list[Path], deadline: float, diagnostic_cap: int,
) -> dict[str, Any]:
    """Execute one bounded package stage; deadline is absolute time.monotonic()."""
    started = time.monotonic()
    if (
        type(deadline) not in {int, float} or not math.isfinite(deadline)
        or type(diagnostic_cap) is not int or not 0 <= diagnostic_cap <= 200
    ):
        raise _runner_error("invalid_test_runner_budget")
    deadline = min(deadline, started + 600)
    return run_native_operation("verify_changes", str(spec.package_root), lambda: _run_project_tests(
        spec, targets=targets, deadline=deadline, diagnostic_cap=diagnostic_cap, started=started,
    ))


def _run_project_tests(
    spec: RunnerSpec, *, targets: list[Path], deadline: float, diagnostic_cap: int, started: float,
) -> dict[str, Any]:
    validate_runner_execution(spec)
    selected = _runner_targets(spec, targets)
    # PHP CLIs differ on multiple path operands; run each explicitly without inventing regex filters.
    batches = [[path] for path in selected] if spec.runner in {"phpunit", "pest"} and selected else [selected]
    totals = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    response: dict[str, Any] = {
        "ok": True, "exit_code": None, "timed_out": False, "required_unknown": False,
        "execution_uncertain": False, "diagnostics": [],
    }
    with managed_path_claims(
        spec.package_root, category="native.testing.project", scope="subtree", timeout_sec=max(0.001, deadline - started),
    ):
        with tempfile.TemporaryDirectory(prefix="computerpilot-tests-") as temporary:
            owned_root = Path(temporary).resolve(strict=True)
            info = owned_root.lstat()
            identity = (info.st_dev, info.st_ino)
            for index, batch in enumerate(batches):
                report = owned_root / f"report-{index}.{'json' if spec.runner in {'jest', 'vitest'} else 'xml'}"
                command = build_runner_argv(spec, batch, report)
                validate_runner_execution(spec)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    response.update(ok=False, timed_out=True, required_unknown=True)
                    break
                resource_requires_native_ownership()
                mark_native_test_dispatch()
                try:
                    result = run_bounded(
                        command, cwd=spec.package_root, timeout_sec=min(600, remaining),
                        stdout_limit=65536, stderr_limit=65536, output_mode="tail", encoding="utf-8", env=_test_environment(owned_root),
                    )
                except Exception as exc:
                    if isinstance(exc, ToolError) and exc.code == "process_cleanup_incomplete":
                        raise ToolError("process_cleanup_incomplete", "Test process cleanup could not be confirmed.") from None
                    # Provider/OS exceptions can contain credentials and arbitrary project text.
                    response.update(
                        ok=False, exit_code=None, required_unknown=True,
                        execution_uncertain=not isinstance(exc, NativeOwnershipRequiredError),
                    )
                    break
                response["exit_code"] = result["exit_code"] if response["exit_code"] in {None, 0} else response["exit_code"]
                if result["timed_out"] or not result.get("capture_complete", True):
                    response.update(ok=False, timed_out=bool(result["timed_out"]), required_unknown=True, execution_uncertain=True)
                    break
                try:
                    if spec.runner == "unittest":
                        output = result["stderr"]["text"]
                        match = re.search(r"(?m)^Ran (\d+) tests? in [0-9.]+s\s*$", output)
                        if match is None or result["stderr"]["truncated"]:
                            raise ValueError
                        count = int(match[1])
                        status = re.search(r"(?m)^(OK|FAILED)(?: \(([^\r\n]*)\))?\s*$", output)
                        if status is None or count > 1000000:
                            raise ValueError
                        counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
                        for field in status[2].split(", ") if status[2] else []:
                            name, separator, number = field.partition("=")
                            if name not in {"failures", "errors", "skipped"} or not separator or not number.isdecimal():
                                raise ValueError
                            counts["failed" if name == "failures" else name] += int(number)
                        if sum(counts.values()) > count:
                            raise ValueError
                        counts["passed"] = count - sum(counts.values())
                        report_ok = status[1] == "OK" and not counts["failed"] and not counts["errors"]
                    else:
                        data = _read_runner_report(report, owned_root, identity)
                        counts, report_ok = (
                            _json_report_counts(data, spec.runner) if spec.runner in {"jest", "vitest"} else _xml_report_counts(data)
                        )
                    if sum(counts.values()) == 0:
                        raise ValueError
                except (ToolError, ValueError, TypeError, RecursionError, UnicodeError, ET.ParseError, OverflowError):
                    response.update(ok=False, required_unknown=True, execution_uncertain=True)
                    break
                for key in totals:
                    totals[key] += counts[key]
                response["ok"] = bool(response["ok"] and result["ok"] and report_ok and not counts["failed"] and not counts["errors"])
    response.update(totals)
    response["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
    response["summary"] = (
        "Test outcome is unknown." if response["required_unknown"]
        else _count_summary({**dict.fromkeys(SUMMARY_KEYS, 0), **totals})
    )
    failures = totals["failed"] + totals["errors"]
    response["diagnostics"] = [
        {"severity": "error", "message": "Test failed.", "source": spec.runner} for _ in range(min(failures, diagnostic_cap))
    ]
    return response
