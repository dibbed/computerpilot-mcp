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


def _versioned(path: Path, version: int, replacement: str = "new") -> dict[str, Any]:
    return {
        "textDocument": {"uri": path.as_uri(), "version": version},
        "edits": [edit(0, 3, replacement)],
    }


def test_stale_document_version_prevents_all_writes(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    payload = {"documentChanges": [_versioned(first, 2), _versioned(second, 1)]}

    with pytest.raises(ToolError) as missing:
        prepare_workspace_edit(tmp_path, payload)
    assert missing.value.code == "lsp_document_version_conflict"

    with pytest.raises(ToolError) as mismatch:
        prepare_workspace_edit(
            tmp_path,
            payload,
            document_versions={first.as_uri(): 1, second.as_uri(): 1},
        )
    assert mismatch.value.code == "lsp_document_version_conflict"
    assert first.read_bytes() == b"one"
    assert second.read_bytes() == b"two"


@pytest.mark.parametrize("version", [-1, True, "1", 1.0])
def test_invalid_document_versions_are_rejected_without_writes(
    tmp_path: Path, version: object
) -> None:
    path = tmp_path / "sample.txt"
    path.write_bytes(b"abc")
    payload = {
        "documentChanges": [
            {
                "textDocument": {"uri": path.as_uri(), "version": version},
                "edits": [edit(0, 1, "X")],
            }
        ]
    }

    with pytest.raises(ToolError) as raised:
        prepare_workspace_edit(tmp_path, payload)
    assert raised.value.code == "lsp_edit_unsupported"
    assert path.read_bytes() == b"abc"


def test_apply_rechecks_disk_before_any_write(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    payload = {
        "changes": {
            first.as_uri(): [edit(0, 3, "new")],
            second.as_uri(): [edit(0, 3, "new")],
        }
    }
    plan = prepare_workspace_edit(tmp_path, payload)
    first.write_bytes(b"changed")

    with pytest.raises(ToolError) as raised:
        apply_workspace_edit(plan)
    assert raised.value.code == "hash_conflict"
    assert first.read_bytes() == b"changed"
    assert second.read_bytes() == b"two"


def test_duplicate_edit_denied_without_double_insert(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_bytes(b"abc")
    duplicate = edit(0, 1, "X")
    alias = path.as_uri().replace("sample.txt", "%73ample.txt")
    payload = {"changes": {path.as_uri(): [duplicate], alias: [dict(duplicate)]}}

    with pytest.raises(ToolError) as raised:
        prepare_workspace_edit(tmp_path, payload)
    assert raised.value.code == "lsp_edit_duplicate"
    assert path.read_bytes() == b"abc"


@pytest.mark.parametrize(
    "bad_edit",
    [
        {"range": None, "newText": "x"},
        {"range": {"start": None, "end": None}, "newText": "x"},
        {"range": {"start": {}, "end": {}}, "newText": "x"},
        {"newText": "x"},
    ],
)
def test_missing_range_returns_safe_error(tmp_path: Path, bad_edit: dict[str, Any]) -> None:
    path = tmp_path / "sample.txt"
    path.write_bytes(b"abc")
    with pytest.raises(ToolError) as raised:
        prepare_workspace_edit(tmp_path, {"changes": {path.as_uri(): [bad_edit]}})
    assert raised.value.code in {"lsp_edit_unsupported", "lsp_position_invalid"}
    assert path.read_bytes() == b"abc"


def test_edit_budget_rejects_before_reading_extra_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deny_read(_path: Path) -> bytes:
        pytest.fail("workspace edit budget was checked after reading a file")

    monkeypatch.setattr(Path, "read_bytes", deny_read)
    many_files = {
        "changes": {
            (tmp_path / f"file-{index}.py").as_uri(): [edit(0, 0, "x")]
            for index in range(101)
        }
    }
    with pytest.raises(ToolError) as files:
        prepare_workspace_edit(tmp_path, many_files)
    assert files.value.code == "lsp_edit_budget"

    many_edits = {"changes": {(tmp_path / "many.py").as_uri(): [edit(0, 0, "x") for _ in range(1001)]}}
    with pytest.raises(ToolError) as edits:
        prepare_workspace_edit(tmp_path, many_edits)
    assert edits.value.code == "lsp_edit_budget"

    oversized = {"changes": {(tmp_path / "large.py").as_uri(): [edit(0, 0, "x" * (2 * 1024 * 1024 + 1))]}}
    with pytest.raises(ToolError) as replacement:
        prepare_workspace_edit(tmp_path, oversized)
    assert replacement.value.code == "lsp_edit_budget"


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
