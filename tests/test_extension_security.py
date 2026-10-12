from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
from mcp import types
from mcp.client.session import ClientSession

from core.errors import ToolError
from core.extensions.config import build_extension_environment
from core.extensions.manager import ExtensionLifecycleManager
from core.extensions.models import ExtensionDefinition, ExtensionState, ExtensionToolDescriptor, MutationClass, ToolPolicy
from core.extensions.registry import ExtensionLease, validate_schema_subset, validate_tool_arguments
from core.extensions.transport import bounded_stdio_transport
from core.platform import normalize_system


def admitted_call(
    schema: dict[str, Any], result: types.CallToolResult, output_schema: dict[str, Any] | None = None,
) -> tuple[ExtensionLifecycleManager, ExtensionLease, MagicMock]:
    manager = ExtensionLifecycleManager()
    definition = ExtensionDefinition.model_validate_json(json.dumps({
        "extension_id": "security-fixture", "display_name": "Fixture", "provider": "fixture",
        "command": "python", "working_directory": ".", "platforms": [normalize_system()],
        "enabled": True, "launch_approved": True, "trust_class": "trusted",
    }))
    owner = manager.register_extension(definition)
    owner.state_machine.transition(ExtensionState.STARTING)
    descriptor = ExtensionToolDescriptor.create(
        extension_id=definition.extension_id, provider=definition.provider, remote_name="read_value", description="Read fixture",
        input_schema=schema, output_schema=output_schema, catalog_generation=owner.generation,
        policy=ToolPolicy(remote_name="read_value", domain="system", mutation_class=MutationClass.READ_ONLY, allow_call=True),
    )
    manager.registry.publish_extension_catalog(definition.extension_id, owner.generation, (descriptor,))
    owner.state_machine.transition(ExtensionState.HEALTHY)
    session = MagicMock()
    session.call_tool = AsyncMock(return_value=result)
    session.send_request = AsyncMock(return_value=result)
    owner.register_session(session, "security-session")
    lease = manager.registry.create_lease(descriptor.public_name)
    assert lease is not None
    return manager, lease, session


@pytest.mark.parametrize("schema,arguments", [
    ({"type": "object", "properties": {"nested": {"type": "object", "required": ["file.name"]}}}, {"nested": {}}),
    ({"type": "object", "properties": {"items": {"type": "array", "uniqueItems": True}}}, {"items": [1, 1]}),
    ({"type": "object", "allOf": [{"required": ["name"]}]}, {}),
    ({"type": "object", "properties": {"count": {"type": "integer", "multipleOf": 2}}}, {"count": 3}),
])
def test_accepted_nested_and_composition_constraints_are_enforced(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    assert not validate_tool_arguments(schema, arguments).valid


@pytest.mark.parametrize("schema", [{"type": "invalid_type"}, {"type": "string", "minLength": "wrong"}])
def test_malformed_schema_is_rejected_before_catalog_publication(schema: dict[str, Any]) -> None:
    assert not validate_schema_subset(schema).valid


def test_remote_schema_cannot_trigger_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("schema validation attempted network access")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    assert not validate_tool_arguments({"$ref": "https://example.invalid/no-fetch"}, {}).valid


@pytest.mark.parametrize("block,expected_type", [
    (types.ImageContent(type="image", data="Zml4dHVyZQ==", mime_type="image/png"), "image"),
    (types.AudioContent(type="audio", data="Zml4dHVyZQ==", mime_type="audio/wav"), "audio"),
    (types.ResourceLink(name="offline", uri="https://example.invalid/no-fetch"), "resource_link"),
    (types.EmbeddedResource(type="resource", resource=types.TextResourceContents(uri="file:///not-opened", text="fixture")), "resource"),
])
def test_binary_and_resource_blocks_are_data_only(block: Any, expected_type: str) -> None:
    manager, lease, _ = admitted_call({"type": "object"}, types.CallToolResult(content=[block]))

    async def scenario() -> None:
        result = await manager.execute_guarded_call(lease)
        assert result["ok"] is True and result["content"][0]["type"] == expected_type

    anyio.run(scenario)


def test_invalid_arguments_are_denied_before_provider_dispatch() -> None:
    manager, lease, session = admitted_call(
        {"type": "object", "properties": {"file.name": {"type": "string"}}, "required": ["file.name"]},
        types.CallToolResult(content=[]),
    )

    async def scenario() -> None:
        with pytest.raises(ToolError) as denied:
            await manager.execute_guarded_call(lease, {"file.name": 1})
        assert denied.value.code == "invalid_arguments"
        session.call_tool.assert_not_awaited()
        session.send_request.assert_not_awaited()

    anyio.run(scenario)


def test_calls_use_public_request_and_return_core_envelope() -> None:
    raw = types.CallToolResult(content=[types.TextContent(type="text", text="fixture")], structured_content={"file.name": "exact"})
    manager, lease, session = admitted_call({"type": "object"}, raw)

    async def scenario() -> None:
        result = await manager.execute_guarded_call(lease, {"file.name": "exact"})
        assert result["ok"] is True and result["outcome"] == "succeeded"
        assert result["source_type"] == "external_mcp" and result["tool"] == lease.public_name
        assert result["structured_content"] == {"file.name": "exact"}
        session.call_tool.assert_not_awaited()
        request = session.send_request.await_args.args[0]
        assert request.params.arguments == {"file.name": "exact"}
        assert session.send_request.await_args.args[1] is types.CallToolResult

    anyio.run(scenario)


def test_real_owned_fixture_returns_exact_arguments_in_core_envelope(tmp_path: Path) -> None:
    manager, lease, _ = admitted_call({"type": "object"}, types.CallToolResult(content=[]))
    owner = manager.get_owner("security-fixture")
    assert owner is not None
    fixture = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"
    definition = owner.definition.model_copy(update={"command": sys.executable, "working_directory": str(tmp_path)})
    env = build_extension_environment(definition)
    env["MCP_EXTENSIONS_ENABLED"] = "0"

    async def scenario() -> None:
        async with bounded_stdio_transport(
            [sys.executable, str(fixture.resolve()), "--workspace", str(tmp_path)], cwd=tmp_path, env=env,
        ) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            owner.register_session(session, "real-security-session")
            result = await manager.execute_guarded_call(lease, {"value": "safe", "file.name": "exact.property"})
            assert result["ok"] is True
            assert result["structured_content"] == {"value": "safe", "arguments": {"value": "safe", "file.name": "exact.property"}}
            owner.close_active_session()

    anyio.run(scenario)
    assert (tmp_path / "call_received").exists()
    assert not (tmp_path / "effects.jsonl").exists()


@pytest.mark.parametrize("result", [
    types.CallToolResult(content=[], result_type="input_required"),
    types.CallToolResult(content=[], result_type="claimed_extension_result"),
    types.CallToolResult(content=[], structured_content={"count": "bad"}),
])
def test_nonterminal_or_invalid_provider_output_is_rejected(result: types.CallToolResult) -> None:
    manager, lease, _ = admitted_call(
        {"type": "object"}, result,
        {"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]},
    )

    async def scenario() -> None:
        with pytest.raises(ToolError) as denied:
            await manager.execute_guarded_call(lease)
        assert denied.value.code == "malformed_result"

    anyio.run(scenario)
