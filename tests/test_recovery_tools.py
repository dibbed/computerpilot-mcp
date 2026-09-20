from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import psutil
import pytest
from mcp import Client

from core import heartbeat
from core.reconcilers import evaluate_postcondition
from core.recovery_models import Postcondition
from core.registry import create_server


def test_file_postconditions_are_conclusive(tmp_path: Path) -> None:
    target = tmp_path / "value.txt"
    target.write_text("hello", encoding="utf-8")
    digest = hashlib.sha256(b"hello").hexdigest()

    exists = evaluate_postcondition(Postcondition("file_exists", {"path": str(target)}))
    absent = evaluate_postcondition(Postcondition("file_absent", {"path": str(target)}))
    hashed = evaluate_postcondition(Postcondition("file_sha256", {"path": str(target), "sha256": digest}))

    assert exists.data["conclusive"] is True and exists.data["satisfied"] is True
    assert absent.data["conclusive"] is True and absent.data["satisfied"] is False
    assert hashed.data["conclusive"] is True and hashed.data["satisfied"] is True


def test_git_head_postcondition(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    head = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()

    evidence = evaluate_postcondition(Postcondition("git_head", {"repo": str(tmp_path), "commit": head}))
    assert evidence.data["conclusive"] is True
    assert evidence.data["satisfied"] is True


def test_process_identity_rejects_pid_reuse_metadata() -> None:
    process = psutil.Process(os.getpid())
    evidence = evaluate_postcondition(
        Postcondition(
            "process_identity",
            {"pid": process.pid, "create_time": process.create_time() - 100, "executable": process.exe()},
        )
    )
    assert evidence.data["conclusive"] is True
    assert evidence.data["satisfied"] is False
    assert evidence.data["pid_exists"] is True


def test_unknown_postcondition_is_rejected() -> None:
    with pytest.raises(Exception, match="postcondition"):
        evaluate_postcondition(Postcondition("unknown", {}))


def _seed_uncertain(path: Path) -> str:
    operation_id = "1" * 32
    records = [
        {
            "schema_version": 1,
            "type": "begin",
            "status": "in_progress",
            "operation_id": operation_id,
            "runtime_id": "old",
            "operation_type": "delete_file",
            "target": "path=C:/gone.txt",
            "started_at": "2026-09-20T00:00:00+00:00",
            "pid": 1,
        },
        {
            "schema_version": 1,
            "type": "uncertain",
            "status": "uncertain",
            "operation_id": operation_id,
            "runtime_id": "old",
            "marked_by_runtime_id": "current",
            "marked_at": "2026-09-20T00:01:00+00:00",
            "reason": "runtime_ended_without_known_result",
        },
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return operation_id


def test_recovery_mcp_tools_list_reconcile_and_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    operation_id = _seed_uncertain(tmp_path / "operation-recovery.jsonl")
    monkeypatch.setenv("MCP_RUNTIME_GENERATION_ID", "current")
    monkeypatch.setenv("MCP_LIFECYCLE_CONTROL_FILE", str(tmp_path / "control.json"))
    monkeypatch.setenv("MCP_LIFECYCLE_STATUS_FILE", str(tmp_path / "status.json"))
    monkeypatch.setattr(heartbeat, "SETTINGS", replace(heartbeat.SETTINGS, state_dir=tmp_path))
    async def scenario() -> None:
        async with Client(create_server()) as client:
            listed = await client.call_tool("list_uncertain_operations", {"offset": 0, "limit": 5})
            assert listed.structured_content and listed.structured_content["total_count"] == 1
            reconciled = await client.call_tool(
                "reconcile_operation",
                {
                    "operation_id": operation_id,
                    "kind": "file_absent",
                    "expected": {"path": str(tmp_path / "gone.txt")},
                },
            )
            assert reconciled.structured_content and reconciled.structured_content["state"] == "succeeded"
            history = await client.call_tool("get_operation_history", {"operation_id": operation_id})
            assert history.structured_content and history.structured_content["total_count"] == 3

    asyncio.run(scenario())
