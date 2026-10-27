from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tools.testing import project_runners, verification


@pytest.fixture(autouse=True)
def _isolated_runtime_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import config, workflow_actions
    from scripts import verification_worker
    from tools.testing import registry as testing_registry

    settings = replace(config.SETTINGS, state_dir=tmp_path / "runtime-state")
    monkeypatch.setattr(config, "SETTINGS", settings)
    for module in (verification, workflow_actions, testing_registry, verification_worker):
        monkeypatch.setattr(module, "SETTINGS", settings)


def _node(root: Path, monkeypatch: pytest.MonkeyPatch, *, installed: bool = True) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(json.dumps({"name": "fixture", "devDependencies": {"jest": "29.7.0"}}), encoding="utf-8")
    (root / "src.js").write_text("export const n = 1;\n", encoding="utf-8")
    if installed:
        package = root / "node_modules" / "jest"
        package.mkdir(parents=True)
        (package / "package.json").write_text(json.dumps({"name": "jest", "version": "29.7.0", "bin": "fixture.py"}), encoding="utf-8")
        (package / "fixture.py").write_text("pass\n", encoding="utf-8")
    monkeypatch.setattr(project_runners.shutil, "which", lambda _name: sys.executable)
    original = project_runners._execution_runtime
    monkeypatch.setattr(project_runners, "_execution_runtime", lambda spec, selected_root: (
        Path(sys.executable).resolve() if spec.runner == "jest" else original(spec, selected_root)
    ))


def _passed(*args: object, **kwargs: object) -> dict[str, Any]:
    return {"ok": True, "exit_code": 0, "timed_out": False, "required_unknown": False,
            "passed": 1, "failed": 0, "errors": 0, "skipped": 0, "summary": "1 passed", "diagnostics": []}


def test_no_pytest_check_means_no_foreign_test_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _node(tmp_path, monkeypatch)
    monkeypatch.setattr(verification, "run_project_tests", lambda *a, **k: pytest.fail("unrequested foreign test dispatched"))
    result = verification.verify_changed_repository(tmp_path, changed_paths=["src.js"], checks=["syntax"], test_backend="auto")
    assert result["ok"] and not result["tests_executed"] and not result["claim_testing"]


def test_unknown_required_runner_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _node(tmp_path, monkeypatch, installed=False)
    monkeypatch.setattr(verification, "run_project_tests", lambda *a, **k: pytest.fail("unavailable test dispatched"), raising=False)
    result = verification.verify_changed_repository(tmp_path, changed_paths=["src.js"], checks=["pytest"], test_backend="auto")
    assert not result["ok"] and result["required_unknown"] and not result["tests_executed"]


def test_empty_persisted_runtime_pins_never_rebind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _node(tmp_path, monkeypatch)
    monkeypatch.setattr(verification, "run_project_tests", lambda *a, **k: pytest.fail("unapproved test dispatched"), raising=False)
    result = verification.verify_changed_repository(
        tmp_path, changed_paths=["src.js"], checks=["pytest"], test_backend="auto", runner_specs=(),
    )
    assert not result["ok"] and result["required_unknown"]


def test_full_package_reports_resolve_semantic_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _node(tmp_path, monkeypatch)
    monkeypatch.setattr(verification, "run_project_tests", _passed)
    result = verification.verify_changed_repository(tmp_path, changed_paths=["src.js"], checks=["pytest"], test_backend="auto")
    assert result["ok"] and not result["required_unknown"] and result["tests_executed"]
    assert result["affected"]["unknown_coverage"]
    assert "semantic_test_coverage_unavailable" in result["affected"]["missing_inputs"]
    assert [stage["name"] for stage in result["stages"]] == ["package_test:jest:."]


@pytest.mark.parametrize("missing", [
    "selection_truncated", "stale_snapshot", "source_fingerprint_unavailable", "python_coverage_incomplete",
])
def test_full_package_does_not_erase_unresolved_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    _node(tmp_path, monkeypatch)
    original = verification.select_affected_tests

    def selection(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original(*args, **kwargs)
        result["missing_inputs"].append(missing)
        return result

    def run(*args: object, **kwargs: object) -> dict[str, Any]:
        verification.mark_native_test_dispatch()
        return _passed()

    monkeypatch.setattr(verification, "select_affected_tests", selection)
    monkeypatch.setattr(verification, "run_project_tests", run)
    result = verification.verify_changed_repository(tmp_path, changed_paths=["src.js"], checks=["pytest"], test_backend="auto")
    assert not result["ok"] and result["required_unknown"] and not result["execution_uncertain"]
    assert result["tests_executed"] and missing in result["affected"]["missing_inputs"]


def test_stale_package_config_rejects_before_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _node(tmp_path, monkeypatch)
    config = tmp_path / "jest.config.js"
    config.write_text("module.exports = {};\n", encoding="utf-8")
    plan = verification.build_verification_plan(tmp_path, changed_paths=["src.js"], test_backend="auto")
    specs = verification.bind_verification_runners(tmp_path, plan)
    assert len(specs) == 1 and specs[0].runtime is not None
    config.write_text("throw 'secret-canary';\n", encoding="utf-8")
    monkeypatch.setattr(verification, "run_project_tests", lambda *a, **k: pytest.fail("stale package dispatched"))
    result = verification.verify_changed_repository(
        tmp_path, changed_paths=["src.js"], checks=["pytest"], test_backend="auto", runner_specs=specs,
    )
    assert not result["ok"] and result["required_unknown"] and not result["execution_uncertain"]
    assert "secret-canary" not in json.dumps(result)


@pytest.mark.parametrize("after_dispatch", [False, True])
def test_stage_exception_uncertainty_matches_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_dispatch: bool) -> None:
    _node(tmp_path, monkeypatch)

    def fail(*args: object, **kwargs: object) -> dict[str, Any]:
        if after_dispatch:
            verification.mark_native_test_dispatch()
        raise RuntimeError("secret-canary")

    monkeypatch.setattr(verification, "run_project_tests", fail)
    result = verification.verify_changed_repository(tmp_path, changed_paths=["src.js"], checks=["pytest"], test_backend="auto")
    assert result["required_unknown"] and result["execution_uncertain"] is after_dispatch
    assert result["stages"][0]["execution_uncertain"] is after_dispatch
    assert "secret-canary" not in json.dumps(result)


def test_stage_dispatch_signal_does_not_leak_from_previous_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _node(tmp_path, monkeypatch)
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(verification, "run_ruff_summary", _passed)

    def fail(*args: object, **kwargs: object) -> dict[str, Any]:
        raise RuntimeError("pre-dispatch failure")

    monkeypatch.setattr(verification, "run_project_tests", fail)
    result = verification.verify_changed_repository(
        tmp_path, changed_paths=["src.js"], checks=["ruff", "pytest"], test_backend="auto", fail_fast=False,
    )
    assert result["stages"][0]["status"] == "passed"
    assert result["stages"][1]["status"] == "unavailable"
    assert not result["execution_uncertain"]


def test_preflight_analyzer_failure_is_not_test_execution_uncertainty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    def fail(*args: object, **kwargs: object) -> dict[str, Any]:
        raise RuntimeError("analyzer unavailable")

    monkeypatch.setattr(verification, "run_ruff_summary", fail)
    result = verification.verify_changed_repository(
        tmp_path, changed_paths=["app.py"], checks=["ruff"], test_backend="auto",
    )
    assert not result["ok"] and result["required_unknown"] and not result["execution_uncertain"]
    assert result["stages"][0]["status"] == "unavailable"


def _python(root: Path) -> Path:
    (root / "tests").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname="fixture"\nversion="0.1.0"\ndependencies=["pytest"]\n', encoding="utf-8",
    )
    (root / "app.py").write_text("n = 1\n", encoding="utf-8")
    (root / "tests" / "test_app.py").write_text("def test_one(): assert True\n", encoding="utf-8")
    binary = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fixture interpreter identity")
    return binary


def test_auto_python_packages_use_their_approved_interpreters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = _python(tmp_path)
    nested = tmp_path / "packages" / "nested"
    second = _python(nested)
    calls: list[tuple[Path, Path]] = []

    def run(spec: project_runners.RunnerSpec, **kwargs: object) -> dict[str, Any]:
        assert spec.runtime is not None
        calls.append((spec.package_root, spec.runtime.path))
        return _passed()

    monkeypatch.setattr(verification, "run_project_tests", run)
    monkeypatch.setattr(verification, "run_pytest_summary", lambda *a, **k: pytest.fail("workspace interpreter bypassed package pins"))
    result = verification.verify_changed_repository(
        tmp_path, changed_paths=["app.py"], checks=["pytest"], test_backend="auto", affected_only=False,
    )
    if project_runners._toml is None:
        assert not calls and result["required_unknown"] and not result["tests_executed"]
        return
    assert set(calls) == {(tmp_path, first), (nested, second)}
    assert [stage["name"] for stage in result["stages"]] == ["pytest_full"]
    assert result["tests_executed"]


def test_auto_docs_only_makes_no_testing_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "README.md").write_text("# Docs\n", encoding="utf-8")
    monkeypatch.setattr(verification, "run_project_tests", lambda *a, **k: pytest.fail("docs triggered package tests"))
    result = verification.verify_changed_repository(tmp_path, changed_paths=["README.md"], test_backend="auto")
    assert result["ok"] and not result["tests_executed"] and not result["claim_testing"]


def test_old_request_keeps_python_backend(tmp_path: Path) -> None:
    from tools.testing.background import VerificationRequest

    request = VerificationRequest.model_validate({"repo": str(tmp_path)})
    assert request.options()["test_backend"] == "python"
    assert not request.runner_specs


def test_cancelled_foreign_runner_has_no_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _node(tmp_path, monkeypatch)
    calls = 0

    def cancel(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        verification.mark_native_test_dispatch()
        raise asyncio.CancelledError

    monkeypatch.setattr(verification, "run_project_tests", cancel)
    with pytest.raises(asyncio.CancelledError):
        verification.verify_changed_repository(tmp_path, changed_paths=["src.js"], checks=["pytest"], test_backend="auto")
    assert calls == 1


def test_shared_analyzer_capacity_is_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import config

    settings = replace(config.SETTINGS, state_dir=tmp_path / "state", max_running_analyzers=1)
    monkeypatch.setattr(config, "SETTINGS", settings)
    monkeypatch.setattr(verification, "SETTINGS", settings)
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    (first / "app.py").write_text("x = 1\n", encoding="utf-8")
    _node(second, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    second_waiting = threading.Event()
    lock = threading.Lock()
    active = peak = capacity_calls = 0
    original = verification.acquire_runtime_capacity

    def acquire(*args: Any, **kwargs: Any) -> Any:
        nonlocal capacity_calls
        assert args[0] == settings.workflow_db and kwargs["limit"] == 1
        with lock:
            capacity_calls += 1
            if capacity_calls == 2:
                second_waiting.set()
        return original(*args, **kwargs)

    def run(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        entered.set()
        try:
            assert release.wait(5)
            return _passed()
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(verification, "acquire_runtime_capacity", acquire)
    monkeypatch.setattr(verification, "run_ruff_summary", run)
    monkeypatch.setattr(verification, "run_project_tests", run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(verification.verify_changed_repository, first, changed_paths=["app.py"], checks=["ruff"])
        assert entered.wait(5)
        two = pool.submit(verification.verify_changed_repository, second, changed_paths=["src.js"], checks=["pytest"], test_backend="auto")
        try:
            assert second_waiting.wait(5)
            assert peak == 1
        finally:
            release.set()
        assert one.result(timeout=10)["ok"] and two.result(timeout=10)["ok"]
    assert capacity_calls == 2 and peak == 1


def test_same_plan_direct_worker_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from core import workflow_actions
    from core.jobs import JobStore
    from scripts import verification_worker
    from tools.testing import background

    root = tmp_path / "repo"
    _node(root, monkeypatch)
    monkeypatch.setattr(verification, "run_project_tests", _passed)
    direct = verification.verify_changed_repository(root, changed_paths=["src.js"], checks=["pytest"], test_backend="auto")
    commands: list[list[str]] = []
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")

    def submit(command: list[str], *args: object, **kwargs: object) -> dict[str, str]:
        commands.append(command)
        return {"job_id": "same-plan"}

    monkeypatch.setattr(store, "submit", submit)
    background.submit_verification(store, background.VerificationRequest(
        repo=str(root), changed_paths=["src.js"], checks=["pytest"], test_backend="auto",
    ), "same-plan")
    assert verification_worker.run_request(commands[0][-1]) == 0
    worker = json.loads(capsys.readouterr().out.splitlines()[-1])
    workflow = workflow_actions._verify_changes({
        "cwd": str(root), "changed_paths": ["src.js"], "checks": ["pytest"], "test_backend": "auto",
    }, 10)
    assert all(result["ok"] and not result["required_unknown"] for result in (direct, worker, workflow))
    assert all(result["tests_executed"] and result["claim_testing"] for result in (direct, worker, workflow))
    assert [stage["name"] for stage in direct["stages"]] == [stage["name"] for stage in worker["stages"]]
    assert [stage["name"] for stage in direct["stages"]] == [stage["name"] for stage in workflow["stages"]]


def test_docs_only_worker_makes_no_testing_claim(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts import verification_worker
    from tools.testing.background import VerificationRequest, source_fingerprint

    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("docs only", encoding="utf-8")
    request = VerificationRequest(
        repo=str(root), changed_paths=["README.md"], test_backend="auto",
        source_fingerprint=source_fingerprint(root, changed_paths=["README.md"]),
    )
    assert verification_worker.run_request(request.model_dump_json()) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["ok"] and not result["tests_executed"] and not result["claim_testing"]


def test_unknown_required_runner_fails_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import workflow_actions
    from core.errors import ToolError

    _node(tmp_path, monkeypatch, installed=False)
    with pytest.raises(ToolError) as exc:
        workflow_actions._verify_changes({
            "cwd": str(tmp_path), "changed_paths": ["src.js"], "checks": ["pytest"], "test_backend": "auto",
        }, 10)
    assert exc.value.code == "workflow_verification_failed"


def test_failed_stage_notification_releases_capacity_before_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.workflow_store import WorkflowStore

    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(verification, "run_ruff_summary", lambda *args, **kwargs: pytest.fail("analyzer dispatched"))

    def notify(event: str, data: dict[str, Any]) -> None:
        if event == "stage_started":
            raise ValueError("worker stream unavailable")

    result = verification.verify_changed_repository(root, changed_paths=["app.py"], checks=["ruff"], stage_callback=notify)
    assert not result["ok"] and result["required_unknown"] and not result["execution_uncertain"]
    health = WorkflowStore(verification.SETTINGS.workflow_db).health_summary()
    assert health["active_analyzer_capacity_weight"] == 0
    assert health["active_resource_request_count"] == 0


def test_source_changed_during_successful_execution_stays_coverage_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _node(tmp_path, monkeypatch)

    def run(*args: object, **kwargs: object) -> dict[str, Any]:
        verification.mark_native_test_dispatch()
        (tmp_path / "src.js").write_text("export const n = 2;\n", encoding="utf-8")
        return _passed()

    monkeypatch.setattr(verification, "run_project_tests", run)
    result = verification.verify_changed_repository(tmp_path, changed_paths=["src.js"], checks=["pytest"], test_backend="auto")
    assert not result["ok"] and result["required_unknown"] and not result["execution_uncertain"]


def test_zero_tests_does_not_make_a_testing_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _node(tmp_path, monkeypatch)

    def run(*args: object, **kwargs: object) -> dict[str, Any]:
        return {**_passed(), "ok": False, "passed": 0, "required_unknown": True, "execution_uncertain": True}

    monkeypatch.setattr(verification, "run_project_tests", run)
    result = verification.verify_changed_repository(tmp_path, changed_paths=["src.js"], checks=["pytest"], test_backend="auto")
    assert not result["ok"] and result["required_unknown"] and not result["tests_executed"] and not result["claim_testing"]


@pytest.mark.parametrize("options", [
    {"stage_timeout_sec": 601}, {"total_timeout_sec": 1801}, {"total_timeout_sec": float("nan")},
    {"diagnostic_cap": 201}, {"maxfail": 0},
])
def test_auto_limits_are_validated_before_mutation_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: dict[str, Any],
) -> None:
    monkeypatch.setattr(verification, "run_native_operation", lambda *a, **kw: pytest.fail("invalid request admitted"))
    with pytest.raises(ValueError):
        verification.verify_changed_repository(tmp_path, test_backend="auto", **options)


def test_admission_and_planning_consume_total_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _node(tmp_path, monkeypatch)
    original = verification.build_verification_plan
    monotonic = time.monotonic
    offset = [0.0]
    monkeypatch.setattr(verification.time, "monotonic", lambda: monotonic() + offset[0])

    def plan(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original(*args, **kwargs)
        offset[0] = 20.0
        return result

    monkeypatch.setattr(verification, "build_verification_plan", plan)
    monkeypatch.setattr(verification, "run_project_tests", lambda *a, **kw: pytest.fail("expired budget dispatched"))
    result = verification.verify_changed_repository(
        tmp_path, changed_paths=["src.js"], checks=["pytest"], test_backend="auto", total_timeout_sec=10,
    )
    assert not result["ok"] and result["required_unknown"] and not result["execution_uncertain"]


def test_auto_only_foreign_project_skips_python_analyzers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _node(tmp_path, monkeypatch)
    monkeypatch.setattr(verification, "run_project_tests", _passed)
    for name in ("run_ruff_summary", "run_mypy_summary", "run_pytest_summary"):
        monkeypatch.setattr(verification, name, lambda *a, **k: pytest.fail("Python analyzer dispatched for foreign-only source"))
    result = verification.verify_changed_repository(tmp_path, changed_paths=["src.js"], test_backend="auto")
    assert result["ok"] and result["tests_executed"]
    assert [stage["status"] for stage in result["stages"]] == ["skipped", "skipped", "skipped", "passed"]


def test_capacity_admission_consumes_stage_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _node(tmp_path, monkeypatch)
    acquire = verification.acquire_runtime_capacity
    monotonic = time.monotonic
    offset = [0.0]
    monkeypatch.setattr(verification.time, "monotonic", lambda: monotonic() + offset[0])

    def admitted(*args: Any, **kwargs: Any) -> Any:
        assert kwargs["deadline"] < monotonic() + 15
        permit = acquire(*args, **kwargs)
        offset[0] = 20
        return permit

    monkeypatch.setattr(verification, "acquire_runtime_capacity", admitted)
    monkeypatch.setattr(verification, "run_project_tests", lambda *a, **k: pytest.fail("stage budget expired during admission"))
    result = verification.verify_changed_repository(
        tmp_path, changed_paths=["src.js"], checks=["pytest"], test_backend="auto", stage_timeout_sec=10, total_timeout_sec=100,
    )
    assert result["required_unknown"] and not result["execution_uncertain"]
    assert result["stages"][0]["status"] == "timed_out"


def _unittest(root: Path) -> None:
    (root / "tests").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0.1.0"\n', encoding="utf-8")
    (root / "app.py").write_text("n = 1\n", encoding="utf-8")
    (root / "tests" / "test_app.py").write_text(
        "import unittest\nclass Sample(unittest.TestCase):\n def test_one(self): self.assertTrue(True)\n", encoding="utf-8",
    )


def test_explicit_bound_unittest_resolves_only_its_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _unittest(tmp_path)
    no_choice = verification.verify_changed_repository(tmp_path, changed_paths=["app.py"], checks=["pytest"], test_backend="auto")
    assert not no_choice["ok"] and no_choice["required_unknown"] and not no_choice["tests_executed"]
    plan = verification.build_verification_plan(tmp_path, changed_paths=["app.py"], test_backend="auto")
    spec = plan["runner_catalog"][0]
    if project_runners._toml is None:
        assert "toml_parser_unavailable" in spec.missing_inputs
        return
    approved = project_runners.bind_runner_runtime(spec, workspace_root=tmp_path, runner="unittest")
    explicit = verification.verify_changed_repository(
        tmp_path, changed_paths=["app.py"], checks=["pytest"], test_backend="auto", runner_specs=(approved,),
    )
    assert explicit["ok"] and not explicit["required_unknown"] and explicit["tests_executed"]
    assert explicit["affected"]["missing_inputs"] == no_choice["affected"]["missing_inputs"]
    assert "runner_required" in explicit["affected"]["missing_inputs"]
    _unittest(tmp_path / "nested")
    partial = verification.verify_changed_repository(
        tmp_path, changed_paths=["app.py"], checks=["pytest"], test_backend="auto", runner_specs=(approved,),
    )
    assert not partial["ok"] and partial["required_unknown"] and partial["tests_executed"]
    assert not partial["execution_uncertain"]


@pytest.mark.parametrize("scope", ["read", "forged"])
def test_direct_verifier_denies_insufficient_inherited_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str,
) -> None:
    from core.errors import ToolError
    from core.resource_claims import coordination_claim, managed_resource_claims, path_claim, resource_capability
    from core.workflow_store import WorkflowStore

    _node(tmp_path, monkeypatch)
    monkeypatch.setattr(verification, "run_project_tests", lambda *a, **k: pytest.fail("denied verifier dispatched"))
    if scope == "forged":
        with resource_capability("forged"), pytest.raises(ToolError):
            verification.verify_changed_repository(tmp_path, changed_paths=["src.js"], checks=["pytest"], test_backend="auto")
    else:
        store = WorkflowStore(tmp_path.parent / f"{tmp_path.name}-claims.sqlite3", recover=False)
        with managed_resource_claims((coordination_claim(), path_claim(tmp_path, "read", scope="subtree")),
                                     category="test", store=store), pytest.raises(ToolError) as exc:
            verification.verify_changed_repository(tmp_path, changed_paths=["src.js"], checks=["pytest"], test_backend="auto")
        assert exc.value.code == "resource_capability_scope_violation"


def test_runtime_pin_from_other_workspace_is_not_adopted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    _node(root, monkeypatch)
    parent_plan = verification.build_verification_plan(tmp_path, changed_paths=["repo/src.js"], test_backend="auto")
    pins = verification.bind_verification_runners(tmp_path, parent_plan)
    assert len(pins) == 1 and pins[0].workspace_root == tmp_path
    calls: list[object] = []

    def run(*args: object, **kwargs: object) -> dict[str, Any]:
        calls.append(args)
        return _passed()

    monkeypatch.setattr(verification, "run_project_tests", run)
    result = verification.verify_changed_repository(
        root, changed_paths=["src.js"], checks=["pytest"], test_backend="auto", runner_specs=pins,
    )
    assert not calls and result["required_unknown"] and not result["ok"]
