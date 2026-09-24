from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from core.errors import ToolError
from tools.language.edits import apply_workspace_edit, prepare_workspace_edit
from tools.language.lsp import position_to_offset


def edit(start: int, end: int, replacement: str) -> dict[str, Any]:
    return {"range": {"start": {"line": 0, "character": start}, "end": {"line": 0, "character": end}}, "newText": replacement}


def test_reversed_range_is_rejected_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_bytes(b"abcdef")
    with pytest.raises(ToolError, match="range"):
        prepare_workspace_edit(tmp_path, {"changes": {path.as_uri(): [edit(4, 2, "X")]}})
    assert path.read_bytes() == b"abcdef"


def test_uri_aliases_are_applied_as_one_file(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_bytes(b"abcdef")
    uri = path.as_uri()
    alias = uri.replace("sample.txt", "%73ample.txt")
    plan = prepare_workspace_edit(tmp_path, {"changes": {uri: [edit(0, 1, "A")], alias: [edit(5, 6, "F")]}})
    result = apply_workspace_edit(plan)
    assert path.read_bytes() == b"AbcdeF"
    assert result["file_count"] == 1


def test_prepare_does_not_mutate_input(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_bytes(b"abcdef")
    payload = {
        "changes": {path.as_uri(): [edit(0, 1, "A")]},
        "documentChanges": [
            {"textDocument": {"uri": path.as_uri()}, "edits": [edit(5, 6, "F")]},
        ],
    }
    original = deepcopy(payload)
    plan = prepare_workspace_edit(tmp_path, payload)
    assert payload == original
    assert prepare_workspace_edit(tmp_path, payload) == plan


def test_required_hashes_cover_every_file_in_workspace_edit(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("alpha = 1\n", encoding="utf-8")
    second.write_text("alpha = 2\n", encoding="utf-8")
    payload = {
        "changes": {
            first.as_uri(): [edit(0, 5, "beta")],
            second.as_uri(): [edit(0, 5, "beta")],
        }
    }
    import hashlib

    first_hash = hashlib.sha256(first.read_bytes()).hexdigest()
    with pytest.raises(ToolError) as raised:
        prepare_workspace_edit(
            tmp_path,
            payload,
            expected_sha256={"first.py": first_hash},
            require_expected_sha256=True,
        )
    assert raised.value.code == "lsp_edit_precondition_required"


def test_same_position_insertions_keep_server_order(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_bytes(b"ab")
    plan = prepare_workspace_edit(tmp_path, {"changes": {path.as_uri(): [edit(1, 1, "Z"), edit(1, 1, "A")]}})
    apply_workspace_edit(plan)
    assert path.read_bytes() == b"aZAb"


@pytest.mark.parametrize(
    "text,line,character,offset",
    [
        ("", 0, 0, 0),
        ("a\n", 1, 0, 2),
        ("a\r\n", 1, 0, 3),
        ("a\u2028b", 0, 3, 3),
        ("a\U0001f600b", 0, 3, 2),
    ],
)
def test_valid_lsp_positions(text: str, line: int, character: int, offset: int) -> None:
    assert position_to_offset(text, {"line": line, "character": character}) == offset


@pytest.mark.parametrize("text,line,character", [("abc", 0, -1), ("a\U0001f600b", 0, 2)])
def test_invalid_lsp_positions(text: str, line: int, character: int) -> None:
    with pytest.raises(ToolError):
        position_to_offset(text, {"line": line, "character": character})
