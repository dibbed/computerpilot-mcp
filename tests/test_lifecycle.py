from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import pytest
from mcp import Client

from core import lifecycle as lifecycle_module
from core.errors import ToolError
from core.lifecycle import CONTROL_SCHEMA_VERSION, RuntimeLifecycle, lifecycle_control_request
from core.registry import create_server
from core.tooling import MUTATING_TOOL_OPERATIONS


def _write_control(path: Path, command: str, request_id: str, deadline: float) -> None:
    payload = lifecycle_control_request(command, request_id, deadline)  # type: ignore[arg-type]
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_mutation_guard_set_matches_mcp_annotations() -> None:
    tools = asyncio.run(create_server().list_tools())
    names = {tool.name for tool in tools}
    annotated_mutations = {
        tool.name
        for tool in tools
        if tool.annotations is not None and tool.annotations.read_only_hint is False
    }
    assert annotated_mutations == MUTATING_TOOL_OPERATIONS & names


def test_runtime_lifecycle_drains_active_mutation_and_rejects_new_work(tmp_path: Path) -> None:
    control = tmp_path / "control.json"
    status = tmp_path / "status.json"
    lifecycle = RuntimeLifecycle()
    lifecycle.configure(control, status)
    entered = threading.Event()
    release = threading.Event()

    def active_mutation() -> None:
        with lifecycle.mutation("safe_refactor"):
            entered.set()
            assert release.wait(timeout=5)

    thread = threading.Thread(target=active_mutation)
    thread.start()
    assert entered.wait(timeout=5)
    assert lifecycle.snapshot()["active_mutations"] == 1

    request_id = "drain-test"
    _write_control(control, "drain", request_id, time.time() + 5)
    assert lifecycle.poll_control() == "DRAINING"
    draining = lifecycle.snapshot()
    assert draining["request_id"] == request_id
    assert draining["active_mutations"] == 1
    with pytest.raises(ToolError) as error:
        with lifecycle.mutation("write_file"):
            raise AssertionError("draining mutation unexpectedly started")
    assert error.value.code == "runtime_draining"

    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert lifecycle.snapshot()["active_mutations"] == 0

    _write_control(control, "stop", request_id, time.time() + 5)
    assert lifecycle.poll_control() == "STOPPING"
    published = json.loads(status.read_text(encoding="utf-8"))
    assert published["schema_version"] == CONTROL_SCHEMA_VERSION
    assert published["state"] == "STOPPING"
    assert published["active_mutations"] == 0


def test_control_ack_retries_after_transient_status_publish_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = tmp_path / "control.json"
    status = tmp_path / "status.json"
    lifecycle = RuntimeLifecycle()
    lifecycle.configure(control, status)
    request_id = "retry-ack"
    _write_control(control, "drain", request_id, time.time() + 5)
    original_replace = lifecycle_module.os.replace
    calls = 0

    def flaky_replace(source: str | bytes | Path, destination: str | bytes | Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("transient sharing violation")
        original_replace(source, destination)

    monkeypatch.setattr(lifecycle_module.os, "replace", flaky_replace)
    assert lifecycle.poll_control() == "DRAINING"
    first = json.loads(status.read_text(encoding="utf-8"))
    assert first["request_id"] is None
    assert lifecycle.poll_control() == "DRAINING"
    second = json.loads(status.read_text(encoding="utf-8"))
    assert second["request_id"] == request_id
    assert second["state"] == "DRAINING"


def test_unmanaged_lifecycle_preserves_standalone_behavior() -> None:
    lifecycle = RuntimeLifecycle()
    lifecycle.configure(None, None)
    lifecycle.mark_stopping()
    with lifecycle.mutation("write_file"):
        pass
    assert lifecycle.snapshot()["state"] == "RUNNING"
    assert lifecycle.snapshot()["active_mutations"] == 0


def test_mcp_drain_rejects_mutations_but_allows_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    control = tmp_path / "control.json"
    status = tmp_path / "status.json"
    existing = tmp_path / "existing.txt"
    existing.write_text("safe", encoding="utf-8")
    monkeypatch.setenv("MCP_LIFECYCLE_CONTROL_FILE", str(control))
    monkeypatch.setenv("MCP_LIFECYCLE_STATUS_FILE", str(status))

    async def scenario() -> None:
        async with Client(create_server()) as client:
            deadline = time.monotonic() + 3
            while not status.exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert status.exists()

            request_id = "mcp-drain"
            _write_control(control, "drain", request_id, time.time() + 3)
            while time.monotonic() < deadline:
                payload = json.loads(status.read_text(encoding="utf-8"))
                if payload.get("request_id") == request_id and payload.get("state") == "DRAINING":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError(status.read_text(encoding="utf-8"))

            rejected = await client.call_tool(
                "create_file",
                {"path": str(tmp_path / "must-not-exist.txt"), "content": "no"},
            )
            assert rejected.structured_content
            assert rejected.structured_content["ok"] is False
            assert rejected.structured_content["error"] == "runtime_draining"
            assert not (tmp_path / "must-not-exist.txt").exists()

            readable = await client.call_tool("get_file_info", {"path": str(existing)})
            assert readable.structured_content
            assert readable.structured_content["ok"] is True
            assert readable.structured_content["type"] == "file"

    asyncio.run(scenario())


def test_real_mutating_tool_stays_active_until_command_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = tmp_path / "control.json"
    status = tmp_path / "status.json"
    existing = tmp_path / "existing.txt"
    existing.write_text("safe", encoding="utf-8")
    monkeypatch.setenv("MCP_LIFECYCLE_CONTROL_FILE", str(control))
    monkeypatch.setenv("MCP_LIFECYCLE_STATUS_FILE", str(status))

    async def scenario() -> None:
        async with Client(create_server()) as client:
            running = asyncio.create_task(
                client.call_tool(
                    "run_process",
                    {
                        "executable": sys.executable,
                        "args": ["-c", "import time; time.sleep(0.35); print('done')"],
                        "cwd": str(tmp_path),
                        "timeout_sec": 5,
                    },
                )
            )
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if status.exists():
                    payload = json.loads(status.read_text(encoding="utf-8"))
                    if payload.get("active_mutations") == 1:
                        break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError(status.read_text(encoding="utf-8") if status.exists() else "missing status")

            request_id = "active-command-drain"
            _write_control(control, "drain", request_id, time.time() + 3)
            while time.monotonic() < deadline:
                payload = json.loads(status.read_text(encoding="utf-8"))
                if payload.get("request_id") == request_id and payload.get("state") == "DRAINING":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError(status.read_text(encoding="utf-8"))
            assert payload["active_mutations"] == 1

            rejected = await client.call_tool(
                "write_file",
                {"path": str(existing), "content": "unsafe", "backup": False},
            )
            assert rejected.structured_content
            assert rejected.structured_content["error"] == "runtime_draining"
            readable = await client.call_tool("read_file", {"path": str(existing)})
            assert readable.structured_content and readable.structured_content["content"] == "safe"

            completed = await running
            assert completed.structured_content
            assert completed.structured_content["ok"] is True
            assert "done" in completed.structured_content["stdout"]["text"]
            while time.monotonic() < deadline:
                payload = json.loads(status.read_text(encoding="utf-8"))
                if payload.get("active_mutations") == 0:
                    break
                await asyncio.sleep(0.01)
            assert payload["state"] == "DRAINING"
            assert payload["active_mutations"] == 0

    asyncio.run(scenario())
