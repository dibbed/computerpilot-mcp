from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from mcp import Client

from core import heartbeat
from core.registry import create_server
from tools.workflows import registry as workflow_registry


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
