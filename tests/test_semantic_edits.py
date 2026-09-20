from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from core.errors import ToolError
from tools.language.edits import apply_workspace_edit, prepare_workspace_edit


def _edit(path: Path) -> dict[str, object]:
    return {
        "changes": {
            path.as_uri(): [
                {"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 3}}, "newText": "new"},
                {"range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}}, "newText": "name"},
            ]
        }
    }


def test_workspace_edit_plans_and_applies_multi_edit_with_hash_guard(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("old key\n", encoding="utf-8")
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    plan = prepare_workspace_edit(tmp_path, _edit(path), expected_sha256={"a.py": expected})
    result = apply_workspace_edit(plan)
    assert result["file_count"] == 1
    assert path.read_text(encoding="utf-8") == "new name\n"


def test_workspace_edit_rejects_overlap_stale_hash_and_external_path(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("old key\n", encoding="utf-8")
    with pytest.raises(ToolError, match="precondition"):
        prepare_workspace_edit(tmp_path, _edit(path), expected_sha256={"a.py": "0" * 64})
    overlap = _edit(path)
    overlap["changes"][path.as_uri()].append(  # type: ignore[index]
        {"range": {"start": {"line": 0, "character": 1}, "end": {"line": 0, "character": 5}}, "newText": "x"}
    )
    with pytest.raises(ToolError, match="overlap"):
        prepare_workspace_edit(tmp_path, overlap)
    with pytest.raises(ToolError, match="outside"):
        prepare_workspace_edit(tmp_path, {"changes": {(tmp_path.parent / "x.py").as_uri(): []}})


def test_workspace_edit_accepts_text_document_edits_and_rejects_resource_operations(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("old\n", encoding="utf-8")
    plan = prepare_workspace_edit(
        tmp_path,
        {
            "documentChanges": [
                {
                    "textDocument": {"uri": path.as_uri(), "version": 1},
                    "edits": [{"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 3}}, "newText": "new"}],
                }
            ]
        },
    )
    apply_workspace_edit(plan)
    assert path.read_text(encoding="utf-8") == "new\n"
    with pytest.raises(ToolError, match="resource operations"):
        prepare_workspace_edit(tmp_path, {"documentChanges": [{"kind": "create", "uri": (tmp_path / "b.py").as_uri()}]})
