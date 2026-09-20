from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tools.testing import verification


def _result(*, ok: bool = True, timed_out: bool = False) -> dict[str, Any]:
    return {
        "ok": ok,
        "exit_code": 0 if ok else 1,
        "timed_out": timed_out,
        "duration_ms": 1,
        "diagnostics": [],
        "summary": "ok" if ok else "failed",
    }


def test_focused_plan_runs_changed_checks_and_selected_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_text("answer = 42\n", encoding="utf-8")
    monkeypatch.setattr(
        verification,
        "select_affected_tests",
        lambda *args, **kwargs: {"decision": "focused", "tests": ["tests/test_app.py"], "recommendation": "focused"},
    )
    calls: list[tuple[str, list[str]]] = []

    def runner(name: str) -> Any:
        def run(_repo: Path, *, targets: list[str], **kwargs: Any) -> dict[str, Any]:
            calls.append((name, targets))
            return _result()

        return run

    monkeypatch.setattr(verification, "run_ruff_summary", runner("ruff"))
    monkeypatch.setattr(verification, "run_mypy_summary", runner("mypy"))
    monkeypatch.setattr(verification, "run_pytest_summary", runner("pytest"))

    result = verification.verify_changed_repository(tmp_path, changed_paths=["app.py"])

    assert result["ok"] is True
    assert [stage["name"] for stage in result["stages"]] == ["syntax", "ruff", "mypy", "pytest_focused"]
    assert calls[-1] == ("pytest", ["tests/test_app.py"])


def test_full_suite_fallback_and_explicit_full_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        verification,
        "select_affected_tests",
        lambda *args, **kwargs: {"decision": "full_suite", "tests": [], "recommendation": "full_suite"},
    )
    seen: list[list[str]] = []
    monkeypatch.setattr(verification, "run_ruff_summary", lambda *args, **kwargs: _result())
    monkeypatch.setattr(verification, "run_mypy_summary", lambda *args, **kwargs: _result())

    def record_pytest(_repo: Path, *, targets: list[str], **kwargs: Any) -> dict[str, Any]:
        seen.append(targets)
        return _result()

    monkeypatch.setattr(verification, "run_pytest_summary", record_pytest)

    fallback = verification.verify_changed_repository(tmp_path, changed_paths=["pyproject.toml"])
    explicit = verification.verify_changed_repository(tmp_path, changed_paths=[], affected_only=False, checks=["pytest"])

    assert fallback["mode"] == "full"
    assert explicit["mode"] == "full"
    assert seen == [[], []]


def test_fail_fast_skips_later_required_stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "broken.py").write_text("if True print('no')\n", encoding="utf-8")
    monkeypatch.setattr(
        verification,
        "select_affected_tests",
        lambda *args, **kwargs: {"decision": "focused", "tests": ["tests/test_x.py"], "recommendation": "focused"},
    )

    result = verification.verify_changed_repository(tmp_path, changed_paths=["broken.py"], fail_fast=True)

    assert result["ok"] is False
    assert result["stages"][0]["status"] == "failed"
    assert all(stage["status"] == "skipped" for stage in result["stages"][1:])


def test_timeout_and_unavailable_are_required_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_text("answer = 42\n", encoding="utf-8")
    monkeypatch.setattr(
        verification,
        "select_affected_tests",
        lambda *args, **kwargs: {"decision": "none", "tests": [], "recommendation": "none"},
    )
    monkeypatch.setattr(
        verification,
        "run_ruff_summary",
        lambda *args, **kwargs: {**_result(ok=False, timed_out=True), "exit_code": None},
    )

    result = verification.verify_changed_repository(tmp_path, changed_paths=["app.py"], checks=["ruff"], stage_timeout_sec=0.05)

    assert result["ok"] is False
    assert result["stages"][-1]["status"] == "timed_out"
    assert result["required_unknown"] is True


def test_invalid_check_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown verification check"):
        verification.verify_changed_repository(tmp_path, checks=["unknown"])  # type: ignore[list-item]


def test_explicit_empty_check_list_executes_nothing(tmp_path: Path) -> None:
    result = verification.verify_changed_repository(tmp_path, changed_paths=[], checks=[])

    assert result["ok"] is True
    assert result["stages"] == []
