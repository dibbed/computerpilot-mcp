from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from mcp import Client

from core import heartbeat
from core.registry import create_server
from tools.workflows import registry as workflow_registry


def test_plan_and_start_share_static_action_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = replace(workflow_registry.SETTINGS, state_dir=tmp_path)
    monkeypatch.setattr(workflow_registry, "SETTINGS", settings)
    monkeypatch.setattr(heartbeat, "SETTINGS", replace(heartbeat.SETTINGS, state_dir=tmp_path))
    definition = {
        "name": "adapter-required",
        "steps": [
            {
                "name": "browser",
                "action": "browser_action",
                "arguments": {"action": "click", "session_id": "missing"},
                "postcondition": {"kind": "browser_state"},
            }
        ],
    }

    async def scenario() -> None:
        async with Client(create_server()) as client:
            planned = await client.call_tool("workflow_plan", {"definition": definition})
            started = await client.call_tool("workflow_start", {"definition": definition})

        assert planned.structured_content
        assert started.structured_content
        assert planned.structured_content["ok"] is False
        assert started.structured_content["ok"] is False
        assert planned.structured_content["error"] == "workflow_action_unavailable"
        assert started.structured_content["error"] == "workflow_action_unavailable"

    asyncio.run(scenario())


def test_plan_returns_normalized_contract_and_dry_run_does_not_mutate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = replace(workflow_registry.SETTINGS, state_dir=tmp_path)
    monkeypatch.setattr(workflow_registry, "SETTINGS", settings)
    monkeypatch.setattr(heartbeat, "SETTINGS", replace(heartbeat.SETTINGS, state_dir=tmp_path))
    target = tmp_path / "ready.txt"
    target.write_text("ok", encoding="utf-8")
    definition = {
        "name": "inspect",
        "steps": [
            {
                "name": "file",
                "action": "check_file",
                "arguments": {"path": str(target)},
                "timeout_sec": 7,
                "max_retries": 2,
            }
        ],
    }

    async def scenario() -> None:
        async with Client(create_server()) as client:
            planned = await client.call_tool("workflow_plan", {"definition": definition})
            assert planned.structured_content
            plan = planned.structured_content
            assert plan["ok"] is True
            assert plan["mutating_step_count"] == 0
            assert plan["unavailable_actions"] == []
            assert plan["required_postconditions"] == []
            assert plan["estimated_max_runtime_sec"] == 21.0

            started = await client.call_tool("workflow_start", {"definition": definition})
            assert started.structured_content
            workflow_id = started.structured_content["workflow_id"]
            version = started.structured_content["version"]

            dry = await client.call_tool(
                "workflow_execute",
                {"workflow_id": workflow_id, "expected_version": version, "dry_run": True},
            )
            assert dry.structured_content
            assert dry.structured_content["dry_run"] is True
            assert dry.structured_content["estimated_max_runtime_sec"] == 21.0

            status = await client.call_tool("workflow_status", {"workflow_id": workflow_id})
            assert status.structured_content
            assert status.structured_content["state"] == "queued"
            assert status.structured_content["version"] == version

    asyncio.run(scenario())
