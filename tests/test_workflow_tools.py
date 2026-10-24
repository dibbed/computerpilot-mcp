from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from mcp import Client
from mcp.server import MCPServer

from core import heartbeat
from core.registry import create_server
from tools.workflows import registry as workflow_registry


def test_cancelled_workflow_keeps_test_worker_admitted_until_settled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import lifecycle, recovery, tooling

    runtime = lifecycle.RuntimeLifecycle()
    runtime.configure(tmp_path / "control.json", tmp_path / "status.json")
    journal = recovery.OperationRecoveryJournal()
    journal_path = tmp_path / "recovery.jsonl"
    journal.configure(journal_path, "cancelled-workflow")
    monkeypatch.setattr(lifecycle, "RUNTIME_LIFECYCLE", runtime)
    monkeypatch.setattr(tooling, "RUNTIME_LIFECYCLE", runtime)
    monkeypatch.setattr(recovery, "OPERATION_RECOVERY", journal)
    monkeypatch.setattr(tooling, "OPERATION_RECOVERY", journal)
    entered, release = threading.Event(), threading.Event()
    effects: list[str] = []

    class Store:
        def get(self, workflow_id: str) -> dict[str, int]:
            return {"version": 1}

    class Executor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def execute(self, *args: object, **kwargs: object) -> dict[str, str]:
            recovery.mark_native_test_dispatch()
            entered.set()
            assert release.wait(5)
            effects.append("test")
            return {"state": "completed"}

    monkeypatch.setattr(workflow_registry, "workflow_store", lambda path: Store())
    monkeypatch.setattr(workflow_registry, "WorkflowExecutor", Executor)
    monkeypatch.setattr(workflow_registry, "build_route_planner", lambda **kwargs: None)
    monkeypatch.setattr(workflow_registry, "detect_capabilities", lambda: None)
    monkeypatch.setattr(workflow_registry, "audit_action", lambda *args, **kwargs: None)
    server = MCPServer("cancelled-workflow")
    workflow_registry.register(server)
    tool = server._tool_manager.get_tool("workflow_execute")
    assert tool is not None

    async def scenario() -> None:
        task = asyncio.create_task(tool.fn(workflow_id="fixture", expected_version=1))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                assert runtime.snapshot()["active_mutations"] == 1
                records = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
                assert all(record["type"] != "result" for record in records)
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert effects == ["test"]
        assert runtime.snapshot()["active_mutations"] == 0
        assert len(journal.list_operations(state="uncertain")["items"]) == 1

    asyncio.run(scenario())


def test_workflow_lifecycle_tools_persist_and_cancel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = replace(workflow_registry.SETTINGS, state_dir=tmp_path)
    monkeypatch.setattr(workflow_registry, "SETTINGS", settings)
    monkeypatch.setattr(heartbeat, "SETTINGS", replace(heartbeat.SETTINGS, state_dir=tmp_path))

    async def scenario() -> None:
        async with Client(create_server()) as client:
            definition = {
                "name": "verify",
                "steps": [{"name": "tests", "action": "verify_changes", "arguments": {"cwd": "C:/repo"}}],
            }
            planned = await client.call_tool("workflow_plan", {"definition": definition})
            assert planned.structured_content and planned.structured_content["ok"] is True
            started = await client.call_tool(
                "workflow_start", {"definition": definition, "inputs": {"api_token": "secret"}, "idempotency_key": "one"},
            )
            assert started.structured_content
            workflow_id = started.structured_content["workflow_id"]
            assert started.structured_content["inputs"]["api_token"] == "<redacted>"
            status = await client.call_tool("workflow_status", {"workflow_id": workflow_id})
            assert status.structured_content and status.structured_content["state"] == "queued"
            cancelled = await client.call_tool(
                "workflow_cancel", {"workflow_id": workflow_id, "expected_version": status.structured_content["version"]},
            )
            assert cancelled.structured_content and cancelled.structured_content["state"] == "cancelled"

    asyncio.run(scenario())


@pytest.mark.parametrize("enabled", [False, True])
def test_dag_public_execution_respects_operator_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    from core import workflow_executor

    settings = replace(workflow_registry.SETTINGS, state_dir=tmp_path, workflow_dag_enabled=enabled)
    monkeypatch.setattr(workflow_registry, "SETTINGS", settings)
    monkeypatch.setattr(workflow_executor, "SETTINGS", settings)
    monkeypatch.setattr(heartbeat, "SETTINGS", replace(heartbeat.SETTINGS, state_dir=tmp_path))
    target = tmp_path / "present.txt"
    target.write_text("ok", encoding="utf-8")

    async def scenario() -> None:
        async with Client(create_server()) as client:
            definition = {
                "name": "dag-inspect",
                "execution_mode": "dag",
                "steps": [
                    {
                        "step_id": "root",
                        "name": "file",
                        "action": "check_file",
                        "arguments": {"path": str(target)},
                        "depends_on": [],
                    }
                ],
            }
            planned = await client.call_tool("workflow_plan", {"definition": definition})
            assert planned.structured_content and planned.structured_content["ok"] is True
            started = await client.call_tool("workflow_start", {"definition": definition})
            assert started.structured_content and started.structured_content["execution_mode"] == "dag"
            workflow_id = started.structured_content["workflow_id"]
            assert started.structured_content["steps"][0]["readiness"] == "ready"
            dry = await client.call_tool(
                "workflow_execute",
                {
                    "workflow_id": workflow_id,
                    "expected_version": started.structured_content["version"],
                    "dry_run": True,
                },
            )
            assert dry.structured_content and dry.structured_content["dry_run"] is True
            executed = await client.call_tool(
                "workflow_execute",
                {"workflow_id": workflow_id, "expected_version": started.structured_content["version"]},
            )
            assert executed.structured_content
            if enabled:
                assert executed.structured_content["state"] == "completed", executed.structured_content
            else:
                assert executed.structured_content["ok"] is False
                assert executed.structured_content["error"] == "workflow_dag_execution_unavailable"
            status = await client.call_tool("workflow_status", {"workflow_id": workflow_id})
            assert status.structured_content and status.structured_content["state"] == ("completed" if enabled else "queued")

    asyncio.run(scenario())


def test_each_mcp_execution_attempt_has_a_distinct_lease_owner() -> None:
    first = workflow_registry._mcp_owner_id()
    second = workflow_registry._mcp_owner_id()

    assert first != second
    assert first.startswith("mcp-") and second.startswith("mcp-")


@pytest.mark.parametrize("mode", ["sequential", "dag"])
def test_admitted_workflow_transfers_verification_admission_during_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    from core import lifecycle, recovery, tooling, workflow_actions, workflow_executor
    from core.resource_claims import resource_requires_native_ownership

    settings = replace(workflow_registry.SETTINGS, state_dir=tmp_path, workflow_dag_enabled=mode == "dag")
    for module in (workflow_registry, workflow_actions, workflow_executor, heartbeat):
        monkeypatch.setattr(module, "SETTINGS", settings)
    runtime = lifecycle.RuntimeLifecycle()
    control = tmp_path / "control.json"
    runtime.configure(control, tmp_path / "status.json")
    monkeypatch.setattr(lifecycle, "RUNTIME_LIFECYCLE", runtime)
    monkeypatch.setattr(tooling, "RUNTIME_LIFECYCLE", runtime)
    journal = recovery.OperationRecoveryJournal()
    journal.configure(tmp_path / "recovery.jsonl", "workflow-testing")
    monkeypatch.setattr(recovery, "OPERATION_RECOVERY", journal)
    monkeypatch.setattr(tooling, "OPERATION_RECOVERY", journal)
    calls: list[bool] = []

    def verify(repo: Path, **kwargs: object) -> dict[str, object]:
        control.write_text(json.dumps(lifecycle.lifecycle_control_request("drain", "workflow-test", time.time() + 5)))
        assert runtime.poll_control() == "DRAINING"

        def admitted() -> dict[str, object]:
            assert resource_requires_native_ownership()
            recovery.mark_native_test_dispatch()
            calls.append(True)
            return {"ok": True, "required_unknown": False, "execution_uncertain": False}

        return recovery.run_native_operation("verify_changes", str(repo), admitted)

    monkeypatch.setattr(workflow_actions, "verify_changed_repository", verify)
    server = MCPServer("workflow-testing-admission")
    workflow_registry.register(server)

    async def scenario() -> None:
        async with Client(server) as client:
            step: dict[str, object] = {
                "name": "tests", "action": "verify_changes", "arguments": {"cwd": str(tmp_path), "checks": ["pytest"]},
            }
            if mode == "dag":
                step.update(step_id="tests", depends_on=[])
            definition = {"name": "verify", "execution_mode": mode, "steps": [step]}
            started = (await client.call_tool("workflow_start", {"definition": definition})).structured_content
            assert started and "workflow_id" in started, started
            result = (await client.call_tool("workflow_execute", {
                "workflow_id": started["workflow_id"], "expected_version": started["version"],
            })).structured_content
            assert result and result.get("state") == "completed", result

    asyncio.run(scenario())
    assert calls == [True]


def test_workflow_execute_and_operations_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = replace(workflow_registry.SETTINGS, state_dir=tmp_path)
    monkeypatch.setattr(workflow_registry, "SETTINGS", settings)
    monkeypatch.setattr(heartbeat, "SETTINGS", replace(heartbeat.SETTINGS, state_dir=tmp_path))
    target = tmp_path / "present.txt"
    target.write_text("ok", encoding="utf-8")

    async def scenario() -> None:
        async with Client(create_server()) as client:
            definition = {
                "name": "inspect",
                "steps": [{"name": "file", "action": "check_file", "arguments": {"path": str(target)}}],
            }
            started = await client.call_tool("workflow_start", {"definition": definition})
            assert started.structured_content
            workflow_id = started.structured_content["workflow_id"]
            dry = await client.call_tool("workflow_execute", {
                "workflow_id": workflow_id,
                "expected_version": started.structured_content["version"],
                "dry_run": True,
            })
            assert dry.structured_content and dry.structured_content["dry_run"] is True
            done = await client.call_tool("workflow_execute", {
                "workflow_id": workflow_id,
                "expected_version": started.structured_content["version"],
            })
            assert done.structured_content and done.structured_content["state"] == "completed"
            operations = await client.call_tool("workflow_operations", {
                "workflow_id": workflow_id, "offset": 0, "limit": 1, "state": "succeeded",
            })
            assert operations.structured_content and operations.structured_content["total_count"] == 1
            assert operations.structured_content["items"][0]["state"] == "succeeded"

    asyncio.run(scenario())
