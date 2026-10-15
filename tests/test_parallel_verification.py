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
