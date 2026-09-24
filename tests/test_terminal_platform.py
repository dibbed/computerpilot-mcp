from __future__ import annotations

import asyncio
import os
import sys
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
