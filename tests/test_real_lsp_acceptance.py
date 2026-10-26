from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts import real_lsp_acceptance as acceptance
from scripts.real_lsp_acceptance import diagnostic_items, location_matches, type_error_matches


def _location(path: Path, start: int, end: int) -> dict[str, object]:
    return {
        "uri": path.as_uri(),
        "range": {"start": {"line": 0, "character": start}, "end": {"line": 0, "character": end}},
    }


def test_definition_acceptance_checks_exact_path_and_symbol_range(tmp_path: Path) -> None:
    target = tmp_path / "service space.py"
    target.write_text("def calculate(value): pass\n", encoding="utf-8")
    other = tmp_path / "other" / target.name
    other.parent.mkdir()
    other.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    assert location_matches(_location(target, 4, 13), target)
    assert not location_matches(_location(other, 4, 13), target)
    assert not location_matches(_location(target, 0, 3), target)
    assert not location_matches(_location(target, 4, 1000), target)
    link = {"targetUri": target.as_uri(), "targetSelectionRange": _location(target, 4, 13)["range"]}
    assert location_matches([link], target)


def test_reference_acceptance_requires_the_call_site(tmp_path: Path) -> None:
    target = tmp_path / "main.ts"
    target.write_text("import { calculate } from './service';\nconst result = calculate(2);\n", encoding="utf-8")
    imported = _location(target, 9, 18)
    call = {
        "uri": target.as_uri(),
        "range": {"start": {"line": 1, "character": 15}, "end": {"line": 1, "character": 24}},
    }
    position = {"line": 1, "character": 16}
    assert not location_matches(imported, target, position=position)
    assert location_matches(call, target, position=position)


@pytest.mark.parametrize("report", [None, {}, {"kind": "unchanged", "resultId": "old"}, {"kind": "full", "items": "bad"}])
def test_diagnostic_acceptance_requires_actual_full_items(report: object) -> None:
    with pytest.raises(AssertionError):
        diagnostic_items(report)


def test_type_error_acceptance_rejects_unrelated_or_warning_diagnostics() -> None:
    error = {
        "severity": 1,
        "message": "Argument of type 'string' is not assignable to parameter of type 'number'.",
        "range": {"start": {"line": 1, "character": 25}, "end": {"line": 1, "character": 39}},
    }
    assert type_error_matches([error], "typescript", 1)
    assert not type_error_matches([{**error, "severity": 2}], "typescript", 1)
    assert not type_error_matches([error], "typescript", 2)
    assert not type_error_matches([{**error, "message": "Unrelated parse failure"}], "typescript", 1)
    literal_error = {**error, "code": "reportArgumentType", "message": "Literal['not-an-int'] is not assignable to int"}
    assert type_error_matches([literal_error], "python", 1)
    php_error = {**error, "code": "P1006", "message": "Expected type 'int'. Found ''not-an-int''."}
    assert type_error_matches([php_error], "php", 1)


@pytest.mark.parametrize("cleanup", ["PASS", "FAIL"])
@pytest.mark.parametrize("existing_scratch", [True, False])
def test_main_isolates_settings_and_retains_unresolved_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cleanup: str, existing_scratch: bool,
) -> None:
    previous = acceptance.sessions.SETTINGS
    scratch_variables = ("TEMP", "TMP") if os.name == "nt" else ("TMPDIR",)
    for name in scratch_variables:
        if existing_scratch:
            monkeypatch.setenv(name, f"previous-{name}")
        else:
            monkeypatch.delenv(name, raising=False)
    previous_environment = {name: os.environ.get(name) for name in scratch_variables}

    async def failing_execution(
        case_name: str, executable: Path, entrypoint: Path | None, workspace: Path, results: dict[str, Any], **kwargs: Any,
    ) -> None:
        assert acceptance.sessions.SETTINGS.state_dir == workspace / ".state"
        assert (workspace / ".tmp").is_dir()
        assert all(os.environ[name] == str(workspace / ".tmp") for name in scratch_variables)
        results["cleanup"] = cleanup
        raise AssertionError("probe failed")

    monkeypatch.setattr(acceptance, "REPO", tmp_path)
    monkeypatch.setattr(acceptance, "execute", failing_execution)
    monkeypatch.setattr(sys, "argv", ["real_lsp_acceptance.py", "python", "--executable", "unused-node.exe"])
    assert acceptance.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert acceptance.sessions.SETTINGS is previous
    assert {name: os.environ.get(name) for name in scratch_variables} == previous_environment
    assert result["workspace_removed"] is (cleanup == "PASS")
    remaining = list((tmp_path / "tests" / ".real_lsp_runs").iterdir())
    assert bool(remaining) is (cleanup != "PASS")
