from __future__ import annotations

import asyncio
import json
import os
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
