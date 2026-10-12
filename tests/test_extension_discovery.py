from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import MagicMock

import anyio
import pytest
from mcp import types
from test_extension_security import admitted_call

from core import registry
from core.extensions.bridge import ExtensionMCPServer
from core.extensions.manager import ExtensionLifecycleManager
from core.extensions.models import ExtensionEnvironment
from core.tool_profiles import PROFILE_DOMAINS


def prepared_server(
    profile: str, monkeypatch: pytest.MonkeyPatch,
) -> tuple[ExtensionMCPServer, ExtensionLifecycleManager, MagicMock]:
    monkeypatch.setattr(registry, "SETTINGS", replace(registry.SETTINGS, tool_profile=profile, extensions_enabled=False))
    server = registry.create_server()
    assert isinstance(server, ExtensionMCPServer)
    manager, _, session = admitted_call({"type": "object"}, types.CallToolResult(content=[]))
    descriptor = manager.list_tools()[0]
    descriptor = replace(descriptor, catalog_generation=descriptor.catalog_generation + 1,
                         policy=descriptor.policy.model_copy(update={"profiles": (profile,)}))
    manager.registry.publish_extension_catalog(descriptor.extension_id, descriptor.catalog_generation, (descriptor,))
    server.extension_manager = manager
    return server, manager, session


@pytest.mark.parametrize("profile", tuple(PROFILE_DOMAINS))
def test_nine_profiles_preserve_native_catalog_and_report_only_approved_live_external_counts(
    profile: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, manager, session = prepared_server(profile, monkeypatch)

    async def scenario() -> None:
        server.extension_manager = None
        native = await server.list_tools()
        server.extension_manager = manager
        listed = await server.list_tools()
        native_now = [tool for tool in listed if not tool.name.startswith("external.")]
        assert [tool.model_dump() for tool in native_now] == [tool.model_dump() for tool in native]
        external = [tool for tool in listed if tool.name.startswith("external.")]
        assert len(external) == 1
        domains = await server.call_tool("discover_tool_domains", {})
        assert isinstance(domains, types.CallToolResult) and domains.structured_content
        assert domains.structured_content["profiles"] == {name: list(values) for name, values in PROFILE_DOMAINS.items()}
        assert domains.structured_content["tool_counts"] == {"native": len(native), "external": 1, "total": len(native) + 1}
        if profile == "minimal":
            assert "write_file" in {tool.name for tool in native}
        session.send_request.assert_not_awaited()

    anyio.run(scenario)


def test_full_profile_grants_no_implicit_external_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    server, manager, session = prepared_server("full", monkeypatch)
    descriptor = manager.list_tools()[0]
    descriptor = replace(descriptor, catalog_generation=descriptor.catalog_generation + 1,
                         policy=descriptor.policy.model_copy(update={"profiles": ()}))
    manager.registry.publish_extension_catalog(descriptor.extension_id, descriptor.catalog_generation, (descriptor,))

    async def scenario() -> None:
        assert not server.external_tools()
        result = await server.call_tool(descriptor.public_name, {})
        assert isinstance(result, types.CallToolResult) and result.is_error
        session.send_request.assert_not_awaited()

    anyio.run(scenario)


@pytest.mark.parametrize("retired", [False, True])
def test_recommendations_attribute_eligible_tools_and_drop_disabled_or_retired_phantoms(
    retired: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, manager, session = prepared_server("full", monkeypatch)
    descriptor = manager.list_tools()[0]

    async def scenario() -> None:
        result = await server.call_tool("recommend_tools", {"query": "read_value", "limit": 1000})
        assert isinstance(result, types.CallToolResult) and result.structured_content
        item = next(item for item in result.structured_content["items"] if item["name"] == descriptor.public_name)
        assert item["source_type"] == "external_mcp"
        assert item["extension_id"] == descriptor.extension_id
        assert item["provider"] == descriptor.identity.provider
        assert item["domain"] == "system"
        assert len(item["description"]) <= 180
        if retired:
            manager.registry.retire_extension_catalog(descriptor.extension_id)
        else:
            owner = manager.get_owner(descriptor.extension_id)
            assert owner is not None
            owner.is_enabled = False
        after = await server.call_tool("recommend_tools", {"query": "read_value"})
        assert isinstance(after, types.CallToolResult) and after.structured_content
        assert not any(item["name"] == descriptor.public_name for item in after.structured_content["items"])
        counts = await server.call_tool("discover_tool_domains", {})
        assert isinstance(counts, types.CallToolResult) and counts.structured_content
        assert counts.structured_content["tool_counts"]["external"] == 0
        session.send_request.assert_not_awaited()

    anyio.run(scenario)


def test_provider_description_is_sanitized_before_public_discovery_and_recommendation(monkeypatch: pytest.MonkeyPatch) -> None:
    canary = "private-description-credential-47b9"
    monkeypatch.setenv("DISCOVERY_OPERATOR_TOKEN", canary)
    server, manager, session = prepared_server("full", monkeypatch)
    owner = manager.get_owner("security-fixture")
    assert owner is not None
    owner.definition = owner.definition.model_copy(update={"environment": ExtensionEnvironment.model_validate_json(json.dumps({
        "references": {"TOKEN": {"kind": "env", "name": "DISCOVERY_OPERATOR_TOKEN"}},
    }))})
    descriptor = manager.list_tools()[0]
    descriptor = replace(descriptor, catalog_generation=descriptor.catalog_generation + 1,
                         description=(canary + " untrusted prose " * 20)[:180])
    manager.registry.publish_extension_catalog(descriptor.extension_id, descriptor.catalog_generation, (descriptor,))

    async def scenario() -> None:
        listed = await server.list_tools()
        assert canary not in json.dumps([tool.model_dump(mode="json") for tool in listed])
        result = await server.call_tool("recommend_tools", {"query": "read_value"})
        assert isinstance(result, types.CallToolResult) and result.structured_content
        assert canary not in result.model_dump_json()
        session.send_request.assert_not_awaited()

    anyio.run(scenario)
