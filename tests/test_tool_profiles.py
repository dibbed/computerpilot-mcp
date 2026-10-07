from __future__ import annotations

import asyncio
import os
from dataclasses import replace

import pytest
from mcp import Client

from core import registry
from core.config import Settings
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
    if os.name == "nt":
        assert len(names) == 150
    else:
        assert "run_shell" in names
        assert "run_cmd" not in names
        assert "installed_programs" not in names
        assert "windows_services" not in names
        assert "desktop_screenshot" not in names
    assert {
        "read_file",
        "run_process",
        "system_info",
        "installed_software",
        "system_services",
        *(["ui_invoke"] if os.name == "nt" else []),
        "reconcile_operation",
        "discover_tool_domains",
        "recommend_tools",
    } <= names
    assert {"workflow_execute", "workflow_operations", "workflow_reconcile", "workflow_acknowledge_operation"} <= names


def test_minimal_profile_does_not_register_workflow_tools() -> None:
    names = _names("minimal")
    assert not any(name.startswith("workflow_") for name in names)


def test_every_named_profile_is_nonempty_and_keeps_discovery() -> None:
    for profile in PROFILE_DOMAINS:
        names = _names(profile)
        assert {"discover_tool_domains", "recommend_tools", "execution_candidates", "execution_recommend", "server_health"} <= names


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


def test_router_settings_have_safe_defaults() -> None:
    assert registry.SETTINGS.execution_router_enabled is True
    assert registry.SETTINGS.execution_router_policy == "deterministic-v1"
    assert registry.SETTINGS.execution_router_explain is True


def test_router_diagnostics_use_registered_profile_and_enrich_recommendations() -> None:
    original = registry.SETTINGS
    registry.SETTINGS = replace(original, tool_profile="documents")

    async def scenario() -> None:
        async with Client(registry.create_server()) as client:
            recommendation = await client.call_tool(
                "execution_recommend",
                {"intent": "document.excel.write", "allow_raw_desktop": True},
            )
            assert recommendation.structured_content
            assert recommendation.structured_content["selected_route"] == "native.excel"
            assert recommendation.structured_content["representative_tool"] == "excel_write_range"

            candidates = await client.call_tool(
                "execution_candidates",
                {"intent": "document.excel.write", "max_candidates": 25, "allow_raw_desktop": True},
            )
            assert candidates.structured_content
            by_route = {item["route"]: item for item in candidates.structured_content["items"]}
            assert by_route["native.excel"]["supported"] is True
            assert by_route["semantic.windows_uia"]["supported"] is False
            assert by_route["semantic.windows_uia"]["rejection_code"] == "tool_unavailable"

            recommendations = await client.call_tool("recommend_tools", {"query": "transactional patch"})
            assert recommendations.structured_content
            patch = next(
                item for item in recommendations.structured_content["items"]
                if item["name"] == "apply_patch"
            )
            assert patch["execution"]["route"] == "native.filesystem"
            assert patch["execution"]["determinism"] == 5
            assert patch["execution"]["recoverability"] == "strong"

    try:
        asyncio.run(scenario())
    finally:
        registry.SETTINGS = original


def test_disabled_and_unknown_policy_router_fail_as_structured_tool_errors() -> None:
    async def call_with(settings: Settings) -> tuple[dict[str, object], dict[str, object]]:
        original = registry.SETTINGS
        registry.SETTINGS = settings
        try:
            async with Client(registry.create_server()) as client:
                recommended = await client.call_tool("execution_recommend", {"intent": "filesystem.read"})
                candidates = await client.call_tool("execution_candidates", {"intent": "filesystem.read"})
                assert isinstance(recommended.structured_content, dict)
                assert isinstance(candidates.structured_content, dict)
                return recommended.structured_content, candidates.structured_content
        finally:
            registry.SETTINGS = original

    disabled = replace(registry.SETTINGS, execution_router_enabled=False)
    recommended, candidates = asyncio.run(call_with(disabled))
    assert recommended["ok"] is False
    assert recommended["error"] == "execution_router_disabled"
    assert candidates["ok"] is False
    assert candidates["error"] == "execution_router_disabled"

    unsupported = replace(registry.SETTINGS, execution_router_policy="mystery-v9")
    recommended, candidates = asyncio.run(call_with(unsupported))
    assert recommended["ok"] is False
    assert recommended["error"] == "unsupported_router_policy"
    assert candidates["ok"] is False
    assert candidates["error"] == "unsupported_router_policy"
