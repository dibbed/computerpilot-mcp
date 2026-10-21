from __future__ import annotations

import asyncio
import inspect
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from mcp import Client

from core import heartbeat, recovery
from core.errors import ToolError
from core.process_ownership import spawn_owned_process
from core.recovery import JOURNAL_SCHEMA_VERSION, OperationRecoveryJournal
from core.recovery_models import Evidence, OperationState
from core.registry import create_server
from core.tooling import _mutation_target


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _workspace_evidence(tmp_path: Path, count: int = 1) -> recovery.WorkspaceEditEvidence:
    return recovery.WorkspaceEditEvidence(
        files=tuple(
            recovery.WorkspaceEditFile(
                path=str((tmp_path / f"file-{index}.py").resolve()),
                before_sha256="a" * 64,
                after_sha256="b" * 64,
                backup_path=str((tmp_path / "backups" / f"{index}.bak").resolve()),
            )
            for index in range(count)
        )
    )


def test_workspace_edit_checkpoint_survives_restart_and_compaction(tmp_path: Path) -> None:
    path = tmp_path / "workspace.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "before")
    handle = journal.begin("workspace_edit", "2 files")
    assert handle is not None
    evidence = _workspace_evidence(tmp_path, 2)
    journal.checkpoint_workspace_edit(handle, evidence, stage="prepared")
    journal.checkpoint_workspace_edit(handle, evidence, stage="may_dispatch")

    recovered = OperationRecoveryJournal()
    recovered.configure(path, "after")
    recovered.compact()

    operation = recovered.inspect(handle.operation_id)
    assert operation["state"] == "uncertain"
    assert operation["workspace_edit"]["dispatch_stage"] == "may_dispatch"
    assert operation["workspace_edit"]["files"] == [
        {
            "path": item.path,
            "before_sha256": item.before_sha256,
            "after_sha256": item.after_sha256,
            "backup_path": item.backup_path,
        }
        for item in evidence.files
    ]
    assert recovered.protected_workspace_backups() == {
        Path(item.backup_path) for item in evidence.files
    }
    serialized = json.dumps(operation)
    assert "newText" not in serialized
    assert "before content" not in serialized


def test_workspace_edit_prepared_crash_recovers_failed_without_replay(tmp_path: Path) -> None:
    path = tmp_path / "workspace.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "before")
    handle = journal.begin("workspace_edit")
    assert handle is not None
    journal.checkpoint_workspace_edit(handle, _workspace_evidence(tmp_path), stage="prepared")
    journal.finish(handle, known_result="raised")

    recovered = OperationRecoveryJournal()
    recovered.configure(path, "after")
    operation = recovered.inspect(handle.operation_id)
    assert operation["state"] == "failed"
    assert recovered.protected_workspace_backups() == set()


def test_workspace_edit_checkpoint_rejects_invalid_or_unbounded_evidence(tmp_path: Path) -> None:
    journal = OperationRecoveryJournal()
    journal.configure(tmp_path / "workspace.jsonl", "runtime")
    handle = journal.begin("workspace_edit")
    assert handle is not None

    with pytest.raises(ToolError) as stage:
        journal.checkpoint_workspace_edit(handle, _workspace_evidence(tmp_path), stage="may_dispatch")
    assert stage.value.code == "operation_checkpoint_conflict"

    relative = recovery.WorkspaceEditEvidence(
        files=(recovery.WorkspaceEditFile("relative.py", "a" * 64, "b" * 64, str(tmp_path / "x.bak")),)
    )
    with pytest.raises(ToolError) as bad_path:
        journal.checkpoint_workspace_edit(handle, relative, stage="prepared")
    assert bad_path.value.code == "invalid_workspace_edit_evidence"

    oversized = recovery.WorkspaceEditEvidence(
        files=tuple(
            recovery.WorkspaceEditFile(
                path=str(tmp_path / ("p" * 400)),
                before_sha256="a" * 64,
                after_sha256="b" * 64,
                backup_path=str(tmp_path / f"{index}.bak"),
            )
            for index in range(100)
        )
    )
    with pytest.raises(ToolError) as too_large:
        journal.checkpoint_workspace_edit(handle, oversized, stage="prepared")
    assert too_large.value.code == "lsp_edit_evidence_too_large"


def test_protected_workspace_backups_fail_closed_over_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recovery, "_MAX_WORKSPACE_PROTECTED_PATHS", 1)
    journal = OperationRecoveryJournal()
    journal.configure(tmp_path / "workspace.jsonl", "runtime")
    handle = journal.begin("workspace_edit")
    assert handle is not None
    evidence = _workspace_evidence(tmp_path, 2)
    journal.checkpoint_workspace_edit(handle, evidence, stage="prepared")
    journal.checkpoint_workspace_edit(handle, evidence, stage="may_dispatch")

    with pytest.raises(ToolError) as raised:
        journal.protected_workspace_backups()
    assert raised.value.code == "workspace_backup_protection_limit"


def test_workspace_edit_mutation_reuses_only_same_task_scope(tmp_path: Path) -> None:
    path = tmp_path / "workspace.jsonl"
    recovery.OPERATION_RECOVERY.configure(path, "scope-runtime")
    try:
        outer = recovery.OPERATION_RECOVERY.begin("outer_tool")
        assert outer is not None

        with recovery.operation_recovery_context(outer):
            with recovery.workspace_edit_mutation((tmp_path / "first.txt",)) as reused:
                assert reused.operation_id == outer.operation_id

            async def child() -> str:
                with recovery.workspace_edit_mutation((tmp_path / "second.txt",)) as owned:
                    assert owned.operation_id != outer.operation_id
                    return owned.operation_id

            child_id = asyncio.run(child())
        recovery.OPERATION_RECOVERY.finish(outer, known_result="ok")
    finally:
        recovery.OPERATION_RECOVERY.configure(None, None)

    records = _records(path)
    begins = [record for record in records if record.get("type") == "begin"]
    assert {record["operation_id"] for record in begins} == {outer.operation_id, child_id}
    assert next(record for record in begins if record["operation_id"] == child_id)["operation_type"] == "workspace_edit"


def test_workspace_edit_mutation_reuses_owning_task_in_worker_thread(tmp_path: Path) -> None:
    path = tmp_path / "workspace.jsonl"
    recovery.OPERATION_RECOVERY.configure(path, "worker-runtime")
    try:
        outer = recovery.OPERATION_RECOVERY.begin("rename_symbol")
        assert outer is not None

        def use_workspace_edit() -> str:
            with recovery.workspace_edit_mutation((tmp_path / "worker.txt",)) as handle:
                return handle.operation_id

        async def scenario() -> str:
            with recovery.operation_recovery_context(outer):
                return await asyncio.to_thread(use_workspace_edit)

        worker_id = asyncio.run(scenario())
        recovery.OPERATION_RECOVERY.finish(outer, known_result="ok")
    finally:
        recovery.OPERATION_RECOVERY.configure(None, None)

    assert worker_id == outer.operation_id
    begins = [record for record in _records(path) if record.get("type") == "begin"]
    assert [record["operation_id"] for record in begins] == [outer.operation_id]


def test_workspace_edit_mutation_denies_when_unresolved_quota_is_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "workspace.jsonl"
    recovery.OPERATION_RECOVERY.configure(path, "quota-runtime")
    monkeypatch.setattr(recovery.OPERATION_RECOVERY, "_max_unresolved_workspace", 0)
    try:
        with pytest.raises(ToolError) as raised:
            with recovery.workspace_edit_mutation((tmp_path / "denied.txt",)):
                pytest.fail("workspace edit admission must be denied before any operation starts")
    finally:
        recovery.OPERATION_RECOVERY.configure(None, None)

    assert raised.value.code == "workspace_edit_limit"
    assert not path.exists() or not any(record.get("type") == "begin" for record in _records(path))


def test_workspace_edit_checkpoint_denies_second_unresolved_operation(tmp_path: Path) -> None:
    journal = OperationRecoveryJournal(max_unresolved_workspace=1)
    path = tmp_path / "workspace.jsonl"
    journal.configure(path, "quota-runtime")
    try:
        first = journal.begin("workspace_edit")
        assert first is not None
        evidence = _workspace_evidence(tmp_path)
        journal.checkpoint_workspace_edit(first, evidence, stage="prepared")
        second = journal.begin("workspace_edit")
        assert second is not None

        with pytest.raises(ToolError) as raised:
            journal.checkpoint_workspace_edit(second, evidence, stage="prepared")
        assert raised.value.code == "workspace_edit_limit"
        assert journal.workspace_edit_stage(second) is None
    finally:
        journal.configure(None, None)


def test_old_journal_records_and_new_checkpoint_compact_without_loss(tmp_path: Path) -> None:
    path = tmp_path / "workspace.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "before")
    legacy = journal.begin("legacy_tool")
    assert legacy is not None
    journal.finish(legacy, known_result="ok")
    handle = journal.begin("workspace_edit")
    assert handle is not None
    evidence = _workspace_evidence(tmp_path)
    journal.checkpoint_workspace_edit(handle, evidence, stage="prepared")
    journal.checkpoint_workspace_edit(handle, evidence, stage="may_dispatch")

    journal.compact()

    recovered = OperationRecoveryJournal()
    recovered.configure(path, "after")
    operation = recovered.inspect(handle.operation_id)
    assert operation["state"] == "uncertain"
    assert operation["workspace_edit"]["dispatch_stage"] == "may_dispatch"
    assert recovered.protected_workspace_backups() == {Path(evidence.files[0].backup_path)}


def test_workspace_edit_mutation_requires_enabled_recovery(tmp_path: Path) -> None:
    recovery.OPERATION_RECOVERY.configure(None, None)
    with pytest.raises(ToolError) as raised:
        with recovery.workspace_edit_mutation((tmp_path / "target.txt",)):
            pass
    assert raised.value.code == "recovery_unavailable"


def test_mutation_target_includes_workflow_and_operation_identity() -> None:
    def operation(workflow_id: str, operation_id: str) -> None:
        del workflow_id, operation_id

    target = _mutation_target(
        inspect.signature(operation),
        (),
        {"workflow_id": "workflow-123", "operation_id": "operation-456"},
    )

    assert target == "workflow_id=workflow-123;operation_id=operation-456"


def test_uia_checkpoint_preserves_file_evidence_across_restart_and_compaction(tmp_path: Path) -> None:
    from core.recovery_models import NativeUIATarget

    path = tmp_path / "native.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "before")
    handle = journal.begin("ui_set_value")
    assert handle is not None
    target = NativeUIATarget(pid=123, process_created=123.0, window_handle=10, element_runtime_id=(42, 9))
    postcondition = {"kind": "file_sha256", "expected": {"path": str(tmp_path / "target"), "sha256": "a" * 64}}
    journal.checkpoint_postcondition(handle, postcondition)
    journal.checkpoint_native_uia(handle, target, stage="prepared")
    journal.checkpoint_native_uia(handle, target, stage="may_dispatch")
    recovered = OperationRecoveryJournal()
    recovered.configure(path, "after")
    recovered.compact()
    operation = recovered.inspect(handle.operation_id)
    assert operation["state"] == "uncertain"
    assert operation["postcondition"] == postcondition
    assert operation["native"]["target"]["element_runtime_id"] == [42, 9]
    assert operation["target"] is not None
    assert recovered.summary()["uncertain_count"] == 1


@pytest.mark.parametrize("dispatched", [False, True])
def test_native_exception_does_not_claim_rollback_after_dispatch(tmp_path: Path, dispatched: bool) -> None:
    from core.recovery_models import NativeUIATarget

    journal = OperationRecoveryJournal()
    journal.configure(tmp_path / "native.jsonl", "runtime")
    handle = journal.begin("ui_set_value")
    assert handle is not None
    journal.checkpoint_native_uia(handle, NativeUIATarget(window_handle=10), stage="prepared")
    if dispatched:
        journal.checkpoint_native_uia(handle, NativeUIATarget(window_handle=10), stage="may_dispatch")
    journal.finish(handle, known_result="raised")
    assert journal.inspect(handle.operation_id)["state"] == ("uncertain" if dispatched else "failed")


def test_uia_identity_is_bounded_and_checkpoint_cannot_change_target_or_runtime(tmp_path: Path) -> None:
    from core.recovery_models import NativeUIATarget

    for values in ({"pid": True}, {"element_runtime_id": (1,) * 33}, {"process_created": float("nan")}):
        with pytest.raises(ValueError):
            NativeUIATarget(**values)
    journal = OperationRecoveryJournal()
    path = tmp_path / "native.jsonl"
    journal.configure(path, "before")
    handle = journal.begin("ui_set_value")
    assert handle is not None
    target = NativeUIATarget(window_handle=10)
    journal.checkpoint_native_uia(handle, target, stage="prepared")
    with pytest.raises(ToolError, match="identity or stage changed"):
        journal.checkpoint_native_uia(handle, NativeUIATarget(window_handle=20), stage="may_dispatch")
    journal.checkpoint_native_uia(handle, target, stage="may_dispatch")
    with pytest.raises(ToolError):
        journal.checkpoint_native_uia(handle, target, stage="prepared")
    journal.configure(path, "after")
    with pytest.raises(ToolError):
        journal.checkpoint_native_uia(handle, target, stage="may_dispatch")


def test_uia_view_does_not_trust_unvalidated_persisted_target_label(tmp_path: Path) -> None:
    from core.recovery_models import NativeUIATarget

    path = tmp_path / "native.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "before")
    handle = journal.begin("ui_set_value")
    assert handle is not None
    journal.checkpoint_native_uia(handle, NativeUIATarget(window_handle=10), stage="prepared")
    records = _records(path)
    records[-1]["target"] = "private-forged-label-canary"
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    recovered = OperationRecoveryJournal()
    recovered.configure(path, "after")
    assert "private-forged-label-canary" not in json.dumps(recovered.inspect(handle.operation_id))
    assert "private-forged-label-canary" not in json.dumps(recovered.summary())


@pytest.mark.parametrize("crash_at", ["prepared", "after_effect"])
def test_native_uia_real_process_crash_preserves_evidence_without_replay(tmp_path: Path, crash_at: str) -> None:
    path, effect = tmp_path / "native.jsonl", tmp_path / "effect.txt"
    script = """
import os,sys
from pathlib import Path
from core import recovery
from core.recovery_models import NativeUIATarget
path,effect,crash_at=sys.argv[1:]
recovery.OPERATION_RECOVERY.configure(Path(path),'child')
append=recovery.OPERATION_RECOVERY._append_unlocked_file
def record(payload):
    append(payload)
    if crash_at=='prepared' and payload.get('native',{}).get('dispatch_stage')=='prepared':
        os._exit(23)
recovery.OPERATION_RECOVERY._append_unlocked_file=record
with recovery.native_uia_mutation('ui_set_value',NativeUIATarget(window_handle=10,element_runtime_id=(42,9))):
    with open(effect,'a',encoding='utf-8') as marker:
        marker.write('1')
        marker.flush()
        os.fsync(marker.fileno())
    os._exit(23)
"""
    result = subprocess.run([sys.executable, "-c", script, str(path), str(effect), crash_at], timeout=15)
    assert result.returncode == 23
    recovered = OperationRecoveryJournal()
    recovered.configure(path, "parent")
    if crash_at == "after_effect":
        recovered.compact()
    operation = recovered.list_operations()["items"][0]
    assert operation["state"] == ("failed" if crash_at == "prepared" else "uncertain")
    assert operation["native"]["dispatch_stage"] == ("prepared" if crash_at == "prepared" else "may_dispatch")
    recovered.configure(path, "another-restart")
    assert recovered.summary()["uncertain_count"] == (0 if crash_at == "prepared" else 1)
    assert (effect.read_text(encoding="utf-8") if effect.exists() else "") == ("1" if crash_at == "after_effect" else "")


def test_native_uia_begin_only_crash_is_not_dispatched_instead_of_uncertain(tmp_path: Path) -> None:
    path = tmp_path / "native.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "before")
    handle = journal.begin("ui_set_value", native_uia_protocol=True)
    assert handle is not None

    recovered = OperationRecoveryJournal()
    recovered.configure(path, "after")

    operation = recovered.inspect(handle.operation_id)
    assert operation["state"] == "failed"
    assert operation["target"] is None
    assert recovered.summary()["uncertain_count"] == 0
    assert recovered.summary()["pending_count"] == 0


def test_wrapped_native_uia_crash_before_checkpoint_recovers_as_failed(tmp_path: Path) -> None:
    path = tmp_path / "wrapped-native.jsonl"
    script = """
import os,sys
from pathlib import Path
from core import recovery
from core.tooling import compact_errors
recovery.OPERATION_RECOVERY.configure(Path(sys.argv[1]),'child')
@compact_errors('ui_set_value')
def crash_before_native_checkpoint():
    os._exit(23)
crash_before_native_checkpoint()
"""
    result = subprocess.run([sys.executable, "-c", script, str(path)], timeout=15)
    assert result.returncode == 23

    recovered = OperationRecoveryJournal()
    recovered.configure(path, "after")

    operation = recovered.list_operations()["items"][0]
    assert operation["state"] == "failed"
    assert operation["target"] is None
    assert recovered.summary()["uncertain_count"] == 0
    records = _records(path)
    native_recovery = records[0].get("native_recovery")
    assert isinstance(native_recovery, dict)
    assert native_recovery["dispatch_rule"] == "may_dispatch_checkpoint_before_effect"
    assert records[-1]["reason"] == "runtime_ended_before_native_uia_dispatch"


def test_legacy_native_uia_begin_without_dispatch_protocol_remains_uncertain(tmp_path: Path) -> None:
    path = tmp_path / "legacy-native.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "before")
    handle = journal.begin("ui_set_value")
    assert handle is not None

    recovered = OperationRecoveryJournal()
    recovered.configure(path, "after")

    assert recovered.inspect(handle.operation_id)["state"] == "uncertain"
    assert recovered.summary()["uncertain_count"] == 1


def test_previous_runtime_pending_operation_becomes_uncertain(tmp_path: Path) -> None:
    path = tmp_path / "operations.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "runtime-a")
    handle = journal.begin("safe_refactor", "path=C:/work/example.py")
    assert handle is not None

    journal.configure(path, "runtime-b")
    summary = journal.summary()
    assert summary["uncertain_count"] == 1
    assert summary["pending_count"] == 0
    assert summary["uncertain"][0]["operation_id"] == handle.operation_id
    assert summary["uncertain"][0]["status"] == "uncertain"
    assert summary["uncertain"][0]["operation_type"] == "safe_refactor"
    assert summary["uncertain"][0]["target"] == "path=C:/work/example.py"

    records = _records(path)
    assert [record["type"] for record in records] == ["begin", "uncertain"]
    assert all(record["schema_version"] == JOURNAL_SCHEMA_VERSION for record in records)


def test_repeated_runtime_recovery_marks_pending_operation_uncertain_once(tmp_path: Path) -> None:
    path = tmp_path / "operations.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "runtime-a")
    handle = journal.begin("write_file", "path=C:/work/restart-safe.txt")
    assert handle is not None

    journal.configure(path, "runtime-b")
    first_records = _records(path)
    first_markers = [
        record for record in first_records
        if record.get("type") == "uncertain" and record.get("operation_id") == handle.operation_id
    ]
    assert len(first_markers) == 1
    assert first_markers[0]["marked_by_runtime_id"] == "runtime-b"

    journal.configure(path, "runtime-c")
    journal.configure(path, "runtime-d")
    records = _records(path)
    markers = [
        record for record in records
        if record.get("type") == "uncertain" and record.get("operation_id") == handle.operation_id
    ]
    assert len(markers) == 1
    assert markers[0] == first_markers[0]
    summary = journal.summary()
    assert summary["uncertain_count"] == 1
    assert summary["pending_count"] == 0
    assert summary["uncertain"][0]["operation_id"] == handle.operation_id


def test_known_result_is_never_reclassified_uncertain(tmp_path: Path) -> None:
    path = tmp_path / "operations.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "runtime-a")
    handle = journal.begin("write_file", "path=C:/work/example.txt")
    journal.finish(handle, known_result="ok")

    journal.configure(path, "runtime-b")
    summary = journal.summary()
    assert summary["uncertain_count"] == 0
    assert summary["pending_count"] == 0
    assert [record["type"] for record in _records(path)] == ["begin", "result"]


def test_known_result_persists_bounded_file_postcondition(tmp_path: Path) -> None:
    path = tmp_path / "operations.jsonl"
    target = tmp_path / "document.xlsx"
    journal = OperationRecoveryJournal()
    journal.configure(path, "runtime-a")
    handle = journal.begin("excel_write_range", f"path={target}")
    assert handle is not None
    postcondition = {
        "kind": "file_sha256",
        "expected": {"path": str(target), "sha256": "a" * 64},
    }

    journal.finish_returned(handle, {"ok": True, "postcondition": postcondition})

    records = _records(path)
    assert records[-1]["postcondition"] == postcondition
    inspected = journal.inspect(handle.operation_id)
    assert inspected["postcondition"] == postcondition


def test_truncated_final_record_is_ignored_and_pending_begin_recovers(tmp_path: Path) -> None:
    path = tmp_path / "operations.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "runtime-a")
    handle = journal.begin("run_process", "cwd=C:/work")
    assert handle is not None
    with path.open("ab") as stream:
        stream.write(b'{"schema_version":1,"type":"result"')
        stream.flush()
        os.fsync(stream.fileno())

    journal.configure(path, "runtime-b")
    summary = journal.summary()
    assert summary["uncertain_count"] == 1
    assert summary["uncertain"][0]["operation_id"] == handle.operation_id


def test_begin_fails_closed_when_durable_journal_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = OperationRecoveryJournal()
    journal.configure(tmp_path / "operations.jsonl", "runtime-a")

    def fail_fsync(fd: int) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr(recovery.os, "fsync", fail_fsync)
    with pytest.raises(ToolError) as error:
        journal.begin("write_file", "path=C:/work/example.txt")
    assert error.value.code == "operation_journal_unavailable"


def test_completed_history_compacts_but_uncertain_operations_survive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recovery, "COMPACT_AFTER_RECORDS", 4)
    path = tmp_path / "operations.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "runtime-a")

    first = journal.begin("write_file", "path=C:/work/first.txt")
    journal.finish(first, known_result="ok")
    second = journal.begin("write_file", "path=C:/work/second.txt")
    journal.finish(second, known_result="ok")
    assert _records(path) == []

    pending = journal.begin("safe_refactor", "path=C:/work/pending.py")
    assert pending is not None
    journal.configure(path, "runtime-b")
    assert journal.summary()["uncertain_count"] == 1

    completed = journal.begin("write_file", "path=C:/work/third.txt")
    journal.finish(completed, known_result="ok")
    records = _records(path)
    assert [record["type"] for record in records] == ["begin", "uncertain"]
    summary = journal.summary()
    assert summary["uncertain_count"] == 1
    assert summary["pending_count"] == 0
    assert summary["uncertain"][0]["operation_id"] == pending.operation_id


def test_acknowledge_response_keeps_evidence_when_append_triggers_compaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recovery, "COMPACT_AFTER_RECORDS", 4)
    path = tmp_path / "operations.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "runtime-a")
    first = journal.begin("write_file", "path=C:/work/first.txt")
    assert first is not None

    journal.configure(path, "runtime-b")
    second = journal.begin("write_file", "path=C:/work/second.txt")
    assert second is not None
    evidence = Evidence("operator", {"confirmed": True})

    acknowledged = journal.acknowledge(
        first.operation_id,
        OperationState.ACKNOWLEDGED,
        evidence,
    )

    assert acknowledged["state"] == "acknowledged"
    assert acknowledged["evidence"] == [{"source": "operator", "data": {"confirmed": True}}]
    assert acknowledged == journal.inspect(first.operation_id)


def test_cross_process_compaction_preserves_all_pending_operations(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    path = tmp_path / "operations.jsonl"
    go = tmp_path / "go"
    helper = tmp_path / "recovery_compaction_child.py"
    helper.write_text(
        "\n".join(
            [
                "import sys, time",
                "from pathlib import Path",
                f"sys.path.insert(0, {str(repo)!r})",
                "import core.recovery as recovery",
                "recovery.COMPACT_AFTER_RECORDS = 50",
                "path = Path(sys.argv[1])",
                "go = Path(sys.argv[2])",
                "ready = Path(sys.argv[3])",
                "worker = sys.argv[4]",
                "journal = recovery.OperationRecoveryJournal()",
                "journal.configure(path, 'runtime-shared')",
                "ready.write_text('ready', encoding='utf-8')",
                "deadline = time.monotonic() + 10",
                "while not go.exists():",
                "    if time.monotonic() >= deadline: raise SystemExit(3)",
                "    time.sleep(0.002)",
                "for index in range(100):",
                "    journal.begin('write_file', f'{worker}-{index}')",
                "print('ok')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    processes: list[subprocess.Popen[str]] = []
    for index in range(4):
        ready = tmp_path / f"ready-{index}"
        processes.append(
            subprocess.Popen(
                [sys.executable, str(helper), str(path), str(go), str(ready), str(index)],
                cwd=repo,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
    deadline = time.monotonic() + 10
    while len(list(tmp_path.glob("ready-*"))) < len(processes) and time.monotonic() < deadline:
        time.sleep(0.002)
    assert len(list(tmp_path.glob("ready-*"))) == len(processes)
    go.write_text("go", encoding="utf-8")

    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, stderr
        assert stdout.strip() == "ok"

    journal = OperationRecoveryJournal()
    journal.configure(path, "runtime-shared")
    summary = journal.summary()

    assert summary["pending_count"] == 400
    assert summary["uncertain_count"] == 0
    records = _records(path)
    assert len(records) == 400
    assert all(record["type"] == "begin" for record in records)


def test_compaction_failure_never_turns_durable_result_into_retryable_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recovery, "COMPACT_AFTER_RECORDS", 2)
    path = tmp_path / "operations.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "runtime-a")
    handle = journal.begin("write_file", "path=C:/work/example.txt")

    def fail_replace(source: str | bytes | Path, destination: str | bytes | Path) -> None:
        raise OSError("sharing violation")

    monkeypatch.setattr(recovery.os, "replace", fail_replace)
    journal.finish(handle, known_result="ok")
    assert [record["type"] for record in _records(path)] == ["begin", "result"]
    assert journal.summary()["uncertain_count"] == 0
    assert journal.summary()["pending_count"] == 0


def test_mutating_mcp_tool_writes_metadata_only_begin_and_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_RUNTIME_GENERATION_ID", "runtime-mcp")
    monkeypatch.setenv("MCP_LIFECYCLE_CONTROL_FILE", str(tmp_path / "control.json"))
    monkeypatch.setenv("MCP_LIFECYCLE_STATUS_FILE", str(tmp_path / "status.json"))
    monkeypatch.setattr(heartbeat, "SETTINGS", replace(heartbeat.SETTINGS, state_dir=tmp_path))
    target = tmp_path / "secret.txt"
    secret = "do-not-journal-this-payload"

    async def scenario() -> None:
        async with Client(create_server()) as client:
            result = await client.call_tool(
                "create_file",
                {"path": str(target), "content": secret},
            )
            assert result.structured_content and result.structured_content["ok"] is True

    asyncio.run(scenario())
    journal_path = tmp_path / "operation-recovery.jsonl"
    text = journal_path.read_text(encoding="utf-8")
    assert secret not in text
    records = _records(journal_path)
    assert [record["type"] for record in records] == ["begin", "result"]
    begin = records[0]
    assert begin["operation_type"] == "create_file"
    assert str(target) in str(begin["target"])
    assert records[1]["known_result"] == "ok"


def test_safe_refactor_crash_after_write_is_recovered_as_uncertain_without_replay(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    target.write_text("value = 1\n", encoding="utf-8")
    helper = tmp_path / "safe_refactor_crash_helper.py"
    helper.write_text(
        f"""
import asyncio
import os
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
from mcp import Client
from core import config

work = Path({str(tmp_path)!r})
config.SETTINGS = replace(config.SETTINGS, state_dir=work)
from core import heartbeat
from core.registry import create_server

heartbeat.SETTINGS = config.SETTINGS
os.environ["MCP_RUNTIME_GENERATION_ID"] = "runtime-safe-refactor-a"
os.environ["MCP_LIFECYCLE_CONTROL_FILE"] = str(work / "control.json")
os.environ["MCP_LIFECYCLE_STATUS_FILE"] = str(work / "status.json")


async def main():
    async with Client(create_server()) as client:
        await client.call_tool(
            "safe_refactor",
            {{
                "path": {str(target)!r},
                "edits": [{{"mode": "exact", "old": "value = 1\\n", "new": "value = 2\\n"}}],
                "validation_command": [sys.executable, "-c", "import time; time.sleep(60)"],
                "validation_cwd": str(work),
                "timeout_sec": 120,
            }},
        )


asyncio.run(main())
""",
        encoding="utf-8",
    )
    process = spawn_owned_process(
        [sys.executable, str(helper)],
        cwd=Path(__file__).resolve().parents[1],
        env=None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        require_native=True,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                current = target.read_text(encoding="utf-8")
            except OSError:
                # safe_refactor rewrites the file atomically; on Windows a reader can
                # observe a transient sharing violation during the replace window.
                current = None
            if current == "value = 2\n":
                break
            if process.poll() is not None:
                stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
                raise AssertionError(f"helper exited before write: {process.returncode} {stderr}")
            time.sleep(0.02)
        else:
            raise AssertionError("safe_refactor did not reach the post-write validation window")
        process.terminate_tree(force=True)
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.terminate_tree(force=True)
            process.wait(timeout=5)
        process.close_ownership()
        if process.stderr is not None:
            process.stderr.close()

    journal = OperationRecoveryJournal()
    journal.configure(tmp_path / "operation-recovery.jsonl", "runtime-safe-refactor-b")
    summary = journal.summary()
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert summary["uncertain_count"] == 1
    assert summary["pending_count"] == 0
    assert summary["uncertain"][0]["operation_type"] == "safe_refactor"
    assert str(target) in str(summary["uncertain"][0]["target"])
    from core.resource_claims import make_resource_capability, path_claim, validate_resource_capability
    from core.workflow_store import WorkflowStore

    resources = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    import sqlite3

    with sqlite3.connect(resources.path) as db:
        request_id, owner = db.execute("SELECT request_id,owner_token FROM workflow_resource_requests").fetchone()
    with pytest.raises(ToolError) as denied:
        validate_resource_capability(make_resource_capability(request_id, owner, resources.path), (), resources)
    assert denied.value.code == "resource_capability_invalid"
    resources.recover_interrupted()
    assert resources.resource_request(request_id)["state"] == "quarantined"
    assert resources.reserve_resource_claims(
        request_id="conflict", owner_token="other", category="test", claims=(path_claim(target, "read"),),
    )["admitted"] is False


def test_server_health_surfaces_recovered_uncertain_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal_path = tmp_path / "operation-recovery.jsonl"
    previous = OperationRecoveryJournal()
    previous.configure(journal_path, "runtime-old")
    handle = previous.begin("run_cmd", "cwd=C:/work")
    assert handle is not None

    monkeypatch.setenv("MCP_RUNTIME_GENERATION_ID", "runtime-new")
    monkeypatch.setenv("MCP_LIFECYCLE_CONTROL_FILE", str(tmp_path / "control.json"))
    monkeypatch.setenv("MCP_LIFECYCLE_STATUS_FILE", str(tmp_path / "status.json"))
    monkeypatch.setattr(heartbeat, "SETTINGS", replace(heartbeat.SETTINGS, state_dir=tmp_path))

    async def scenario() -> None:
        async with Client(create_server()) as client:
            health = await client.call_tool("server_health", {})
            assert health.structured_content
            summary = health.structured_content["operation_recovery"]
            assert summary["enabled"] is True
            assert summary["uncertain_count"] == 1
            assert summary["uncertain"][0]["operation_id"] == handle.operation_id
            assert health.structured_content["health_status"] == "degraded"
            assert "operation_recovery_uncertain" in health.structured_content["degraded_reasons"]

    asyncio.run(scenario())



def test_uncertain_operation_retains_checkpointed_file_postcondition(tmp_path: Path) -> None:
    path = tmp_path / "operations.jsonl"
    target = tmp_path / "document.xlsx"
    journal = OperationRecoveryJournal()
    journal.configure(path, "runtime-a")
    handle = journal.begin("excel_write_range", f"path={target}")
    assert handle is not None
    postcondition = {
        "kind": "file_sha256",
        "expected": {"path": str(target), "sha256": "b" * 64},
    }

    journal.checkpoint_postcondition(handle, postcondition)
    journal.configure(path, "runtime-b")

    inspected = journal.inspect(handle.operation_id)
    assert inspected["state"] == "uncertain"
    assert inspected["postcondition"] == postcondition
    assert [record["type"] for record in _records(path)] == ["begin", "checkpoint", "uncertain"]
