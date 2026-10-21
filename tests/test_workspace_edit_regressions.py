import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from core.errors import ToolError
from tools.language.edits import apply_workspace_edit, prepare_workspace_edit
from tools.language.lsp import position_to_offset


@pytest.fixture(autouse=True)
def _workspace_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from types import SimpleNamespace

    from core import recovery
    from core.backups import BackupPolicy, BackupRetentionManager
    from tools.filesystem import service
    from tools.language import edits

    journal = tmp_path / "runtime" / "operation-recovery.jsonl"
    journal.parent.mkdir()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr(edits, "SETTINGS", SimpleNamespace(backup_dir=backup_dir))
    monkeypatch.setattr(edits, "BACKUP_RETENTION", BackupRetentionManager(
        backup_dir, BackupPolicy(max_bytes=0, max_age_sec=0, cleanup_interval_sec=0, recent_grace_sec=0),
    ))
    monkeypatch.setattr(service, "SETTINGS", SimpleNamespace(backup_dir=backup_dir))
    monkeypatch.setattr(service, "ensure_runtime_dirs", lambda: None)
    recovery.OPERATION_RECOVERY.configure(journal, "workspace-edit-regressions")
    try:
        yield recovery.OPERATION_RECOVERY
    finally:
        recovery.OPERATION_RECOVERY.configure(None, None)


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


def test_apply_rechecks_hash_after_prepare(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_bytes(b"before")
    plan = prepare_workspace_edit(tmp_path, {"changes": {path.as_uri(): [edit(0, 6, "after")]}})
    path.write_bytes(b"changed")

    with pytest.raises(ToolError) as raised:
        apply_workspace_edit(plan)
    assert raised.value.code == "hash_conflict"
    assert path.read_bytes() == b"changed"


def test_workspace_edit_persists_backups_and_stage_evidence(tmp_path: Path) -> None:
    from core.recovery import OPERATION_RECOVERY

    path = tmp_path / "sample.txt"
    path.write_bytes(b"before")
    plan = prepare_workspace_edit(tmp_path, {"changes": {path.as_uri(): [edit(0, 6, "after")]}})

    apply_workspace_edit(plan)

    records = [
        json.loads(line)
        for line in (tmp_path / "runtime" / "operation-recovery.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    checkpoints = [record for record in records if record.get("type") == "checkpoint"]
    assert checkpoints[-1]["workspace_edit"]["dispatch_stage"] == "may_dispatch"
    evidence = checkpoints[-1]["workspace_edit"]["files"][0]
    backup = Path(evidence["backup_path"])
    assert backup.read_bytes() == b"before"
    assert evidence["before_sha256"] == hashlib.sha256(b"before").hexdigest()
    assert evidence["after_sha256"] == hashlib.sha256(b"after").hexdigest()
    assert OPERATION_RECOVERY.protected_workspace_backups() == set()


def test_workspace_edit_rolls_back_every_written_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.recovery import OPERATION_RECOVERY
    from tools.language import edits

    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    plan = prepare_workspace_edit(tmp_path, {
        "changes": {
            first.as_uri(): [edit(0, 3, "new")],
            second.as_uri(): [edit(0, 3, "new")],
        }
    })
    real_write = edits._write_bytes
    calls = 0

    def fail_second(path: Path, data: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated write failure")
        real_write(path, data)

    monkeypatch.setattr(edits, "_write_bytes", fail_second)
    with pytest.raises(OSError, match="simulated"):
        apply_workspace_edit(plan)

    assert first.read_bytes() == b"one"
    assert second.read_bytes() == b"two"
    records = [
        json.loads(line)
        for line in (tmp_path / "runtime" / "operation-recovery.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    operation_id = next(record["operation_id"] for record in records if record.get("type") == "begin")
    assert OPERATION_RECOVERY.inspect(operation_id)["state"] == "failed"


def test_workspace_edit_restore_conflict_stays_uncertain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.recovery import OPERATION_RECOVERY
    from tools.language import edits

    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    plan = prepare_workspace_edit(tmp_path, {
        "changes": {
            first.as_uri(): [edit(0, 3, "new")],
            second.as_uri(): [edit(0, 3, "new")],
        }
    })
    real_write = edits._write_bytes
    calls = 0

    def conflicting_write(path: Path, data: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            real_write(path, b"foreign")
        elif calls == 2:
            raise OSError("simulated write failure")
        else:
            real_write(path, data)

    monkeypatch.setattr(edits, "_write_bytes", conflicting_write)
    with pytest.raises(ToolError) as raised:
        apply_workspace_edit(plan)

    assert raised.value.code == "workspace_edit_outcome_uncertain"
    records = [
        json.loads(line)
        for line in (tmp_path / "runtime" / "operation-recovery.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    operation_id = next(record["operation_id"] for record in records if record.get("type") == "begin")
    assert OPERATION_RECOVERY.inspect(operation_id)["state"] == "uncertain"
    assert OPERATION_RECOVERY.protected_workspace_backups()


def test_workspace_edit_backup_failure_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.recovery import OPERATION_RECOVERY
    from tools.filesystem import service

    path = tmp_path / "sample.txt"
    path.write_bytes(b"before")
    plan = prepare_workspace_edit(tmp_path, {"changes": {path.as_uri(): [edit(0, 6, "after")]}})

    def fail_backup(*_args: object, **_kwargs: object) -> str:
        raise ToolError("backup_write_failed", "simulated backup storage failure")

    monkeypatch.setattr(service, "_backup_file", fail_backup)
    with pytest.raises(ToolError) as raised:
        apply_workspace_edit(plan)
    assert raised.value.code == "backup_write_failed"
    assert path.read_bytes() == b"before"
    records = [
        json.loads(line)
        for line in (tmp_path / "runtime" / "operation-recovery.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    operation_id = next(record["operation_id"] for record in records if record.get("type") == "begin")
    operation = OPERATION_RECOVERY.inspect(operation_id)
    assert operation["state"] == "failed"
    assert "workspace_edit" not in operation


def test_workspace_edit_mid_batch_crash_preserves_backups_and_uncertainty(tmp_path: Path) -> None:
    from core.recovery import OperationRecoveryJournal

    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    journal_path = tmp_path / "crash" / "operation-recovery.jsonl"
    journal_path.parent.mkdir()
    state_dir = tmp_path / "crash-state"
    script = r"""
import os
import sys
from pathlib import Path

state_dir, journal_path, root = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
from core.config import SETTINGS
object.__setattr__(SETTINGS, "state_dir", state_dir)
from core import recovery
from tools.language import edits
from tools.language.edits import prepare_workspace_edit

recovery.OPERATION_RECOVERY.configure(journal_path, "crash-runtime")
first, second = root / "first.txt", root / "second.txt"
change = {"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 3}}, "newText": "new"}
plan = prepare_workspace_edit(root, {"changes": {first.as_uri(): [change], second.as_uri(): [change]}})
real_write = edits._write_bytes

def crash_after_first(path, data):
    real_write(path, data)
    if path == first:
        os._exit(23)

edits._write_bytes = crash_after_first
edits.apply_workspace_edit(plan)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(state_dir), str(journal_path), str(tmp_path)],
        cwd=Path.cwd(),
        env={**os.environ, "PYTHONPATH": str(Path.cwd())},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 23, completed.stderr
    assert first.read_bytes() == b"new"
    assert second.read_bytes() == b"two"

    recovered = OperationRecoveryJournal()
    recovered.configure(journal_path, "after-crash")
    records = [
        json.loads(line)
        for line in journal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    operation_id = next(record["operation_id"] for record in records if record.get("type") == "begin")
    operation = recovered.inspect(operation_id)
    assert operation["state"] == "uncertain"
    assert operation["workspace_edit"]["dispatch_stage"] == "may_dispatch"
    protected = recovered.protected_workspace_backups()
    assert len(protected) == 2
    assert all(path.is_relative_to(state_dir) for path in protected)
    assert {path.read_bytes() for path in protected} == {b"one", b"two"}
    assert first.read_bytes() == b"new"
    assert second.read_bytes() == b"two"


def test_workspace_edit_prepared_process_crash_does_not_write_or_replay(tmp_path: Path) -> None:
    from core.recovery import OperationRecoveryJournal

    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    journal_path = tmp_path / "prepared-crash" / "operation-recovery.jsonl"
    journal_path.parent.mkdir()
    state_dir = tmp_path / "prepared-crash-state"
    script = r"""
import os
import sys
from pathlib import Path

state_dir, journal_path, root = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
from core.config import SETTINGS
object.__setattr__(SETTINGS, "state_dir", state_dir)
from core import recovery
from tools.language import edits
from tools.language.edits import prepare_workspace_edit

recovery.OPERATION_RECOVERY.configure(journal_path, "prepared-crash-runtime")
first, second = root / "first.txt", root / "second.txt"
change = {"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 3}}, "newText": "new"}
plan = prepare_workspace_edit(root, {"changes": {first.as_uri(): [change], second.as_uri(): [change]}})
real_checkpoint = recovery.OPERATION_RECOVERY.checkpoint_workspace_edit

def crash_after_prepared(handle, evidence, *, stage):
    real_checkpoint(handle, evidence, stage=stage)
    if stage == "prepared":
        os._exit(24)

recovery.OPERATION_RECOVERY.checkpoint_workspace_edit = crash_after_prepared
edits.apply_workspace_edit(plan)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(state_dir), str(journal_path), str(tmp_path)],
        cwd=Path.cwd(),
        env={**os.environ, "PYTHONPATH": str(Path.cwd())},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 24, completed.stderr
    assert first.read_bytes() == b"one"
    assert second.read_bytes() == b"two"

    recovered = OperationRecoveryJournal()
    recovered.configure(journal_path, "after-prepared-crash")
    records = [
        json.loads(line)
        for line in journal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    operation_id = next(record["operation_id"] for record in records if record.get("type") == "begin")
    operation = recovered.inspect(operation_id)
    assert operation["state"] == "failed"
    assert operation["workspace_edit"]["dispatch_stage"] == "prepared"
    assert all(Path(item["backup_path"]).is_file() for item in operation["workspace_edit"]["files"])
    assert all(Path(item["backup_path"]).is_relative_to(state_dir) for item in operation["workspace_edit"]["files"])
    assert recovered.protected_workspace_backups() == set()


def test_workspace_edit_rejects_oversized_evidence_before_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import recovery
    from tools.language import edits

    path = tmp_path / "sample.txt"
    path.write_bytes(b"before")
    plan = prepare_workspace_edit(tmp_path, {"changes": {path.as_uri(): [edit(0, 6, "after")]}})
    existing = set(edits.SETTINGS.backup_dir.glob("*.bak"))
    monkeypatch.setattr(recovery, "_MAX_WORKSPACE_EVIDENCE_BYTES", 1)

    with pytest.raises(ToolError) as raised:
        apply_workspace_edit(plan)
    assert raised.value.code == "lsp_edit_evidence_too_large"
    assert path.read_bytes() == b"before"
    assert set(edits.SETTINGS.backup_dir.glob("*.bak")) == existing


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


@pytest.mark.parametrize("text,line,character", [
    ("abc", 0, -1), ("a\U0001f600b", 0, 2),
    ("abc", False, 0), ("abc", True, 0), ("abc", 0, False), ("abc", 0, True),
])
def test_invalid_lsp_positions(text: str, line: int, character: int) -> None:
    with pytest.raises(ToolError):
        position_to_offset(text, {"line": line, "character": character})
