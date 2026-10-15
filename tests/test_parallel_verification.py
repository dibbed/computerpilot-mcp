from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from tools.testing import verification


def test_checks_overlap_with_bounded_capacity_and_ordered_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(verification, "select_affected_tests", lambda *a, **kw: {
        "decision": "focused", "tests": [{"path": "test_app.py"}],
    })
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active = peak = calls = 0

    def run(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal active, peak, calls
        with lock:
            active += 1
            peak = max(peak, active)
            calls += 1
            first_pair = calls <= 2
        try:
            if first_pair:
                barrier.wait(timeout=5)
            return {"ok": True, "exit_code": 0, "summary": "passed"}
        finally:
            with lock:
                active -= 1

    for name in ("run_ruff_summary", "run_mypy_summary", "run_pytest_summary"):
        monkeypatch.setattr(verification, name, run)
    events: list[str] = []
    result = verification.verify_changed_repository(
        tmp_path, changed_paths=["app.py"], checks=["ruff", "mypy", "pytest"],
        max_parallel_checks=2, fail_fast=False, stage_callback=lambda event, data: events.append(event),
    )
    assert result["ok"] and peak == 2 and calls == 3
    assert [stage["name"] for stage in result["stages"]] == ["ruff", "mypy", "pytest_focused"]
    assert events.count("stage_started") == events.count("stage_finished") == 3


def test_parallel_syntax_failure_prevents_analyzer_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "broken.py").write_text("x =\n", encoding="utf-8")
    monkeypatch.setattr(verification, "run_ruff_summary", lambda *a, **kw: pytest.fail("analyzer dispatched"))
    result = verification.verify_changed_repository(
        tmp_path, changed_paths=["broken.py"], checks=["syntax", "ruff"], max_parallel_checks=2, fail_fast=False,
    )
    assert not result["ok"]
    assert result["stages"][0]["status"] == "failed"
    assert result["stages"][1]["status"] == "skipped"


@pytest.mark.parametrize("options", [{"fail_fast": True}, {"fail_fast": False, "ruff_fix": True}])
def test_invalid_parallel_modes_are_denied_before_planning(tmp_path: Path, options: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="parallel"):
        verification.verify_changed_repository(tmp_path, max_parallel_checks=2, **options)


def test_full_mode_targets_whole_repo_for_linters_and_typecheckers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    recorded_targets: dict[str, list[str]] = {}
    def mock_runner(name: str) -> Any:
        def _run(*a: object, targets: list[str] | None = None, **kw: object) -> dict[str, Any]:
            recorded_targets[name] = targets or []
            return {"ok": True, "exit_code": 0, "summary": "ok"}
        return _run

    monkeypatch.setattr(verification, "run_ruff_summary", mock_runner("ruff"))
    monkeypatch.setattr(verification, "run_mypy_summary", mock_runner("mypy"))
    monkeypatch.setattr(verification, "run_pytest_summary", mock_runner("pytest"))
    monkeypatch.setattr(
        verification, "select_affected_tests",
        lambda *a, **kw: {"decision": "full_suite", "tests": [], "recommendation": "full_suite"},
    )

    result = verification.verify_changed_repository(
        tmp_path, changed_paths=["app.py"], checks=["syntax", "ruff", "mypy", "pytest"],
        affected_only=False,
    )
    assert result["mode"] == "full"
    assert recorded_targets["ruff"] == ["."]
    assert recorded_targets["mypy"] == ["."]
    assert recorded_targets["pytest"] == []


def test_syntax_skipped_when_no_python_targets(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Doc\n", encoding="utf-8")
    result = verification.verify_changed_repository(
        tmp_path, changed_paths=["README.md"], checks=["syntax", "ruff", "mypy"],
        affected_only=True,
    )
    for stage in result["stages"]:
        assert stage["status"] == "skipped"
        assert stage["reason"] == "no_python_targets"


def test_parallel_catches_unhandled_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    def boom(*args: object, **kwargs: object) -> dict[str, Any]:
        raise RuntimeError("simulated crash")
    monkeypatch.setattr(verification, "run_ruff_summary", boom)
    events: list[str] = []
    result = verification.verify_changed_repository(
        tmp_path, changed_paths=["app.py"], checks=["ruff"],
        stage_callback=lambda event, data: events.append(event),
    )
    assert not result["ok"]
    assert result["required_unknown"] is True
    assert result["stages"][0]["status"] == "unavailable"
    assert "simulated crash" in result["stages"][0]["summary"]
    assert events == ["stage_started", "stage_finished"]


def test_analyze_short_circuits_on_total_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(verification, "run_ruff_summary", lambda *a, **kw: pytest.fail("should not run"))
    result = verification.verify_changed_repository(
        tmp_path, changed_paths=["app.py"], checks=["ruff"], total_timeout_sec=-1.0,
    )
    assert not result["ok"]
    assert result["required_unknown"] is True
    assert result["stages"][0]["status"] == "timed_out"
