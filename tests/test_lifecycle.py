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


def test_direct_project_runner_denied_during_drain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.project.project_types import detect_project_types
    from tools.testing import project_runners, runners

    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    spec = project_runners.discover_test_runners(tmp_path, detect_project_types(tmp_path)).runners[0]
    bound = project_runners.bind_runner_runtime(spec, workspace_root=tmp_path)
    control = tmp_path / "control.json"
    lifecycle = RuntimeLifecycle()
    lifecycle.configure(control, tmp_path / "status.json")
    _write_control(control, "drain", "runner-drain", time.time() + 5)
    assert lifecycle.poll_control() == "DRAINING"
    monkeypatch.setattr(lifecycle_module, "RUNTIME_LIFECYCLE", lifecycle)
    monkeypatch.setattr(runners, "run_bounded", lambda *a, **k: pytest.fail("dispatched during drain"))
    with pytest.raises(ToolError) as error:
        runners.run_project_tests(bound, targets=[], deadline=time.monotonic() + 2, diagnostic_cap=10)
    assert error.value.code == "runtime_draining"


@pytest.mark.parametrize("enabled", [False, True])
def test_admitted_native_verification_reuses_during_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool,
) -> None:
    from core import recovery, tooling

    lifecycle = RuntimeLifecycle()
    control = tmp_path / "control.json"
    lifecycle.configure(control, tmp_path / "status.json")
    journal = recovery.OperationRecoveryJournal()
    path = tmp_path / "journal.jsonl"
    journal.configure(path if enabled else None, "fixture" if enabled else None)
    monkeypatch.setattr(lifecycle_module, "RUNTIME_LIFECYCLE", lifecycle)
    monkeypatch.setattr(tooling, "RUNTIME_LIFECYCLE", lifecycle)
    monkeypatch.setattr(recovery, "OPERATION_RECOVERY", journal)
    monkeypatch.setattr(tooling, "OPERATION_RECOVERY", journal)

    @tooling.compact_errors("verify_changes")
    async def execute() -> dict[str, bool]:
        _write_control(control, "drain", "admitted-verifier", time.time() + 5)
        assert lifecycle.poll_control() == "DRAINING"

        def work() -> dict[str, bool]:
            assert lifecycle.snapshot()["active_mutations"] == 1
            recovery.mark_native_test_dispatch()
            return {"ok": True}

        return await asyncio.to_thread(recovery.bind_operation_recovery(
            lambda: recovery.run_native_operation("verify_changes", None, work),
        ))

    assert asyncio.run(execute()) == {"ok": True}
    assert lifecycle.snapshot()["active_mutations"] == 0
    if enabled:
        records = [json.loads(line) for line in path.read_text().splitlines()]
        assert sum(row.get("type") == "begin" for row in records) == 1
        assert sum(row.get("type") == "result" for row in records) == 1


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


@pytest.mark.parametrize("transition", ["enter", "exit"])
@pytest.mark.parametrize("invalid_control", [None, {"command": "invalid", "request_id": "bad"}, {"command": "drain"}])
def test_mutation_status_retries_without_control_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transition: str, invalid_control: dict[str, str] | None,
) -> None:
    status = tmp_path / "status.json"
    control = tmp_path / "control.json"
    lifecycle = RuntimeLifecycle()
    lifecycle.configure(control, status)
    if invalid_control is not None:
        control.write_text(json.dumps({"schema_version": 1, **invalid_control}), encoding="utf-8")
    if transition == "exit":
        lifecycle.enter_mutation("run_process")
    original_replace = lifecycle_module.os.replace
    calls = 0

    def flaky_replace(source: str | bytes | Path, destination: str | bytes | Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("transient sharing violation")
        original_replace(source, destination)

    monkeypatch.setattr(lifecycle_module.os, "replace", flaky_replace)
    if transition == "enter":
        lifecycle.enter_mutation("run_process")
    else:
        lifecycle.exit_mutation()
    for _ in range(2):
        assert lifecycle.poll_control() == "RUNNING"
    payload = json.loads(status.read_text(encoding="utf-8"))
    assert payload["active_mutations"] == (1 if transition == "enter" else 0)
    assert payload["active_mutations"] == lifecycle.snapshot()["active_mutations"]
    assert calls == 2  # A successful retry stops further publication without new state.


def test_unmanaged_server_does_not_wake_for_supervisor_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    from core import heartbeat

    monkeypatch.delenv("MCP_LIFECYCLE_CONTROL_FILE", raising=False)
    monkeypatch.delenv("MCP_LIFECYCLE_STATUS_FILE", raising=False)
    monkeypatch.setattr(heartbeat, "SETTINGS", replace(heartbeat.SETTINGS, state_dir=tmp_path))
    for name in (
        "schedule_artifact_retention", "schedule_job_history_retention",
        "schedule_workflow_history_retention", "flush_audit",
    ):
        monkeypatch.setattr(heartbeat, name, lambda **kwargs: None)
    monkeypatch.setattr(heartbeat.BACKUP_RETENTION, "schedule", lambda: None)
    polls = 0

    def count_poll() -> str:
        nonlocal polls
        polls += 1
        return "RUNNING"

    monkeypatch.setattr(heartbeat.RUNTIME_LIFECYCLE, "poll_control", count_poll)

    async def scenario() -> None:
        async with heartbeat.lifespan(None):
            await asyncio.sleep(0.13)

    asyncio.run(scenario())
    assert polls == 0


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


@pytest.mark.parametrize("observer_delay", [0.01, 0.8])
def test_real_mutating_tool_stays_active_until_command_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observer_delay: float,
) -> None:
    control = tmp_path / "control.json"
    status = tmp_path / "status.json"
    release = tmp_path / "command-release"
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
                        "args": ["-c", (
                            "import sys, time\n"
                            "from pathlib import Path\n"
                            "release = Path(sys.argv[1])\n"
                            "deadline = time.monotonic() + 15\n"
                            "while not release.exists():\n"
                            "    if time.monotonic() >= deadline: raise TimeoutError('release marker timeout')\n"
                            "    time.sleep(0.01)\n"
                            "print('done')\n"
                        ), str(release)],
                        "cwd": str(tmp_path),
                        "timeout_sec": 20,
                    },
                )
            )
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if status.exists():
                        payload = json.loads(status.read_text(encoding="utf-8"))
                        if payload.get("active_mutations") == 1:
                            break
                    await asyncio.sleep(observer_delay)
                else:
                    raise AssertionError(status.read_text(encoding="utf-8") if status.exists() else "missing status")

                request_id = "active-command-drain"
                _write_control(control, "drain", request_id, time.time() + 10)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    payload = json.loads(status.read_text(encoding="utf-8"))
                    if payload.get("request_id") == request_id and payload.get("state") == "DRAINING":
                        break
                    await asyncio.sleep(observer_delay)
                else:
                    raise AssertionError(status.read_text(encoding="utf-8"))
                assert payload["active_mutations"] == 1
                assert not running.done()

                rejected = await client.call_tool(
                    "write_file",
                    {"path": str(existing), "content": "unsafe", "backup": False},
                )
                assert rejected.structured_content
                assert rejected.structured_content["error"] == "runtime_draining"
                readable = await client.call_tool("read_file", {"path": str(existing)})
                assert readable.structured_content and readable.structured_content["content"] == "safe"
            finally:
                # Keep the real process active until the observer finishes its drain checks.
                release.write_text("go", encoding="ascii")
                completed = await running

            assert completed.structured_content
            assert completed.structured_content["ok"] is True
            assert "done" in completed.structured_content["stdout"]["text"]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                payload = json.loads(status.read_text(encoding="utf-8"))
                if payload.get("active_mutations") == 0:
                    break
                await asyncio.sleep(0.01)
            assert payload["state"] == "DRAINING"
            assert payload["active_mutations"] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_count,seal_error", [(0, False), (1, False), (2, False), (0, True)])
def test_lsp_shutdown_survives_extension_error_and_parent_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_count: int, seal_error: bool,
) -> None:
    from dataclasses import replace
    from types import SimpleNamespace

    from core import audit, config, heartbeat

    settings = replace(config.SETTINGS, state_dir=tmp_path / "state", memory_dir=tmp_path / "memory", extensions_enabled=False)
    for module in (audit, config, heartbeat):
        monkeypatch.setattr(module, "SETTINGS", settings)
    for name in ("MCP_HEARTBEAT_FILE", "MCP_RUNTIME_HEALTH_FILE", "MCP_LIFECYCLE_CONTROL_FILE",
                 "MCP_LIFECYCLE_STATUS_FILE", "MCP_TOOL_ACTIVITY_FILE", "MCP_RUNTIME_GENERATION_ID"):
        monkeypatch.delenv(name, raising=False)
    for name in ("schedule_artifact_retention", "schedule_job_history_retention", "schedule_workflow_history_retention"):
        monkeypatch.setattr(heartbeat, name, lambda **_kwargs: None)
    monkeypatch.setattr(heartbeat.BACKUP_RETENTION, "schedule", lambda: None)
    monkeypatch.setattr(heartbeat.SEARCH_SNAPSHOTS, "cleanup", lambda: None)
    closing, release, closed = threading.Event(), threading.Event(), threading.Event()
    calls: list[float] = []
    drained: list[bool] = []

    def close_all(*, timeout_sec: float) -> None:
        calls.append(timeout_sec)
        if not timeout_sec and seal_error:
            raise RuntimeError("fixture initial seal failure")
        if timeout_sec:
            closing.set()
            assert release.wait(5)
            closed.set()

    class Extension:
        async def start_all_async(self) -> None:
            return None

        async def drain_and_stop(self, *, timeout_sec: float) -> None:
            drained.append(True)
            raise RuntimeError("fixture extension drain failure")

    server = SimpleNamespace(lsp_manager=SimpleNamespace(close_all=close_all), extension_manager=Extension())

    async def run_lifespan() -> None:
        async with heartbeat.lifespan(server):
            pass

    async def scenario() -> None:
        task = asyncio.create_task(run_lifespan())
        try:
            assert await asyncio.to_thread(closing.wait, 5)
            for _ in range(cancel_count):
                task.cancel()
                await asyncio.sleep(0)
                await asyncio.sleep(0)
            assert not task.done()
            release.set()
            if cancel_count:
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                await task
            assert closed.is_set() and drained == [True]
            assert calls == [0, 5.0]
            assert server.runtime_started is False
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
