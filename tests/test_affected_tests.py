from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _write_project(root: Path) -> None:
    for directory in (root / "app", root / "tests"):
        directory.mkdir(parents=True, exist_ok=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "service.py").write_text("def create() -> int:\n    return 1\n", encoding="utf-8")
    (root / "app" / "api.py").write_text("from app.service import create\n", encoding="utf-8")
    (root / "tests" / "test_service.py").write_text(
        "from app.service import create\n\ndef test_create() -> None:\n    assert create() == 1\n", encoding="utf-8"
    )
    (root / "tests" / "test_api.py").write_text("from app import api\n\ndef test_api() -> None:\n    assert api\n", encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout


def _init_git(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "initial")


def test_affected_tests_selects_direct_and_reverse_importers_with_evidence(tmp_path: Path) -> None:
    from tools.testing.impact import select_affected_tests

    _write_project(tmp_path)

    result = select_affected_tests(tmp_path, changed_paths=["app/service.py"], max_depth=3)

    selected = {item["path"]: set(item["evidence"]) for item in result["tests"]}
    assert "tests/test_service.py" in selected
    assert "direct_import" in selected["tests/test_service.py"]
    assert "tests/test_api.py" in selected
    assert "reverse_dependency" in selected["tests/test_api.py"]
    assert result["decision"] == "focused"
    assert result["complete"] is True


@pytest.mark.parametrize("changed", ["conftest.py", "pyproject.toml", "requirements.txt", "tests/plugins.py"])
def test_affected_tests_requires_full_suite_for_repository_wide_risk(tmp_path: Path, changed: str) -> None:
    from tools.testing.impact import select_affected_tests

    _write_project(tmp_path)
    target = tmp_path / changed
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")

    result = select_affected_tests(tmp_path, changed_paths=[changed])

    assert result["decision"] == "full_suite"
    assert result["full_suite_reasons"]


def test_affected_tests_returns_none_only_for_confidently_non_executable_changes(tmp_path: Path) -> None:
    from tools.testing.impact import select_affected_tests

    _write_project(tmp_path)
    (tmp_path / "README.md").write_text("docs", encoding="utf-8")

    result = select_affected_tests(tmp_path, changed_paths=["README.md"])

    assert result["decision"] == "none"
    assert result["tests"] == []
    assert result["complete"] is True


def test_affected_tests_discovers_staged_unstaged_untracked_and_renamed_git_paths(tmp_path: Path) -> None:
    from tools.testing.impact import discover_changed_paths

    _write_project(tmp_path)
    _init_git(tmp_path)
    (tmp_path / "app" / "service.py").write_text("def create() -> int:\n    return 2\n", encoding="utf-8")
    _git(tmp_path, "mv", "app/api.py", "app/http.py")
    (tmp_path / "tests" / "test_new.py").write_text("def test_new() -> None:\n    pass\n", encoding="utf-8")
    _git(tmp_path, "add", "app/http.py")

    changed = discover_changed_paths(tmp_path)

    by_path = {item.path: item for item in changed.items}
    assert "app/service.py" in by_path
    assert "app/http.py" in by_path
    assert by_path["app/http.py"].old_path == "app/api.py"
    assert "tests/test_new.py" in by_path


def test_affected_tests_reports_incomplete_scan_and_conservative_fallback(tmp_path: Path) -> None:
    from tools.testing.impact import select_affected_tests

    _write_project(tmp_path)

    result = select_affected_tests(tmp_path, changed_paths=["app/service.py"], max_files=1)

    assert result["complete"] is False
    assert result["scan_truncated"] is True
    assert result["decision"] in {"focused_plus_full_recommended", "full_suite"}


def test_affected_tests_maps_deleted_module_from_imports(tmp_path: Path) -> None:
    from tools.testing.impact import select_affected_tests

    _write_project(tmp_path)
    (tmp_path / "app" / "service.py").unlink()

    result = select_affected_tests(tmp_path, changed_paths=["app/service.py"])

    assert result["decision"] == "focused"
    selected = next(item for item in result["tests"] if item["path"] == "tests/test_service.py")
    assert "direct_import" in selected["evidence"]
