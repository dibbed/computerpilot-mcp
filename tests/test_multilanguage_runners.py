from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import psutil
import pytest

from core.errors import ToolError
from tools.project.project_types import detect_project_types
from tools.testing import project_runners, runners


def _node(root: Path, monkeypatch: pytest.MonkeyPatch, *, runner: str = "jest", version: str = "29.7.0") -> project_runners.RunnerSpec:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(json.dumps({
        "name": "fixture", "devDependencies": {runner: version},
        "scripts": {"test": "MUST_NOT_EXECUTE"},
    }), encoding="utf-8")
    installed = root / "node_modules" / runner
    installed.mkdir(parents=True)
    (installed / "package.json").write_text(json.dumps({"name": runner, "version": version, "bin": "runner.py"}), encoding="utf-8")
    (installed / "runner.py").write_text(
        "import json,sys,pathlib\n"
        "a=sys.argv; p=a[a.index('--outputFile')+1]\n"
        "pathlib.Path(p).write_text(json.dumps({'numTotalTests':1,'numPassedTests':1,'numFailedTests':0,'numPendingTests':0,'success':True}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(project_runners.shutil, "which", lambda _name: sys.executable)
    monkeypatch.setattr(project_runners, "_execution_runtime", lambda _spec, _root: Path(sys.executable).resolve(), raising=False)
    inventory = detect_project_types(root)
    spec = project_runners.discover_test_runners(root, inventory).runners[0]
    return project_runners.bind_runner_runtime(spec, workspace_root=root)


def _run(spec: project_runners.RunnerSpec) -> dict[str, Any]:
    return runners.run_project_tests(spec, targets=[], deadline=time.monotonic() + 10, diagnostic_cap=30)


def _rewrite(spec: project_runners.RunnerSpec, text: str) -> project_runners.RunnerSpec:
    assert spec.entrypoint is not None and spec.workspace_root is not None
    spec.entrypoint.write_text(text, encoding="utf-8")
    return project_runners.bind_runner_runtime(replace(spec, runtime=None, entrypoint_sha256=None), workspace_root=spec.workspace_root)


def _result(*, exit_code: int = 0, text: str = "") -> dict[str, Any]:
    return {"ok": exit_code == 0, "exit_code": exit_code, "timed_out": False, "capture_complete": True,
            "stdout": {"text": text, "truncated": False}, "stderr": {"text": text, "truncated": False}}


def _report_fixture(monkeypatch: pytest.MonkeyPatch, writer: Callable[[Path], object]) -> list[list[str]]:
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> dict[str, Any]:
        commands.append(command)
        flag = "--outputFile" if "--outputFile" in command else "--log-junit"
        writer(Path(command[command.index(flag) + 1]))
        return _result()

    monkeypatch.setattr(runners, "run_bounded", run)
    return commands


def test_argv_spaces_are_single_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "space فارسی"
    spec = _node(root, monkeypatch)
    target = root / "-a test فارسی.js"
    target.write_text("", encoding="utf-8")
    report = tmp_path / "private report.json"
    argv = runners.build_runner_argv(spec, [target], report)
    assert argv[0] == str(Path(sys.executable).resolve())
    assert argv[-1] == str(target.resolve())
    assert argv[argv.index("--outputFile") + 1] == str(report)
    assert "--runInBand" in argv and "--runTestsByPath" in argv


def test_script_command_not_executed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _node(tmp_path, monkeypatch)
    result = _run(spec)
    assert result["ok"] and result["passed"] == 1
    assert "MUST_NOT_EXECUTE" not in json.dumps(result)


def test_missing_report_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _node(tmp_path, monkeypatch)
    assert spec.entrypoint is not None
    spec.entrypoint.write_text("pass\n", encoding="utf-8")
    spec = project_runners.bind_runner_runtime(replace(spec, runtime=None, entrypoint_sha256=None), workspace_root=tmp_path)
    result = _run(spec)
    assert not result["ok"] and result["required_unknown"]


def test_unbound_spec_denied_before_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _node(tmp_path, monkeypatch)
    monkeypatch.setattr(runners, "run_bounded", lambda *a, **k: pytest.fail("dispatched forged spec"))
    with pytest.raises(ToolError):
        _run(replace(spec, runtime=None))


@pytest.mark.parametrize("mutation", ["manifest", "installed_version", "config", "entrypoint", "runtime", "workspace", "runner"])
def test_stale_or_forged_identity_never_dispatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    spec = _node(tmp_path, monkeypatch)
    if mutation == "manifest":
        (tmp_path / "package.json").write_text('{"name":"changed"}', encoding="utf-8")
    elif mutation == "installed_version":
        path = tmp_path / "node_modules" / "jest" / "package.json"
        path.write_text(path.read_text().replace("29.7.0", "29.7.1"), encoding="utf-8")
    elif mutation == "config":
        (tmp_path / "jest.config.js").write_text("throw 'secret-canary';", encoding="utf-8")
    elif mutation == "entrypoint":
        assert spec.entrypoint is not None
        spec.entrypoint.write_text("raise Exception('secret-canary')", encoding="utf-8")
    elif mutation == "runtime":
        assert spec.runtime is not None
        spec = replace(spec, runtime=replace(spec.runtime, sha256="0" * 64))
    elif mutation == "workspace":
        spec = replace(spec, workspace_id="0" * 64)
    else:
        spec = replace(spec, runner="vitest")
    monkeypatch.setattr(runners, "run_bounded", lambda *a, **k: pytest.fail("dispatched stale/forged spec"))
    with pytest.raises(ToolError):
        _run(spec)


@pytest.mark.parametrize("runner,version,flag", [("jest", "29.7.0", "--runInBand"), ("vitest", "3.2.4", "--no-file-parallelism")])
def test_pinned_node_version_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: str, version: str, flag: str,
) -> None:
    spec = _node(tmp_path, monkeypatch, runner=runner, version=version)
    argv = runners.build_runner_argv(spec, [], tmp_path / "report.json")
    assert flag in argv
    assert all(item not in argv for item in ("npm", "npx", "--parallel"))
    if runner == "vitest":
        assert argv[2] == "run" and "--maxWorkers=1" in argv


@pytest.mark.parametrize("version", ["28.0.0", "30.0.0", "29.7.0-beta.1", "anything"])
def test_unsupported_version_has_zero_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    spec = _node(tmp_path, monkeypatch, version=version)
    monkeypatch.setattr(runners, "run_bounded", lambda *a, **k: pytest.fail("dispatched unsupported runner"))
    with pytest.raises(ToolError, match="unsupported") as exc:
        _run(spec)
    assert exc.value.code == "unsupported_runner_version"


def test_no_tests_is_not_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _node(tmp_path, monkeypatch)
    _report_fixture(monkeypatch, lambda path: path.write_text(json.dumps({
        "numTotalTests": 0, "numPassedTests": 0, "numFailedTests": 0, "numPendingTests": 0, "success": True,
    }), encoding="utf-8"))
    result = _run(spec)
    assert result["exit_code"] == 0 and not result["ok"] and result["required_unknown"]


@pytest.mark.parametrize("runner,status,unknown", [
    ("vitest", "pending", True), ("vitest", "skipped", False), ("jest", "pending", False),
])
def test_node_report_terminal_statuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: str, status: str, unknown: bool,
) -> None:
    spec = _node(tmp_path, monkeypatch, runner=runner, version="3.2.4" if runner == "vitest" else "29.7.0")
    _report_fixture(monkeypatch, lambda path: path.write_text(json.dumps({
        "numTotalTests": 1, "numPassedTests": 0, "numFailedTests": 0, "numPendingTests": 1, "success": True,
        "testResults": [{"status": "passed", "assertionResults": [{"status": status}]}],
    }), encoding="utf-8"))
    result = _run(spec)
    assert result["required_unknown"] is unknown and result["ok"] is not unknown


@pytest.mark.parametrize("results", [
    "invalid", [{"assertionResults": "invalid"}],
    [{"assertionResults": [{"status": "running"}]}],
    [{"assertionResults": [{"status": "failed"}]}],
])
def test_node_report_invalid_assertions_are_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, results: object,
) -> None:
    spec = _node(tmp_path, monkeypatch)
    _report_fixture(monkeypatch, lambda path: path.write_text(json.dumps({
        "numTotalTests": 1, "numPassedTests": 1, "numFailedTests": 0, "numPendingTests": 0, "success": True,
        "testResults": results,
    }), encoding="utf-8"))
    result = _run(spec)
    assert not result["ok"] and result["required_unknown"]


def test_report_symlink_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _node(tmp_path, monkeypatch)
    outside = tmp_path / "outside.json"
    outside.write_text('{"success":true,"numTotalTests":1,"numPassedTests":1,"numFailedTests":0,"numPendingTests":0}')
    checked: list[Path] = []
    if os.name == "nt":
        original = Path.lstat

        def linked_stat(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
            info = original(path, *args, **kwargs)
            if path.name.startswith("report-") and path.suffix == ".json":
                checked.append(path)
                values = list(info)
                values[0] = stat.S_IFLNK | 0o600
                return os.stat_result(values)
            return info

        monkeypatch.setattr(Path, "lstat", linked_stat)
        _report_fixture(monkeypatch, lambda path: path.write_bytes(outside.read_bytes()))
    else:
        def link(path: Path) -> None:
            path.symlink_to(outside)
            assert path.is_symlink()
            checked.append(path)

        _report_fixture(monkeypatch, link)
    result = _run(spec)
    assert not result["ok"] and result["required_unknown"]
    assert outside.exists() and checked


def test_report_hardlink_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Hard links require the report and fixture target to share a filesystem.
    monkeypatch.setattr(runners.tempfile, "tempdir", str(tmp_path))
    spec = _node(tmp_path, monkeypatch)
    outside = tmp_path / "outside.json"
    outside.write_bytes(_GOOD_JSON + b'}')
    created: list[Path] = []

    def link(path: Path) -> None:
        path.hardlink_to(outside)
        assert path.stat().st_nlink == 2
        created.append(path)

    _report_fixture(monkeypatch, link)
    result = _run(spec)
    assert not result["ok"] and result["required_unknown"] and created
    assert outside.stat().st_nlink == 1


_GOOD_JSON = b'{"success":true,"numTotalTests":1,"numPassedTests":1,"numFailedTests":0,"numPendingTests":0'


@pytest.mark.parametrize("payload", [
    _GOOD_JSON + b',"success":true}',
    _GOOD_JSON + b',"extra":NaN}',
    _GOOD_JSON + b',"extra":1e9999}', b'[]', b'not json',
    _GOOD_JSON + b',"extra":' + json.dumps([0] * 10001).encode() + b'}',
    _GOOD_JSON + b',"extra":' + ("[" * 17 + "0" + "]" * 17).encode() + b'}',
    b"x" * (4 * 1024 * 1024 + 1),
], ids=["duplicate", "nan", "overflow", "array", "invalid", "list_limit", "depth_limit", "byte_limit"])
def test_invalid_json_report_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
    spec = _node(tmp_path, monkeypatch)
    _report_fixture(monkeypatch, lambda path: path.write_bytes(payload))
    result = _run(spec)
    assert not result["ok"] and result["required_unknown"]


def test_report_diagnostics_are_capped_and_do_not_echo_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _node(tmp_path, monkeypatch)
    _report_fixture(monkeypatch, lambda path: path.write_text(json.dumps({
        "numTotalTests": 500, "numPassedTests": 0, "numFailedTests": 500, "numPendingTests": 0,
        "success": False, "testResults": [{"message": "secret-canary", "name": "secret-canary",
            "assertionResults": [{"status": "failed", "title": "secret-canary"}] * 500}],
    }), encoding="utf-8"))
    result = runners.run_project_tests(spec, targets=[], deadline=time.monotonic() + 5, diagnostic_cap=200)
    assert not result["ok"] and not result["required_unknown"]
    assert len(result["diagnostics"]) == 200 and "secret-canary" not in json.dumps(result)


def test_exception_text_is_not_exposed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _node(tmp_path, monkeypatch)

    def fail(*args: object, **kwargs: object) -> dict[str, Any]:
        raise RuntimeError("secret-canary")

    monkeypatch.setattr(runners, "run_bounded", fail)
    result = _run(spec)
    assert result["required_unknown"] and "secret-canary" not in json.dumps(result)


def test_runtime_path_ignores_workspace_and_relative_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, host = tmp_path / "workspace", tmp_path / "host"
    root.mkdir()
    host.mkdir()
    suffix = ".exe" if os.name == "nt" else ""
    for directory in (root, host):
        path = directory / ("node" + suffix)
        path.write_text("fixture", encoding="utf-8")
        path.chmod(0o700)
    monkeypatch.setenv("PATH", os.pathsep.join([str(root), ".", "relative", str(host)]))
    spec = project_runners.RunnerSpec("jest", root, "", (), None, "29.7.0", (), True, ())
    assert project_runners._execution_runtime(spec, root) == (host / ("node" + suffix)).resolve()
    monkeypatch.setenv("PATH", os.pathsep.join([str(root), "."]))
    with pytest.raises(ToolError) as exc:
        project_runners._execution_runtime(spec, root)
    assert exc.value.code == "runtime_unavailable"


def test_target_escape_and_argv_limit_deny(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "package"
    spec = _node(root, monkeypatch)
    outside = tmp_path / "other.js"
    outside.write_text("", encoding="utf-8")
    with pytest.raises(ToolError):
        runners.build_runner_argv(spec, [outside], tmp_path / "report")
    with pytest.raises(ToolError) as exc:
        runners.build_runner_argv(spec, [root] * 1000, tmp_path / "report")
    assert exc.value.code == "test_runner_argv_limit"


def test_expired_deadline_never_dispatches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _node(tmp_path, monkeypatch)
    monkeypatch.setattr(runners, "run_bounded", lambda *a, **k: pytest.fail("dispatched expired stage"))
    result = runners.run_project_tests(spec, targets=[], deadline=time.monotonic() - 1, diagnostic_cap=30)
    assert result["timed_out"] and result["required_unknown"]


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).is_running() and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def test_timeout_reaps_grandchild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _node(tmp_path, monkeypatch)
    pids = tmp_path / "pids.json"
    child_code = (
        "import subprocess,sys,time,json,os;from pathlib import Path;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"Path({str(pids)!r}).write_text(json.dumps([os.getpid(),p.pid]));time.sleep(60)"
    )
    spec = _rewrite(spec, f"import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c',{child_code!r}])\ntime.sleep(60)\n")
    try:
        result = runners.run_project_tests(spec, targets=[], deadline=time.monotonic() + 2, diagnostic_cap=30)
        assert result["timed_out"] and result["required_unknown"] and not result["ok"]
        assert pids.exists(), "fixture descendants did not start before timeout"
        processes = json.loads(pids.read_text())
        until = time.monotonic() + 5
        while any(_alive(pid) for pid in processes) and time.monotonic() < until:
            time.sleep(0.02)
        assert all(not _alive(pid) for pid in processes)
    finally:
        if pids.exists():
            for pid in json.loads(pids.read_text()):
                if _alive(pid):
                    psutil.Process(pid).kill()


def test_cancel_has_no_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _node(tmp_path, monkeypatch)
    calls = 0

    def cancel(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    monkeypatch.setattr(runners, "run_bounded", cancel)
    with pytest.raises(asyncio.CancelledError):
        _run(spec)
    assert calls == 1


def test_environment_secrets_and_loaders_are_not_forwarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_SECRET", "secret-canary")
    monkeypatch.setenv("NODE_OPTIONS", "--require secret-canary")
    spec = _node(tmp_path, monkeypatch)
    spec = _rewrite(spec,
        "import os,json,pathlib,sys\n"
        "assert not os.environ.get('RUNNER_SECRET') and not os.environ.get('NODE_OPTIONS')\n"
        "p=pathlib.Path(sys.argv[sys.argv.index('--outputFile')+1])\n"
        "p.write_text(json.dumps({'success':True,'numTotalTests':1,'numPassedTests':1,'numFailedTests':0,'numPendingTests':0}))\n"
    )
    assert _run(spec)["ok"]


def _php(root: Path, monkeypatch: pytest.MonkeyPatch, runner: str = "phpunit") -> project_runners.RunnerSpec:
    name = "phpunit/phpunit" if runner == "phpunit" else "pestphp/pest"
    version = "11.5.0" if runner == "phpunit" else "3.8.0"
    (root / "composer.json").write_text(json.dumps({"require-dev": {name: "*"}}), encoding="utf-8")
    metadata = root / "vendor" / "composer"
    metadata.mkdir(parents=True)
    (metadata / "installed.json").write_text(json.dumps({"packages": [{"name": name, "version": version}]}), encoding="utf-8")
    binary = root / "vendor" / "bin" / runner
    binary.parent.mkdir()
    binary.write_text("<?php exit(0);\n", encoding="utf-8")
    monkeypatch.setattr(project_runners.shutil, "which", lambda _name: sys.executable)
    monkeypatch.setattr(project_runners, "_execution_runtime", lambda _spec, _root: Path(sys.executable).resolve())
    spec = project_runners.discover_test_runners(root, detect_project_types(root)).runners[0]
    return project_runners.bind_runner_runtime(spec, workspace_root=root)


@pytest.mark.parametrize("runner", ["phpunit", "pest"])
def test_php_targets_are_sequential_per_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: str) -> None:
    spec = _php(tmp_path, monkeypatch, runner)
    targets = [tmp_path / "first test.php", tmp_path / "-second فارسی.php"]
    for target in targets:
        target.touch()
    commands = _report_fixture(monkeypatch, lambda path: path.write_text(
        '<testsuites><testsuite tests="1"><testcase name="secret-canary"/></testsuite></testsuites>', encoding="utf-8",
    ))
    result = runners.run_project_tests(spec, targets=targets, deadline=time.monotonic() + 10, diagnostic_cap=30)
    assert result["ok"] and result["passed"] == 2 and len(commands) == 2
    assert [command[-1] for command in commands] == [str(target) for target in targets]
    assert all("--parallel" not in command and "--filter" not in command for command in commands)
    assert "secret-canary" not in json.dumps(result)
    assert all(not Path(command[command.index("--log-junit") + 1]).exists() for command in commands)


@pytest.mark.parametrize("payload", [
    '<!DOCTYPE testsuite [<!ENTITY x "secret-canary">]><testsuite><testcase name="&x;"/></testsuite>',
    '<!ENTITY x "secret-canary"><testsuite><testcase/></testsuite>',
    '<testsuite tests="0"/>', '<testsuite>',
    ('<testsuites>' * 17) + '<testcase/>' + ('</testsuites>' * 17),
    '<testsuite tests="2"><testcase/></testsuite>',
    '<testsuites><testsuite tests="2" failures="1" errors="0" skipped="0"><testcase/></testsuite></testsuites>',
], ids=["doctype", "entity", "empty", "broken", "depth", "inconsistent", "nested-inconsistent"])
def test_php_invalid_report_stays_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: str) -> None:
    spec = _php(tmp_path, monkeypatch)
    _report_fixture(monkeypatch, lambda path: path.write_text(payload, encoding="utf-8"))
    result = _run(spec)
    assert not result["ok"] and result["required_unknown"] and "secret-canary" not in json.dumps(result)


def test_nested_junit_counters_do_not_double_count() -> None:
    report = (
        '<testsuites tests="2"><testsuite tests="2"><testsuite tests="1"><testcase/></testsuite>'
        '<testsuite tests="1"><testcase/></testsuite></testsuite></testsuites>'
    )
    counts, ok = runners._xml_report_counts(report.encode())
    assert ok and counts == {"passed": 2, "failed": 0, "errors": 0, "skipped": 0}


def test_php_missing_later_report_does_not_claim_partial_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _php(tmp_path, monkeypatch)
    targets = [tmp_path / "first.php", tmp_path / "second.php"]
    for target in targets:
        target.touch()
    count = 0

    def write(path: Path) -> None:
        nonlocal count
        count += 1
        if count == 1:
            path.write_text('<testsuite><testcase/></testsuite>', encoding="utf-8")

    _report_fixture(monkeypatch, write)
    result = runners.run_project_tests(spec, targets=targets, deadline=time.monotonic() + 10, diagnostic_cap=30)
    assert not result["ok"] and result["required_unknown"] and result["passed"] == 1


def test_explicit_unittest_uses_approved_directory(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0.1.0"\n', encoding="utf-8")
    directory = tmp_path / "tests"
    directory.mkdir()
    (directory / "test_sample.py").write_text(
        "import unittest\nclass Sample(unittest.TestCase):\n def test_one(self): self.assertTrue(True)\n", encoding="utf-8",
    )
    spec = project_runners.discover_test_runners(tmp_path, detect_project_types(tmp_path)).runners[0]
    assert spec.runner == "unknown"
    if project_runners._toml is None:
        with pytest.raises(ToolError):
            project_runners.bind_runner_runtime(spec, workspace_root=tmp_path, runner="unittest")
        return
    bound = project_runners.bind_runner_runtime(spec, workspace_root=tmp_path, runner="unittest")
    argv = runners.build_runner_argv(bound, [], tmp_path / "unused.xml")
    assert argv[-3:] == ["discover", "-s", str(directory)]
    result = _run(bound)
    assert result["ok"] and result["passed"] == 1 and not result["required_unknown"]


def test_subprocess_deadline_and_capture_are_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _node(tmp_path, monkeypatch)
    original = runners.run_bounded
    observed: list[float] = []

    def run(command: list[str], **kwargs: Any) -> dict[str, Any]:
        observed.append(kwargs["timeout_sec"])
        assert kwargs["stdout_limit"] == kwargs["stderr_limit"] == 65536
        return original(command, **kwargs)

    monkeypatch.setattr(runners, "run_bounded", run)
    result = runners.run_project_tests(spec, targets=[], deadline=time.monotonic() + 1800, diagnostic_cap=30)
    assert result["ok"] and len(observed) == 1 and 0 < observed[0] <= 600


def test_binary_changed_after_binding_is_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _node(tmp_path, monkeypatch)
    runtime = tmp_path / "fake-runtime"
    runtime.write_bytes(b"approved")
    monkeypatch.setattr(project_runners, "_execution_runtime", lambda _spec, _root: runtime)
    spec = project_runners.bind_runner_runtime(replace(spec, runtime=None), workspace_root=tmp_path)
    runtime.write_bytes(b"replaced")
    monkeypatch.setattr(runners, "run_bounded", lambda *a, **k: pytest.fail("dispatched replaced binary"))
    with pytest.raises(ToolError):
        _run(spec)


def test_python_pytest_compatibility(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_sample.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    spec = project_runners.discover_test_runners(tmp_path, detect_project_types(tmp_path)).runners[0]
    bound = project_runners.bind_runner_runtime(spec, workspace_root=tmp_path)
    assert bound.runtime is not None and bound.runtime.path == Path(runners.python_for(tmp_path))
    result = _run(bound)
    assert result["ok"] and result["passed"] == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX virtualenv interpreter symlinks; Windows uses copied python.exe")
def test_python_venv_invocation_and_binary_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    spec = project_runners.discover_test_runners(tmp_path, detect_project_types(tmp_path)).runners[0]
    directory = tmp_path / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    directory.mkdir(parents=True)
    invocation = directory / ("python.exe" if os.name == "nt" else "python")
    invocation.symlink_to(Path(sys.executable).resolve())
    bound = project_runners.bind_runner_runtime(spec, workspace_root=tmp_path)
    assert bound.runtime is not None and bound.runtime.path == invocation
    assert bound.runtime.binary_path == Path(sys.executable).resolve()
    assert runners.build_runner_argv(bound, [], tmp_path / "report.xml")[0] == str(invocation)
    invocation.unlink()
    other = tmp_path / "other-binary"
    other.write_bytes(b"replaced binary")
    invocation.symlink_to(other)
    monkeypatch.setattr(runners, "run_bounded", lambda *a, **k: pytest.fail("dispatched retargeted venv"))
    with pytest.raises(ToolError):
        _run(bound)


@pytest.mark.parametrize("scope", ["read", "unrelated", "forged", "expired"])
def test_inherited_capability_denies_before_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str) -> None:
    from core.resource_claims import coordination_claim, managed_resource_claims, path_claim, resource_capability
    from core.workflow_store import WorkflowStore

    spec = _node(tmp_path / "package", monkeypatch)
    monkeypatch.setattr(runners, "run_bounded", lambda *a, **k: pytest.fail("dispatched denied capability"))
    if scope == "expired":
        store = WorkflowStore(tmp_path / "claims.sqlite3", recover=False)
        with managed_resource_claims((coordination_claim(), path_claim(spec.package_root, "write", scope="subtree")),
                                     category="test", store=store) as token:
            pass
        with resource_capability(token), pytest.raises(ToolError):
            _run(spec)
    elif scope == "forged":
        with resource_capability("forged"), pytest.raises(ToolError):
            _run(spec)
    else:
        store = WorkflowStore(tmp_path / "claims.sqlite3", recover=False)
        path = spec.package_root if scope == "read" else tmp_path / "unrelated"
        claim = path_claim(path, "read" if scope == "read" else "write", scope="subtree")
        with managed_resource_claims((coordination_claim(), claim), category="test", store=store):
            with pytest.raises(ToolError) as exc:
                _run(spec)
            assert exc.value.code == "resource_capability_scope_violation"
