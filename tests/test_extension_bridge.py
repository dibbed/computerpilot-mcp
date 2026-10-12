from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import anyio
import pytest
from mcp import Client, types
from test_extension_security import admitted_call

from core.extensions.bridge import ExtensionMCPServer
from core.extensions.manager import ExtensionLifecycleManager
from core.extensions.models import ExtensionEnvironment, ExtensionState, MutationClass
from core.extensions.output import ExtensionResult


def bridge(profile: str = "full") -> tuple[ExtensionMCPServer, ExtensionLifecycleManager, MagicMock]:
    schema = {
        "type": "object", "properties": {"file.name": {"type": "string", "default": "not injected"}},
        "additionalProperties": True,
    }
    manager, _, session = admitted_call(schema, types.CallToolResult(content=[], structured_content={"value": "safe"}))
    server = ExtensionMCPServer("bridge-fixture", tool_profile=profile)
    server.extension_manager = manager

    @server.tool(structured_output=True)
    def native_echo(value: str) -> dict[str, str]:
        return {"value": value}

    return server, manager, session


def test_public_list_and_direct_wire_calls_preserve_exact_external_schema_and_native_path() -> None:
    server, manager, session = bridge()

    async def scenario() -> None:
        listed = await server.list_tools()
        external = next(tool for tool in listed if tool.name.startswith("external."))
        descriptor = manager.registry.get_tool(external.name)
        assert descriptor is not None
        assert external.input_schema == descriptor.input_schema
        assert external.output_schema == ExtensionResult.model_json_schema()
        arguments: dict[str, Any] = {"file.name": "exact", "extra": {"nested": 12}}
        direct = await server.call_tool(external.name, arguments)
        assert isinstance(direct, types.CallToolResult)
        async with Client(server) as client:
            wire_list = await client.list_tools()
            assert [tool.name for tool in wire_list.tools] == [tool.name for tool in listed]
            wire = await client.call_tool(external.name, arguments)
            native = await client.call_tool("native_echo", {"value": "native"})
        assert direct.structured_content == wire.structured_content
        assert wire.structured_content and wire.structured_content["structured_content"] == {"value": "safe"}
        ExtensionResult.model_validate_json(json.dumps(wire.structured_content))
        assert native.structured_content == {"value": "native"}
        assert session.send_request.await_count == 2
        assert session.send_request.await_args.args[0].params.arguments == arguments

    anyio.run(scenario)


@pytest.mark.parametrize("denial", ["profile", "launch", "trust", "disabled", "health", "policy", "mutation"])
def test_public_direct_and_sdk_calls_share_denial_without_provider_dispatch(denial: str) -> None:
    server, manager, session = bridge("minimal" if denial == "profile" else "full")
    owner = manager.get_owner("security-fixture")
    assert owner is not None
    descriptor = manager.list_tools()[0]
    if denial in {"launch", "trust"}:
        field = "launch_approved" if denial == "launch" else "trust_class"
        value: Any = False if denial == "launch" else "untrusted"
        owner.definition = owner.definition.model_copy(update={field: value})
    elif denial == "disabled":
        owner.is_enabled = False
    elif denial == "health":
        owner.state_machine.transition(ExtensionState.DEGRADED)
    elif denial in {"policy", "mutation"}:
        from dataclasses import replace

        policy = descriptor.policy.model_copy(update={
            "allow_call": False if denial == "policy" else True,
            "mutation_class": MutationClass.LOCAL_MUTATION if denial == "mutation" else MutationClass.READ_ONLY,
        })
        descriptor = replace(descriptor, policy=policy, catalog_generation=descriptor.catalog_generation + 1)
        manager.registry.publish_extension_catalog(descriptor.extension_id, descriptor.catalog_generation, (descriptor,))

    async def scenario() -> None:
        assert not any(tool.name.startswith("external.") for tool in await server.list_tools())
        direct = await server.call_tool(descriptor.public_name, {})
        assert isinstance(direct, types.CallToolResult)
        async with Client(server) as client:
            wire = await client.call_tool(descriptor.public_name, {})
        assert direct.is_error and wire.is_error
        assert direct.structured_content == wire.structured_content
        assert direct.structured_content and direct.structured_content["outcome"] == "not_dispatched"
        session.send_request.assert_not_awaited()

    anyio.run(scenario)


def test_unknown_external_name_returns_bounded_core_error_without_native_or_provider_dispatch() -> None:
    server, _, session = bridge()

    async def scenario() -> None:
        result = await server.call_tool("external.unknown." + "x" * 10000, {})
        assert isinstance(result, types.CallToolResult)
        assert result.is_error and result.structured_content
        assert result.structured_content["outcome"] == "not_dispatched"
        assert len(json.dumps(result.structured_content)) < 2048
        session.send_request.assert_not_awaited()

    anyio.run(scenario)


def test_credential_bearing_schema_is_withheld_instead_of_changing_its_public_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    canary = "private-metadata-credential-47b9"
    monkeypatch.setenv("BRIDGE_OPERATOR_TOKEN", canary)
    server, manager, session = bridge()
    owner = manager.get_owner("security-fixture")
    assert owner is not None
    owner.definition = owner.definition.model_copy(update={"environment": ExtensionEnvironment.model_validate_json(json.dumps({
        "references": {"TOKEN": {"kind": "env", "name": "BRIDGE_OPERATOR_TOKEN"}},
    }))})
    descriptor = manager.list_tools()[0]
    from core.extensions.models import ExtensionToolDescriptor

    secret_schema = ExtensionToolDescriptor.create(
        extension_id=descriptor.extension_id, provider=descriptor.identity.provider, remote_name="secret_metadata",
        description="safe", input_schema={"type": "object", "properties": {"x": {"const": canary}}},
        policy=descriptor.policy.model_copy(update={"remote_name": "secret_metadata"}),
        catalog_generation=descriptor.catalog_generation + 1,
    )
    manager.registry.publish_extension_catalog(descriptor.extension_id, secret_schema.catalog_generation, (secret_schema,))

    async def scenario() -> None:
        listed = await server.list_tools()
        assert not any(tool.name == secret_schema.public_name for tool in listed)
        result = await server.call_tool(secret_schema.public_name, {"x": canary})
        assert isinstance(result, types.CallToolResult)
        assert result.is_error and canary not in result.model_dump_json()
        session.send_request.assert_not_awaited()

    anyio.run(scenario)


def test_auth_revocation_while_queued_prevents_provider_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    monkeypatch.setenv("BRIDGE_OPERATOR_TOKEN", "private-queue-credential-47b9")
    server, manager, session = bridge()
    owner = manager.get_owner("security-fixture")
    assert owner is not None
    owner.definition = owner.definition.model_copy(update={"environment": ExtensionEnvironment.model_validate_json(json.dumps({
        "references": {"TOKEN": {"kind": "env", "name": "BRIDGE_OPERATOR_TOKEN"}},
    }))})
    descriptor = manager.list_tools()[0]
    descriptor = replace(descriptor, catalog_generation=descriptor.catalog_generation + 1,
                         policy=descriptor.policy.model_copy(update={"required_env_refs": ("TOKEN",)}))
    manager.registry.publish_extension_catalog(descriptor.extension_id, descriptor.catalog_generation, (descriptor,))
    results: list[types.CallToolResult] = []

    async def scenario() -> None:
        await owner.call_semaphore.acquire()
        await owner.call_semaphore.acquire()
        async def call() -> None:
            result = await server.call_tool(descriptor.public_name, {})
            assert isinstance(result, types.CallToolResult)
            results.append(result)
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(call)
            await anyio.sleep(0.01)
            monkeypatch.delenv("BRIDGE_OPERATOR_TOKEN")
            owner.call_semaphore.release()
            owner.call_semaphore.release()
        assert len(results) == 1 and results[0].is_error
        assert results[0].structured_content and results[0].structured_content["outcome"] == "not_dispatched"
        session.send_request.assert_not_awaited()

    anyio.run(scenario)


def test_call_deadline_includes_queue_and_provider_time() -> None:
    from core.errors import ToolError

    _, manager, session = bridge()
    descriptor = manager.list_tools()[0]
    owner = manager.get_owner(descriptor.extension_id)
    assert owner is not None
    lease = manager.registry.create_lease(descriptor.public_name)
    assert lease is not None

    async def provider(*args: Any, **kwargs: Any) -> types.CallToolResult:
        await anyio.sleep(0.12)
        return types.CallToolResult(content=[], structured_content={"value": "too late"})
    session.send_request.side_effect = provider

    async def scenario() -> None:
        await owner.call_semaphore.acquire()
        await owner.call_semaphore.acquire()
        async def release_queue() -> None:
            await anyio.sleep(0.12)
            owner.call_semaphore.release()
            owner.call_semaphore.release()
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(release_queue)
            with pytest.raises(ToolError, match="timed out"):
                await manager.execute_guarded_call(lease, timeout_sec=0.20)

    anyio.run(scenario)
