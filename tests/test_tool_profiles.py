from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from mcp import Client

from core import registry
from core.errors import ToolError
from core.tool_profiles import PROFILE_DOMAINS, resolve_profile


def _names(profile: str) -> set[str]:
    original = registry.SETTINGS
    registry.SETTINGS = replace(original, tool_profile=profile)
    try:
        return {tool.name for tool in asyncio.run(registry.create_server().list_tools())}
    finally:
        registry.SETTINGS = original


def test_full_is_default_and_preserves_complete_catalog() -> None:
    assert resolve_profile(None).name == "full"
    names = _names("full")
    assert len(names) == 108
    assert {"read_file", "run_process", "ui_invoke", "reconcile_operation", "discover_tool_domains", "recommend_tools"} <= names
    assert {"workflow_execute", "workflow_operations"} <= names


def test_minimal_profile_does_not_register_workflow_tools() -> None:
    names = _names("minimal")
    assert not any(name.startswith("workflow_") for name in names)


def test_every_named_profile_is_nonempty_and_keeps_discovery() -> None:
    for profile in PROFILE_DOMAINS:
        names = _names(profile)
        assert {"discover_tool_domains", "recommend_tools", "server_health"} <= names


def test_invalid_profile_fails_startup_with_guidance() -> None:
    with pytest.raises(ToolError, match="Unknown MCP tool profile"):
        resolve_profile("mystery")


def test_discovery_reports_active_profile_and_recommends_registered_tools() -> None:
    original = registry.SETTINGS
    registry.SETTINGS = replace(original, tool_profile="coding")

    async def scenario() -> None:
        async with Client(registry.create_server()) as client:
            domains = await client.call_tool("discover_tool_domains", {})
            assert domains.structured_content and domains.structured_content["active_profile"] == "coding"
            recommendations = await client.call_tool("recommend_tools", {"query": "transactional patch"})
            assert recommendations.structured_content
            assert any(item["name"] == "apply_patch" for item in recommendations.structured_content["items"])

    try:
        asyncio.run(scenario())
    finally:
        registry.SETTINGS = original
