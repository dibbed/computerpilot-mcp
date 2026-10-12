from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import pytest
from mcp import Client, types
from mcp.server.context import ServerRequestContext
from mcp.shared.subscriptions import ToolsListChanged
from test_extension_bridge import bridge

from core.extensions.manager import ExtensionLifecycleManager, connect_and_discover_tools
from core.extensions.models import ExtensionDefinition, ExtensionState
from core.extensions.registry import CatalogLimitExceededError
from core.platform import normalize_system


def configured_manager(tmp_path: Path, mode: str, *, starting: bool = True) -> ExtensionLifecycleManager:
    fixture = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"
    definition = ExtensionDefinition.model_validate_json(json.dumps({
        "extension_id": "refresh-fixture", "display_name": "Refresh fixture", "provider": "fixture",
        "enabled": True, "launch_approved": True, "trust_class": "trusted", "platforms": [normalize_system()],
        "command": sys.executable, "arguments": [str(fixture.resolve()), "--workspace", str(tmp_path), "--mode", mode],
        "working_directory": str(tmp_path), "tools": {"read_value": {
            "remote_name": "read_value", "domain": "system", "mutation_class": "read_only", "allow_call": True,
            "profiles": ["full"],
        }},
    }))
    manager = ExtensionLifecycleManager()
    owner = manager.register_extension(definition)
    if starting:
        owner.state_machine.transition(ExtensionState.STARTING)
    return manager


@pytest.mark.parametrize("legacy", [False, True])
def test_real_sdk_clients_receive_catalog_change_and_relist_tools(legacy: bool) -> None:
    server, manager, _ = bridge()
    received = anyio.Event()

    async def handler(message: Any) -> None:
        if isinstance(message, types.ToolListChangedNotification):
            received.set()

    async def scenario() -> None:
        async with Client(server, mode="legacy" if legacy else "auto", message_handler=handler) as client:
            before = await client.list_tools()
            assert any(tool.name.startswith("external.") for tool in before.tools)
            if legacy:
                manager.registry.retire_extension_catalog("security-fixture")
                with anyio.fail_after(2):
                    await received.wait()
            else:
                assert client.protocol_version == "2026-07-28"
                async with client.listen(tools_list_changed=True) as subscription:
                    manager.registry.retire_extension_catalog("security-fixture")
                    with anyio.fail_after(2):
                        event = await subscription.__anext__()
                    assert isinstance(event, ToolsListChanged)
            after = await client.list_tools()
            assert not any(tool.name.startswith("external.") for tool in after.tools)

    anyio.run(scenario)


@pytest.mark.parametrize("ttl_ms", [0, 60_000])
def test_real_modern_sdk_response_cache_is_invalidated_by_notifications(ttl_ms: int, monkeypatch: pytest.MonkeyPatch) -> None:
    server, manager, session = bridge(list_cache_ttl_ms=ttl_ms)
    descriptor = manager.list_tools()[0]
    permit = anyio.Event()
    original_publish = server.catalog_subscriptions.publish

    async def publish(event: Any) -> None:
        await permit.wait()
        await original_publish(event)

    monkeypatch.setattr(server.catalog_subscriptions, "publish", publish)

    async def scenario() -> None:
        nonlocal permit
        async with Client(server) as client, client.listen(tools_list_changed=True) as subscription:
            assert descriptor.public_name in {tool.name for tool in (await client.list_tools()).tools}
            manager.registry.retire_extension_catalog(descriptor.extension_id)
            before_event = {tool.name for tool in (await client.list_tools()).tools}
            assert (descriptor.public_name in before_event) == bool(ttl_ms)
            denied = await client.call_tool(descriptor.public_name, {})
            assert denied.is_error and denied.structured_content is not None
            assert denied.structured_content["outcome"] == "not_dispatched"
            session.send_request.assert_not_awaited()
            assert (descriptor.public_name in {tool.name for tool in (await client.list_tools()).tools}) == bool(ttl_ms)
            permit.set()
            with anyio.fail_after(3):
                assert isinstance(await subscription.__anext__(), ToolsListChanged)
            assert descriptor.public_name not in {tool.name for tool in (await client.list_tools()).tools}

            permit = anyio.Event()
            generation = manager.registry.next_catalog_generation(descriptor.extension_id)
            manager.registry.publish_extension_catalog(
                descriptor.extension_id, generation, (replace(descriptor, catalog_generation=generation),),
            )
            before_event = {tool.name for tool in (await client.list_tools()).tools}
            assert (descriptor.public_name not in before_event) == bool(ttl_ms)
            permit.set()
            with anyio.fail_after(3):
                assert isinstance(await subscription.__anext__(), ToolsListChanged)
            restored = next(tool for tool in (await client.list_tools()).tools if tool.name == descriptor.public_name)
            assert restored.meta is not None and restored.meta["catalog_generation"] == generation

    anyio.run(scenario)


def test_healthy_owned_connection_outlives_its_startup_deadline(tmp_path: Path) -> None:
    manager = configured_manager(tmp_path, "readonly")
    owner = manager.get_owner("refresh-fixture")
    assert owner is not None

    async def scenario() -> None:
        stop = anyio.Event()
        async def run() -> None:
            await connect_and_discover_tools(owner, manager.registry, startup_timeout=3.0, keep_alive_event=stop)
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(run)
            with anyio.fail_after(3):
                while owner.state != ExtensionState.HEALTHY:
                    await anyio.sleep(0.01)
            await anyio.sleep(3.1)
            assert owner.state == ExtensionState.HEALTHY
            lease = manager.registry.create_lease("external.refresh-fixture.read_value")
            assert lease is not None
            assert (await manager.execute_guarded_call(lease))["ok"]
            stop.set()

    anyio.run(scenario)


def test_real_provider_notification_flood_refreshes_a_coalesced_catalog(tmp_path: Path) -> None:
    manager = configured_manager(tmp_path, "notification_flood")
    owner = manager.get_owner("refresh-fixture")
    assert owner is not None

    async def scenario() -> None:
        stop = anyio.Event()
        async def run() -> None:
            await connect_and_discover_tools(owner, manager.registry, keep_alive_event=stop)
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(run)
            with anyio.fail_after(5):
                while owner.state != ExtensionState.HEALTHY:
                    await anyio.sleep(0.01)
            lease = manager.registry.create_lease("external.refresh-fixture.read_value")
            assert lease is not None
            assert (await manager.execute_guarded_call(lease))["ok"]
            with anyio.fail_after(2):
                while manager.registry.active_snapshot.extension_generations[owner.extension_id] <= lease.catalog_generation:
                    await anyio.sleep(0.01)
            await anyio.sleep(0.15)
            assert manager.registry.active_snapshot.extension_generations[owner.extension_id] <= lease.catalog_generation + 2
            assert owner.state == ExtensionState.HEALTHY
            stop.set()

    anyio.run(scenario)


def test_real_provider_cursor_cycles_fail_without_catalog_publication(tmp_path: Path) -> None:
    manager = configured_manager(tmp_path, "cursor_loop")
    owner = manager.get_owner("refresh-fixture")
    assert owner is not None

    async def scenario() -> None:
        with pytest.raises(Exception) as failure:
            await connect_and_discover_tools(owner, manager.registry)
        pending = [failure.value]
        causes: list[Exception] = []
        while pending:
            error = pending.pop()
            children = getattr(error, "exceptions", ())
            if children:
                pending.extend(children)
            else:
                causes.append(error)
        assert len(causes) == 1 and isinstance(causes[0], CatalogLimitExceededError)
        assert "cursor" in str(causes[0])
        assert manager.registry.active_snapshot.total_tools == 0

    anyio.run(scenario)


def test_live_changed_removed_and_readded_schema_keeps_the_first_runtime_contract(tmp_path: Path) -> None:
    manager = configured_manager(tmp_path, "catalog_change")
    owner = manager.get_owner("refresh-fixture")
    assert owner is not None
    name = "external.refresh-fixture.read_value"

    async def scenario() -> None:
        stop = anyio.Event()
        async def run() -> None:
            await connect_and_discover_tools(owner, manager.registry, keep_alive_event=stop)
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(run)
            with anyio.fail_after(5):
                while owner.state != ExtensionState.HEALTHY:
                    await anyio.sleep(0.01)
            original = manager.registry.get_tool(name)
            assert original is not None
            for epoch in (1, 2, 1, 3, 0):
                previous = manager.registry.manager_generation
                (tmp_path / "catalog_epoch").write_text(str(epoch), encoding="utf-8")
                with anyio.fail_after(3):
                    while manager.registry.manager_generation == previous or owner.state != ExtensionState.HEALTHY:
                        await anyio.sleep(0.01)
                if epoch == 1:
                    assert manager.registry.get_tool(name) is None
                    assert manager.registry.get_withheld_reason(name) == "schema_changed_restart_required"
                elif epoch == 2:
                    assert manager.registry.get_tool(name) is None
                elif epoch == 3:
                    assert manager.registry.get_tool(name) is None
                    assert manager.registry.get_tool("external.refresh-fixture.renamed_read_value") is not None
                else:
                    restored = manager.registry.get_tool(name)
                    assert restored is not None and restored.schema_fingerprint == original.schema_fingerprint
            stop.set()

    anyio.run(scenario)


def test_legacy_notification_handles_are_bounded_and_overflow_keeps_native_tools() -> None:
    from core.extensions.models import LIMITS

    server, _, _ = bridge()
    params: list[object] = []

    async def list_next(ctx: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=await server.list_tools())

    async def scenario() -> None:
        for _ in range(LIMITS.notification_pending + 1):
            params.append(object())
            session: Any = SimpleNamespace(client_params=params[-1])
            ctx: ServerRequestContext[dict[str, Any], Any] = ServerRequestContext(
                session=session, lifespan_context={}, protocol_version="2025-11-25", method="tools/list",
            )
            result = await server.observe_client(ctx, list_next)
            assert isinstance(result, types.ListToolsResult)
        assert len(server._legacy_sessions) == LIMITS.notification_pending
        assert isinstance(result, types.ListToolsResult)
        assert [tool.name for tool in result.tools] == ["native_echo"]

    anyio.run(scenario)


def test_refresh_state_history_stays_bounded_during_sustained_changes() -> None:
    from core.extensions.manager import ExtensionLifecycleStateMachine
    from core.extensions.models import LIMITS

    state = ExtensionLifecycleStateMachine("history-fixture")
    state.transition(ExtensionState.STARTING)
    state.transition(ExtensionState.HEALTHY)
    for _ in range(1000):
        state.transition(ExtensionState.DEGRADED, reason="catalog_refresh_pending")
        state.transition(ExtensionState.HEALTHY)
    assert len(state.history) <= LIMITS.recent_errors
    assert state.history[-1][0] == ExtensionState.HEALTHY
