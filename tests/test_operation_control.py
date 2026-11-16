"""Explicit operation identity and cancellation for stdio and HTTP callers."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import anyio
import psutil
import pytest
from mcp import Client

from core import config
from core.errors import ToolError
from core.operation_control import OPERATIONS, OperationRegistry, run_cancellable_operation
from core.registry import create_server


def _tool_payload(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    return {}


def _token_processes(token: str) -> list[int]:
    rows: list[int] = []
    for process in psutil.process_iter(attrs=["pid", "cmdline"]):
        try:
            cmdline = process.info["cmdline"] or []
        except psutil.Error:
            continue
        if any(token in str(part) for part in cmdline):
            rows.append(int(process.info["pid"]))
    return rows


def _wait_until_gone(token: str, timeout: float = 15.0) -> list[int]:
    deadline = time.monotonic() + timeout
    remaining = _token_processes(token)
    while remaining and time.monotonic() < deadline:
        time.sleep(0.1)
        remaining = _token_processes(token)
    return remaining


def test_registry_rejects_duplicate_and_reports_terminal_state() -> None:
    registry = OperationRegistry()
    handle = registry.reserve("registry-operation-01", "run_shell")
    with pytest.raises(ToolError) as raised:
        registry.reserve("registry-operation-01", "run_shell")
    assert raised.value.code == "operation_id_in_use"
    first = registry.request_cancel(handle.operation_id)
    assert first["state"] == "cancelling" and first["cancel_requested"] is True
    assert handle.cancel_event.is_set()
    assert registry.request_cancel(handle.operation_id)["already_requested"] is True
    registry.finish(handle, "cancelled")
    finished = registry.request_cancel(handle.operation_id)
    assert finished["ok"] is True and finished["cancelled"] is False
    assert finished["state"] == "cancelled"
    assert registry.request_cancel("registry-unknown-01")["reason"] == "operation_not_found"
    with pytest.raises(ToolError):
        registry.reserve("short", "run_shell")


def test_cancel_identifiers_are_validated_process_local_and_idempotent() -> None:
    from concurrent.futures import ThreadPoolExecutor

    registry = OperationRegistry()
    with pytest.raises(ToolError) as malformed:
        registry.request_cancel("short")
    assert malformed.value.code == "invalid_operation_id"
    assert registry.request_cancel("registry-unknown-02")["reason"] == "operation_not_found"

    handle = registry.reserve("registry-concurrent-01", "run_shell")
    with ThreadPoolExecutor(max_workers=8) as pool:
        replies = list(pool.map(lambda _: registry.request_cancel(handle.operation_id), range(8)))
    assert all(reply["cancelled"] is True for reply in replies)
    assert sum(reply['already_requested'] is False for reply in replies) == 1
    assert handle.cancel_event.is_set()

    registry.finish(handle, "cancelled")
    assert registry.request_cancel(handle.operation_id)["reason"] == "operation_finished"
    # A new runtime cannot resolve a capability minted by the previous one.
    assert OperationRegistry().request_cancel(handle.operation_id)["reason"] == "operation_not_found"


def test_cancel_audit_never_persists_bearer_operation_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import audit, tooling
    from core.recovery import OperationRecoveryJournal

    audit.close_audit_writer()
    monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, state_dir=tmp_path))
    monkeypatch.setattr(audit, "SETTINGS", replace(audit.SETTINGS, state_dir=tmp_path))
    operation_id = f"op-audit-secret-{uuid.uuid4().hex}"
    journal_path = tmp_path / "operation-recovery.jsonl"
    recovery = OperationRecoveryJournal()

    async def scenario() -> None:
        async with Client(create_server()) as client:
            recovery.configure(journal_path, "terminal-bearer-test")
            monkeypatch.setattr(tooling, "OPERATION_RECOVERY", recovery)
            command = _tool_payload(await client.call_tool("run_process", {
                "executable": sys.executable, "args": ["-c", "raise SystemExit(0)"],
                "cwd": str(tmp_path), "operation_id": operation_id,
            }))
            assert command["ok"] is True
            unknown_id = operation_id + "-unknown"
            first = _tool_payload(
                await client.call_tool("cancel_operation", {"operation_id": unknown_id})
            )
            second = _tool_payload(
                await client.call_tool("cancel_operation", {"operation_id": unknown_id})
            )
            assert first["ok"] is False and first["reason"] == "operation_not_found"
            assert second["ok"] is False and second["reason"] == "operation_not_found"

    try:
        asyncio.run(scenario())
        audit.flush_audit()
        path = tmp_path / "audit.jsonl"
        text = path.read_text(encoding="utf-8")
        assert operation_id not in text
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
        cancellations = [record for record in records if record.get("operation") == "cancel_operation"]
        assert len(cancellations) == 2
        targets = {record["target"] for record in cancellations}
        assert len(targets) == 1
        target = targets.pop()
        assert target.startswith("op-correlation:")
        assert operation_id not in target
        journal = journal_path.read_text(encoding="utf-8")
        assert "run_process" in journal and "cancel_operation" in journal
        assert operation_id not in journal
    finally:
        recovery.configure(None, None)
        audit.close_audit_writer()


def test_run_cancellable_operation_signals_worker_and_settles_state() -> None:
    observed = threading.Event()

    def action(cancel: threading.Event) -> dict[str, Any]:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if cancel.is_set():
                observed.set()
                raise ToolError("command_cancelled", "cancelled")
            time.sleep(0.02)
        raise AssertionError("worker never observed cancellation")

    async def scenario() -> None:
        task = asyncio.create_task(
            run_cancellable_operation("registry-worker-0001", "run_shell", action)
        )
        await asyncio.sleep(0.1)
        assert OPERATIONS.request_cancel("registry-worker-0001")["cancelled"] is True
        with pytest.raises(ToolError) as raised:
            await asyncio.wait_for(task, timeout=20)
        assert raised.value.code == "command_cancelled"

    asyncio.run(scenario())
    assert observed.is_set()
    assert OPERATIONS.request_cancel("registry-worker-0001")["state"] == "cancelled"


def test_lost_request_keeps_command_cancelling_until_worker_settles() -> None:
    operation_id = f"op-lost-{uuid.uuid4().hex}"
    entered = threading.Event()
    release = threading.Event()

    def action(cancel: threading.Event) -> dict[str, Any]:
        entered.set()
        assert cancel.wait(5)
        assert release.wait(5)
        raise ToolError("command_cancelled", "cancelled")

    async def scenario() -> None:
        task = asyncio.create_task(run_cancellable_operation(operation_id, "run_process", action))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert OPERATIONS.status(operation_id)["state"] == "cancelling"
        finally:
            release.set()
        with anyio.fail_after(5):
            while OPERATIONS.status(operation_id)["state"] == "cancelling":
                await asyncio.sleep(0.02)
        assert OPERATIONS.status(operation_id)["state"] == "cancelled"

    asyncio.run(scenario())


def test_explicit_cancel_reaps_owned_tree_and_releases_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = f"cpm-opcancel-{uuid.uuid4().hex[:10]}"
    script = f"import time; time.sleep(120)  # {token}"
    operation_id = f"opcancel-{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, state_dir=tmp_path))

    async def scenario() -> None:
        async with Client(create_server()) as client:
            call = asyncio.create_task(
                client.call_tool(
                    "run_process",
                    {
                        "executable": sys.executable,
                        "args": ["-c", script],
                        "cwd": str(tmp_path),
                        "timeout_sec": 300,
                        "operation_id": operation_id,
                    },
                )
            )
            deadline = time.monotonic() + 20
            while not _token_processes(token) and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert _token_processes(token), "owned command never started"

            running = _tool_payload(
                await client.call_tool("command_status", {"operation_id": operation_id})
            )
            assert running["state"] == "running"

            cancel = _tool_payload(
                await client.call_tool("cancel_operation", {"operation_id": operation_id})
            )
            assert cancel["ok"] is True and cancel["cancelled"] is True

            started = time.monotonic()
            outcome = _tool_payload(await asyncio.wait_for(call, timeout=25))
            assert time.monotonic() - started < 10
            assert outcome.get("error") == "command_cancelled"
            assert _wait_until_gone(token) == []
            settled = _tool_payload(
                await client.call_tool("command_status", {"operation_id": operation_id})
            )
            assert settled["state"] == "cancelled"

            follow = _tool_payload(
                await client.call_tool(
                    "run_process",
                    {
                        "executable": sys.executable,
                        "args": ["-c", "print('released')"],
                        "cwd": str(tmp_path),
                        "timeout_sec": 60,
                    },
                )
            )
            assert follow.get("ok") is True
            assert follow["stdout"].get("text", "").strip() == "released"

    asyncio.run(scenario())


def test_failed_command_exit_is_recoverable_by_client_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, state_dir=tmp_path))
    operation_id = f"op-exit-{uuid.uuid4().hex}"

    async def scenario() -> None:
        async with Client(create_server()) as client:
            result = _tool_payload(await client.call_tool("run_process", {
                "executable": sys.executable, "args": ["-c", "print('private output'); raise SystemExit(7)"],
                "cwd": str(tmp_path), "operation_id": operation_id,
            }))
            assert result["exit_code"] == 7 and result["ok"] is False
            status = _tool_payload(await client.call_tool("command_status", {"operation_id": operation_id}))
            assert status["state"] == "failed" and status["exit_code"] == 7
            assert "private output" not in json.dumps(status)

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group ownership probe")
def test_duplicate_operation_id_is_rejected_while_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = f"cpm-opdup-{uuid.uuid4().hex[:10]}"
    script = f"{sys.executable} -c 'import time; time.sleep(60)' # {token}"
    operation_id = f"opdup-{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, state_dir=tmp_path))

    async def scenario() -> None:
        async with Client(create_server()) as client:
            first = asyncio.create_task(
                client.call_tool(
                    "run_shell",
                    {
                        "command_text": script,
                        "cwd": str(tmp_path),
                        "timeout_sec": 120,
                        "operation_id": operation_id,
                    },
                )
            )
            deadline = time.monotonic() + 20
            while not _token_processes(token) and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            duplicate = _tool_payload(
                await client.call_tool(
                    "run_shell",
                    {"command_text": "echo should-not-run", "operation_id": operation_id},
                )
            )
            assert duplicate.get("error") == "operation_id_in_use"
            await client.call_tool("cancel_operation", {"operation_id": operation_id})
            await asyncio.wait_for(first, timeout=25)
            assert _wait_until_gone(token) == []

    asyncio.run(scenario())
