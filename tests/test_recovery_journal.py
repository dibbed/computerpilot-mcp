from __future__ import annotations

import asyncio
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
from core.recovery import JOURNAL_SCHEMA_VERSION, OperationRecoveryJournal
from core.registry import create_server


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


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
from core import heartbeat
from core.registry import create_server

work = Path({str(tmp_path)!r})
heartbeat.SETTINGS = replace(heartbeat.SETTINGS, state_dir=work)
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
    process = subprocess.Popen(
        [sys.executable, str(helper)],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if target.read_text(encoding="utf-8") == "value = 2\n":
                break
            if process.poll() is not None:
                stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
                raise AssertionError(f"helper exited before write: {process.returncode} {stderr}")
            time.sleep(0.02)
        else:
            raise AssertionError("safe_refactor did not reach the post-write validation window")
        process.kill()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
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

    asyncio.run(scenario())
