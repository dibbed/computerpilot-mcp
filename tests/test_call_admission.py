"""Tests for extension call admission guard, semaphores, lease and policy rechecks."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from core.errors import ToolError
from core.extensions.manager import ExtensionLifecycleManager
from core.extensions.models import (
    ExtensionDefinition,
    ExtensionState,
    ExtensionToolDescriptor,
    MutationClass,
    ProviderToolHints,
    ToolPolicy,
)
from core.extensions.registry import ExtensionLease
from core.platform import normalize_system


def _dummy_definition(extension_id: str = "ext-test", enabled: bool = True) -> ExtensionDefinition:
    return ExtensionDefinition.model_validate_json(
        json.dumps({
            "extension_id": extension_id,
            "display_name": "Test Extension",
            "provider": "fixture",
            "command": "python",
            "working_directory": ".",
            "platforms": [normalize_system()],
            "enabled": enabled,
        })
    )


def _create_descriptor(
    extension_id: str,
    remote_name: str,
    generation: int,
    mutation_class: MutationClass = MutationClass.READ_ONLY,
    allow_call: bool = True,
    provider_hints: ProviderToolHints | None = None,
) -> ExtensionToolDescriptor:
    policy = ToolPolicy(
        remote_name=remote_name,
        domain="system",
        mutation_class=mutation_class,
        allow_call=allow_call,
    )
    return ExtensionToolDescriptor.create(
        extension_id=extension_id,
        provider="fixture",
        remote_name=remote_name,
        description=f"Description for {remote_name}",
        input_schema={"type": "object"},
        catalog_generation=generation,
        policy=policy,
        provider_hints=provider_hints or ProviderToolHints(),
    )


def test_admitted_read_only_call_executes_successfully() -> None:
    """Verify healthy, allowed read-only tool is admitted and executed on active session."""
    manager = ExtensionLifecycleManager()
    definition = _dummy_definition("ext-read", enabled=True)
    owner = manager.register_extension(definition)
    owner.state_machine.transition(ExtensionState.STARTING)

    desc = _create_descriptor("ext-read", "get_status", owner.generation)
    manager.registry.publish_extension_catalog("ext-read", owner.generation, (desc,))
    owner.state_machine.transition(ExtensionState.HEALTHY)

    # Attach mock session
    session_mock = MagicMock()
    session_mock.call_tool = AsyncMock(return_value={"status": "all_green"})
    owner.register_session(session_mock, "session-read-1")

    lease = manager.registry.create_lease("external.ext-read.get_status")
    assert lease is not None

    async def scenario() -> None:
        result = await manager.execute_guarded_call(lease, {"verbose": True})
        assert result == {"status": "all_green"}
        session_mock.call_tool.assert_awaited_once_with("get_status", arguments={"verbose": True})

    anyio.run(scenario)


def test_mutation_closed_denial_and_marker_absent(tmp_path: Path) -> None:
    """Verify mutating tool is denied before dispatch and leaves no marker (T02/T07)."""
    manager = ExtensionLifecycleManager()
    definition = _dummy_definition("ext-mut", enabled=True)
    owner = manager.register_extension(definition)
    owner.state_machine.transition(ExtensionState.STARTING)

    desc = _create_descriptor(
        "ext-mut",
        "do_mutation",
        owner.generation,
        mutation_class=MutationClass.LOCAL_MUTATION,
    )
    manager.registry.publish_extension_catalog("ext-mut", owner.generation, (desc,))
    owner.state_machine.transition(ExtensionState.HEALTHY)

    marker_file = tmp_path / "mutation_executed.marker"

    async def mock_call_tool(remote_name: str, arguments: dict) -> dict:
        marker_file.write_text("dispatched", encoding="utf-8")
        return {"ok": True}

    session_mock = MagicMock()
    session_mock.call_tool = AsyncMock(side_effect=mock_call_tool)
    owner.register_session(session_mock, "session-mut-1")

    lease = manager.registry.create_lease("external.ext-mut.do_mutation")
    assert lease is not None

    async def scenario() -> None:
        with pytest.raises(ToolError) as exc_info:
            await manager.execute_guarded_call(lease)

        assert exc_info.value.code == "mutation_withheld"
        assert "withheld until M10" in exc_info.value.message

        # Assert provider was NEVER called and marker file is absent
        assert not marker_file.exists()
        session_mock.call_tool.assert_not_called()

    anyio.run(scenario)


def test_provider_hints_cannot_bypass_mutation_classification(tmp_path: Path) -> None:
    """Verify non-authoritative provider hints (e.g. read_only=True) cannot bypass operator policy."""
    manager = ExtensionLifecycleManager()
    definition = _dummy_definition("ext-bypass", enabled=True)
    owner = manager.register_extension(definition)
    owner.state_machine.transition(ExtensionState.STARTING)

    # Provider hints claim read_only=True, but operator policy is LOCAL_MUTATION
    hints = ProviderToolHints(read_only=True, idempotent=True)
    desc = _create_descriptor(
        "ext-bypass",
        "tricky_tool",
        owner.generation,
        mutation_class=MutationClass.LOCAL_MUTATION,
        provider_hints=hints,
    )
    manager.registry.publish_extension_catalog("ext-bypass", owner.generation, (desc,))
    owner.state_machine.transition(ExtensionState.HEALTHY)

    session_mock = MagicMock()
    session_mock.call_tool = AsyncMock(return_value={"ok": True})
    owner.register_session(session_mock, "session-bypass-1")

    lease = manager.registry.create_lease("external.ext-bypass.tricky_tool")
    assert lease is not None

    async def scenario() -> None:
        # LOCAL_MUTATION denied
        with pytest.raises(ToolError) as exc_info:
            await manager.execute_guarded_call(lease)
        assert exc_info.value.code == "mutation_withheld"

        session_mock.call_tool.assert_not_called()

    anyio.run(scenario)


def test_unknown_mutation_class_denied() -> None:
    """Verify tools with UNKNOWN side effect are denied under mutation-closed gate."""
    manager = ExtensionLifecycleManager()
    definition = _dummy_definition("ext-unk", enabled=True)
    owner = manager.register_extension(definition)
    owner.state_machine.transition(ExtensionState.STARTING)

    desc = _create_descriptor(
        "ext-unk",
        "unknown_tool",
        owner.generation,
        mutation_class=MutationClass.UNKNOWN,
    )
    manager.registry.publish_extension_catalog("ext-unk", owner.generation, (desc,))
    owner.state_machine.transition(ExtensionState.HEALTHY)

    lease = manager.registry.create_lease("external.ext-unk.unknown_tool")
    assert lease is not None

    async def scenario() -> None:
        with pytest.raises(ToolError) as exc_info:
            await manager.execute_guarded_call(lease)
        assert exc_info.value.code == "mutation_withheld"

    anyio.run(scenario)


def test_callbacks_denied() -> None:
    """Verify requesting callbacks is explicitly denied under security policy."""
    manager = ExtensionLifecycleManager()
    definition = _dummy_definition("ext-cb", enabled=True)
    owner = manager.register_extension(definition)
    owner.state_machine.transition(ExtensionState.STARTING)

    desc = _create_descriptor("ext-cb", "cb_tool", owner.generation)
    manager.registry.publish_extension_catalog("ext-cb", owner.generation, (desc,))
    owner.state_machine.transition(ExtensionState.HEALTHY)

    session_mock = MagicMock()
    session_mock.call_tool = AsyncMock(return_value={})
    owner.register_session(session_mock, "session-cb-1")

    lease = manager.registry.create_lease("external.ext-cb.cb_tool")
    assert lease is not None

    async def scenario() -> None:
        with pytest.raises(ToolError) as exc_info:
            await manager.execute_guarded_call(lease, request_callback=True)

        assert exc_info.value.code == "callbacks_denied"
        session_mock.call_tool.assert_not_called()

    anyio.run(scenario)


def test_policy_denied_allow_call_false() -> None:
    """Verify tool with allow_call=False is denied by policy."""
    manager = ExtensionLifecycleManager()
    definition = _dummy_definition("ext-disallowed", enabled=True)
    owner = manager.register_extension(definition)
    owner.state_machine.transition(ExtensionState.STARTING)

    desc = _create_descriptor("ext-disallowed", "blocked_tool", owner.generation, allow_call=False)
    manager.registry.publish_extension_catalog("ext-disallowed", owner.generation, (desc,))
    owner.state_machine.transition(ExtensionState.HEALTHY)

    lease = manager.registry.create_lease("external.ext-disallowed.blocked_tool")
    assert lease is not None

    async def scenario() -> None:
        with pytest.raises(ToolError) as exc_info:
            await manager.execute_guarded_call(lease)

        assert exc_info.value.code == "policy_denied"

    anyio.run(scenario)


def test_unroutable_and_disabled_extension_denied() -> None:
    """Verify unroutable or disabled extensions are rejected immediately."""
    manager = ExtensionLifecycleManager()
    definition = _dummy_definition("ext-state", enabled=True)
    owner = manager.register_extension(definition)
    owner.state_machine.transition(ExtensionState.STARTING)

    desc = _create_descriptor("ext-state", "probe_tool", owner.generation)
    manager.registry.publish_extension_catalog("ext-state", owner.generation, (desc,))

    lease = manager.registry.create_lease("external.ext-state.probe_tool")
    assert lease is not None

    async def scenario() -> None:
        # In STARTING state: not routable
        with pytest.raises(ToolError) as exc_info:
            await manager.execute_guarded_call(lease)
        assert exc_info.value.code == "extension_not_routable"

        # Transition to HEALTHY: now routable
        owner.state_machine.transition(ExtensionState.HEALTHY)
        session_mock = MagicMock()
        session_mock.call_tool = AsyncMock(return_value={"ok": True})
        owner.register_session(session_mock, "session-state-1")

        # Disable extension
        await manager.disable_extension("ext-state")
        with pytest.raises(ToolError) as exc_info:
            await manager.execute_guarded_call(lease)
        assert exc_info.value.code in {"tool_not_found", "stale_generation", "extension_disabled"}

    anyio.run(scenario)


def test_stale_generation_and_invalid_lease_denied() -> None:
    """Verify stale lease from prior generation or invalid lease object is rejected."""
    manager = ExtensionLifecycleManager()
    definition = _dummy_definition("ext-stale", enabled=True)
    owner = manager.register_extension(definition)
    owner.state_machine.transition(ExtensionState.STARTING)

    desc = _create_descriptor("ext-stale", "stale_tool", owner.generation)
    manager.registry.publish_extension_catalog("ext-stale", owner.generation, (desc,))
    owner.state_machine.transition(ExtensionState.HEALTHY)

    valid_lease = manager.registry.create_lease("external.ext-stale.stale_tool")
    assert valid_lease is not None

    # Stale lease with mismatched generation
    stale_lease = ExtensionLease(
        public_name="external.ext-stale.stale_tool",
        manager_generation=valid_lease.manager_generation,
        catalog_generation=valid_lease.catalog_generation + 99,
        schema_fingerprint=valid_lease.schema_fingerprint,
    )

    async def scenario() -> None:
        # Invalid object
        with pytest.raises(ToolError) as exc_info:
            await manager.execute_guarded_call("not-a-lease")  # type: ignore[arg-type]
        assert exc_info.value.code == "invalid_lease"

        # Stale lease
        with pytest.raises(ToolError) as exc_info:
            await manager.execute_guarded_call(stale_lease)
        assert exc_info.value.code == "stale_generation"

    anyio.run(scenario)


def test_per_extension_semaphore_and_isolation() -> None:
    """Verify per-extension concurrency limit and that slow provider A does not block provider B."""
    manager = ExtensionLifecycleManager()

    # Provider A
    def_a = _dummy_definition("ext-a", enabled=True)
    owner_a = manager.register_extension(def_a)
    owner_a.state_machine.transition(ExtensionState.STARTING)
    desc_a = _create_descriptor("ext-a", "slow_tool", owner_a.generation)
    manager.registry.publish_extension_catalog("ext-a", owner_a.generation, (desc_a,))
    owner_a.state_machine.transition(ExtensionState.HEALTHY)

    # Provider B
    def_b = _dummy_definition("ext-b", enabled=True)
    owner_b = manager.register_extension(def_b)
    owner_b.state_machine.transition(ExtensionState.STARTING)
    desc_b = _create_descriptor("ext-b", "fast_tool", owner_b.generation)
    manager.registry.publish_extension_catalog("ext-b", owner_b.generation, (desc_b,))
    owner_b.state_machine.transition(ExtensionState.HEALTHY)

    lease_a = manager.registry.create_lease("external.ext-a.slow_tool")
    lease_b = manager.registry.create_lease("external.ext-b.fast_tool")
    assert lease_a is not None and lease_b is not None

    release_a = anyio.Event()

    async def slow_a_call(name: str, arguments: dict) -> dict:
        await release_a.wait()
        return {"provider": "a"}

    async def fast_b_call(name: str, arguments: dict) -> dict:
        return {"provider": "b"}

    session_a = MagicMock()
    session_a.call_tool = AsyncMock(side_effect=slow_a_call)
    owner_a.register_session(session_a, "sess-a")

    session_b = MagicMock()
    session_b.call_tool = AsyncMock(side_effect=fast_b_call)
    owner_b.register_session(session_b, "sess-b")

    async def scenario() -> None:
        async with anyio.create_task_group() as tg:
            # Calls per extension limit is 2. Launch 2 slow calls to A.
            tg.start_soon(manager.execute_guarded_call, lease_a, None)
            tg.start_soon(manager.execute_guarded_call, lease_a, None)
            await anyio.sleep(0.02)

            # 3rd call to A exceeds per-extension capacity; with short timeout it raises admission_limit_exceeded
            with pytest.raises(ToolError) as exc_info:
                await manager.execute_guarded_call(lease_a, timeout_sec=0.05)
            assert exc_info.value.code == "admission_limit_exceeded"

            # Meanwhile, call to B is NOT blocked by slow A!
            res_b = await manager.execute_guarded_call(lease_b)
            assert res_b == {"provider": "b"}

            # Release A calls to complete task group
            release_a.set()

    anyio.run(scenario)


def test_global_call_semaphore_queue_timeout() -> None:
    """Verify global admission semaphore limits concurrent calls across all extensions."""
    # Create manager with global capacity of 2
    manager = ExtensionLifecycleManager(max_global_calls=2)

    # Provider 1
    def_1 = _dummy_definition("ext-g1", enabled=True)
    owner_1 = manager.register_extension(def_1)
    owner_1.state_machine.transition(ExtensionState.STARTING)
    desc_1 = _create_descriptor("ext-g1", "g1_tool", owner_1.generation)
    manager.registry.publish_extension_catalog("ext-g1", owner_1.generation, (desc_1,))
    owner_1.state_machine.transition(ExtensionState.HEALTHY)

    # Provider 2
    def_2 = _dummy_definition("ext-g2", enabled=True)
    owner_2 = manager.register_extension(def_2)
    owner_2.state_machine.transition(ExtensionState.STARTING)
    desc_2 = _create_descriptor("ext-g2", "g2_tool", owner_2.generation)
    manager.registry.publish_extension_catalog("ext-g2", owner_2.generation, (desc_2,))
    owner_2.state_machine.transition(ExtensionState.HEALTHY)

    lease_1 = manager.registry.create_lease("external.ext-g1.g1_tool")
    lease_2 = manager.registry.create_lease("external.ext-g2.g2_tool")
    assert lease_1 is not None and lease_2 is not None

    release_calls = anyio.Event()

    async def slow_call(name: str, arguments: dict) -> dict:
        await release_calls.wait()
        return {"ok": True}

    session_1 = MagicMock()
    session_1.call_tool = AsyncMock(side_effect=slow_call)
    owner_1.register_session(session_1, "sess-1")

    session_2 = MagicMock()
    session_2.call_tool = AsyncMock(side_effect=slow_call)
    owner_2.register_session(session_2, "sess-2")

    async def scenario() -> None:
        async with anyio.create_task_group() as tg:
            # Consume both global slots (1 on Provider 1, 1 on Provider 2)
            tg.start_soon(manager.execute_guarded_call, lease_1, None)
            tg.start_soon(manager.execute_guarded_call, lease_2, None)
            await anyio.sleep(0.02)

            # 3rd call across any provider exceeds global capacity
            with pytest.raises(ToolError) as exc_info:
                await manager.execute_guarded_call(lease_1, timeout_sec=0.05)
            assert exc_info.value.code == "admission_limit_exceeded"

            release_calls.set()

    anyio.run(scenario)

