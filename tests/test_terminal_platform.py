from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from mcp import Client

from core.registry import create_server


def _tool_names() -> set[str]:
    return {tool.name for tool in asyncio.run(create_server().list_tools())}


def test_platform_shell_registration_matches_host() -> None:
    names = _tool_names()
    if os.name == "nt":
        assert "run_cmd" in names
        assert "run_shell" not in names
    else:
        assert "run_shell" in names
        assert "run_cmd" not in names


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell test requires POSIX")
def test_run_shell_executes_with_explicit_shell(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Client(create_server()) as client:
            result = await client.call_tool(
                "run_shell",
                {"command_text": "printf 'portable-shell'", "cwd": str(tmp_path)},
            )
            assert result.structured_content
            payload = result.structured_content
            assert payload["ok"] is True
            assert payload["stdout"]["text"] == "portable-shell"
            assert payload["process_ownership"] == "posix_process_group"

    asyncio.run(scenario())


def test_run_process_remains_shell_free(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Client(create_server()) as client:
            result = await client.call_tool(
                "run_process",
                {
                    "executable": sys.executable,
                    "args": ["-c", "print('direct')"],
                    "cwd": str(tmp_path),
                },
            )
            assert result.structured_content
            assert result.structured_content["stdout"]["text"].strip() == "direct"

    asyncio.run(scenario())


def test_public_command_cleanup_denial_keeps_durable_fence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import config
    from core.errors import ToolError
    from core.resource_claims import coordination_claim, path_claim
    from core.workflow_store import WorkflowStore
    from tools.terminal import registry

    monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, state_dir=tmp_path))

    def failed(*args: object, **kwargs: object) -> None:
        raise ToolError("process_cleanup_incomplete", "owned tree cleanup denied")

    monkeypatch.setattr(registry, "run_bounded", failed)

    async def scenario() -> None:
        async with Client(create_server()) as client:
            result = await client.call_tool("run_process", {"executable": sys.executable, "cwd": str(tmp_path)})
            assert result.structured_content
            assert result.structured_content["error"] == "process_cleanup_incomplete"

    asyncio.run(scenario())
    resources = WorkflowStore(config.SETTINGS.workflow_db, recover=False)
    conflict = resources.reserve_resource_claims(
        request_id="conflict", owner_token="other", category="test",
        claims=(coordination_claim(), path_claim(tmp_path / "target", "write")),
    )
    assert conflict["admitted"] is False
    assert conflict["reason"] == "resource_quarantined"
