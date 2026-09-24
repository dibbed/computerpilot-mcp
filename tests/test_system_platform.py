from __future__ import annotations

import asyncio

from mcp import Client

from core.registry import create_server


def test_portable_system_tools_are_registered() -> None:
    names = {tool.name for tool in asyncio.run(create_server().list_tools())}
    assert {"system_info", "cpu_usage", "memory_usage", "disk_usage", "environment_variables"} <= names
    assert {"installed_software", "system_services"} <= names


def test_system_info_reports_current_host() -> None:
    async def scenario() -> None:
        async with Client(create_server()) as client:
            result = await client.call_tool("system_info", {})
            assert result.structured_content
            assert result.structured_content["ok"] is True
            assert result.structured_content["os"]

    asyncio.run(scenario())
