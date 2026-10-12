"""Tests for extension lifecycle state machine, owner channels, and manager."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import anyio
import pytest

from core.extensions.manager import (
    LEGAL_TRANSITIONS,
    ExtensionLifecycleManager,
    ExtensionLifecycleStateMachine,
    ExtensionOwner,
    IllegalStateTransitionError,
    OwnerSignal,
    RestartBudget,
    SessionReusedError,
)
from core.extensions.models import ExtensionDefinition, ExtensionState, ExtensionToolDescriptor
from core.platform import normalize_system


def _dummy_definition(extension_id: str = "test-ext", enabled: bool = True) -> ExtensionDefinition:
    return ExtensionDefinition.model_validate_json(
        json.dumps({
            "extension_id": extension_id,
            "display_name": "Test Extension",
            "provider": "fixture",
            "command": "python",
            "working_directory": ".",
            "platforms": [normalize_system()],
            "enabled": enabled,
            "launch_approved": True,
            "trust_class": "trusted",
        })
    )


@pytest.mark.parametrize("override", [{"launch_approved": False}, {"trust_class": "untrusted"}])
def test_unapproved_owner_never_runs_on_start_or_restart(tmp_path: Path, override: dict[str, Any]) -> None:
    definition = _dummy_definition().model_copy(update={"launch_approved": True, "trust_class": "trusted", **override})
    owner = ExtensionOwner(definition)
    marker = tmp_path / "unapproved-provider-ran"

    async def runner(own: ExtensionOwner) -> None:
        marker.write_text("executed", encoding="utf-8")

    async def scenario() -> None:
        owner.signal(OwnerSignal.START)
        owner.signal(OwnerSignal.RESTART)
        owner.signal(OwnerSignal.STOP)
        await owner.run_owner_loop(runner_fn=runner)

    anyio.run(scenario)
    assert not marker.exists()


def test_direct_connection_requires_launch_approval_before_transport(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from core.errors import ToolError
    from core.extensions import manager as module

    definition = _dummy_definition().model_copy(update={"launch_approved": False, "trust_class": "trusted"})
    owner = ExtensionOwner(definition)
    opened = False

    def forbidden_transport(*args: Any, **kwargs: Any) -> Any:
        nonlocal opened
        opened = True
        raise AssertionError("transport was entered before operator approval")

    monkeypatch.setattr(module, "bounded_stdio_transport", forbidden_transport)

    async def scenario() -> None:
        with pytest.raises(ToolError) as denied:
            await module.connect_and_discover_tools(owner)
        assert denied.value.code == "launch_not_approved"

    anyio.run(scenario)
    assert not opened


def test_transition_matrix_all_legal_transitions() -> None:
    """Verify that every transition in LEGAL_TRANSITIONS succeeds."""
    expected_transitions = [
        (ExtensionState.CONFIGURED, ExtensionState.DISABLED),
        (ExtensionState.CONFIGURED, ExtensionState.STARTING),
        (ExtensionState.DISABLED, ExtensionState.STARTING),
        (ExtensionState.STARTING, ExtensionState.HEALTHY),
        (ExtensionState.STARTING, ExtensionState.FAILED),
        (ExtensionState.STARTING, ExtensionState.STOPPING),
        (ExtensionState.HEALTHY, ExtensionState.DEGRADED),
        (ExtensionState.HEALTHY, ExtensionState.UNREACHABLE),
        (ExtensionState.HEALTHY, ExtensionState.STOPPING),
        (ExtensionState.DEGRADED, ExtensionState.HEALTHY),
        (ExtensionState.DEGRADED, ExtensionState.UNREACHABLE),
        (ExtensionState.DEGRADED, ExtensionState.STOPPING),
        (ExtensionState.UNREACHABLE, ExtensionState.STARTING),
        (ExtensionState.UNREACHABLE, ExtensionState.FAILED),
        (ExtensionState.UNREACHABLE, ExtensionState.STOPPING),
        (ExtensionState.FAILED, ExtensionState.STARTING),
        (ExtensionState.STOPPING, ExtensionState.DISABLED),
        (ExtensionState.STOPPING, ExtensionState.CONFIGURED),
    ]

    for source, target in expected_transitions:
        sm = ExtensionLifecycleStateMachine("test", initial_state=source)
        assert sm.can_transition(target), f"Expected legal transition {source} -> {target}"
        sm.transition(target)
        assert sm.state == target


def test_transition_matrix_illegal_transitions_raise() -> None:
    """Verify that any transition outside Section 21 raises IllegalStateTransitionError."""
    illegal_cases = [
        (ExtensionState.CONFIGURED, ExtensionState.HEALTHY),
        (ExtensionState.CONFIGURED, ExtensionState.DEGRADED),
        (ExtensionState.CONFIGURED, ExtensionState.UNREACHABLE),
        (ExtensionState.CONFIGURED, ExtensionState.FAILED),
        (ExtensionState.CONFIGURED, ExtensionState.STOPPING),
        (ExtensionState.DISABLED, ExtensionState.HEALTHY),
        (ExtensionState.DISABLED, ExtensionState.CONFIGURED),
        (ExtensionState.DISABLED, ExtensionState.STOPPING),
        (ExtensionState.STARTING, ExtensionState.CONFIGURED),
        (ExtensionState.STARTING, ExtensionState.DISABLED),
        (ExtensionState.STARTING, ExtensionState.DEGRADED),
        (ExtensionState.HEALTHY, ExtensionState.CONFIGURED),
        (ExtensionState.HEALTHY, ExtensionState.STARTING),
        (ExtensionState.HEALTHY, ExtensionState.FAILED),
        (ExtensionState.FAILED, ExtensionState.HEALTHY),
        (ExtensionState.FAILED, ExtensionState.STOPPING),
        (ExtensionState.FAILED, ExtensionState.DEGRADED),
        (ExtensionState.STOPPING, ExtensionState.HEALTHY),
        (ExtensionState.STOPPING, ExtensionState.STARTING),
    ]

    for source, target in illegal_cases:
        sm = ExtensionLifecycleStateMachine("test", initial_state=source)
        assert not sm.can_transition(target)
        with pytest.raises(IllegalStateTransitionError):
            sm.transition(target)
        # Verify state did not change
        assert sm.state == source


def test_configured_is_not_healthy_or_routable() -> None:
    """Verify core requirement: CONFIGURED != HEALTHY, and only HEALTHY is routable."""
    sm = ExtensionLifecycleStateMachine("test", initial_state=ExtensionState.CONFIGURED)
    assert not sm.is_healthy
    assert not sm.is_routable

    # Transition through states
    sm.transition(ExtensionState.STARTING)
    assert not sm.is_healthy
    assert not sm.is_routable

    sm.transition(ExtensionState.HEALTHY)
    assert sm.is_healthy
    assert sm.is_routable

    sm.transition(ExtensionState.DEGRADED)
    assert not sm.is_healthy
    assert not sm.is_routable

    sm.transition(ExtensionState.UNREACHABLE)
    assert not sm.is_healthy
    assert not sm.is_routable

    sm.transition(ExtensionState.FAILED)
    assert not sm.is_healthy
    assert not sm.is_routable


def test_generation_increments_on_starting() -> None:
    """Verify that generation monotonically increments upon entering STARTING."""
    sm = ExtensionLifecycleStateMachine("test", initial_state=ExtensionState.CONFIGURED)
    assert sm.generation == 0

    sm.transition(ExtensionState.STARTING)
    assert sm.generation == 1

    sm.transition(ExtensionState.HEALTHY)
    assert sm.generation == 1

    sm.transition(ExtensionState.UNREACHABLE)
    sm.transition(ExtensionState.STARTING)
    assert sm.generation == 2


def test_single_use_session_rule() -> None:
    """Verify that an active session cannot be reused once closed or retired."""
    owner = ExtensionOwner(_dummy_definition("ext-1"))
    session_mock = MagicMock()

    # First use succeeds
    owner.register_session(session_mock, "session-123")

    # Closing active session
    owner.close_active_session()

    # Attempting to reuse same session ID fails closed
    with pytest.raises(SessionReusedError):
        owner.register_session(session_mock, "session-123")

    # A different session ID succeeds
    session_mock2 = MagicMock()
    owner.register_session(session_mock2, "session-456")


def test_coalesce_duplicate_start_signals() -> None:
    """Verify that duplicate START signals are coalesced when already starting/running."""
    owner = ExtensionOwner(_dummy_definition("ext-1"))
    assert owner.state == ExtensionState.CONFIGURED

    # First start signal succeeds
    assert owner.signal(OwnerSignal.START) is True

    # Simulate transition to STARTING
    owner.state_machine.transition(ExtensionState.STARTING)

    # Subsequent start signal is coalesced
    assert owner.signal(OwnerSignal.START) is False

    # Simulate transition to HEALTHY
    owner.state_machine.transition(ExtensionState.HEALTHY)
    assert owner.signal(OwnerSignal.START) is False

    # Simulate transition to DEGRADED
    owner.state_machine.transition(ExtensionState.DEGRADED)
    assert owner.signal(OwnerSignal.START) is False


def test_owner_actor_event_loop_start_and_stop() -> None:
    """Verify owner actor task processes START, transitions to HEALTHY, and STOP cleanly."""
    definition = _dummy_definition("ext-actor")
    owner = ExtensionOwner(definition)
    stop_event = anyio.Event()

    async def mock_runner(own: ExtensionOwner) -> None:
        own.state_machine.transition(ExtensionState.HEALTHY)

    async def scenario() -> None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(owner.run_owner_loop, mock_runner, stop_event)

            # Signal start
            assert owner.signal(OwnerSignal.START) is True
            # Allow actor to process
            await anyio.sleep(0.05)
            assert owner.state == ExtensionState.HEALTHY
            assert owner.is_routable

            # Signal stop
            assert owner.signal(OwnerSignal.STOP) is True
            await stop_event.wait()
            assert owner.state in {ExtensionState.CONFIGURED, ExtensionState.DISABLED}
            assert not owner.is_routable

    anyio.run(scenario)


def test_owner_actor_restart_cycle() -> None:
    """Verify owner actor handles RESTART signal cleanly."""
    definition = _dummy_definition("ext-restart")
    owner = ExtensionOwner(definition)
    stop_event = anyio.Event()
    starts_count = 0

    async def mock_runner(own: ExtensionOwner) -> None:
        nonlocal starts_count
        starts_count += 1
        own.state_machine.transition(ExtensionState.HEALTHY)

    async def scenario() -> None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(owner.run_owner_loop, mock_runner, stop_event)

            # Initial start
            owner.signal(OwnerSignal.START)
            await anyio.sleep(0.05)
            assert owner.state == ExtensionState.HEALTHY
            assert starts_count == 1
            assert owner.state_machine.generation == 1

            # Signal restart
            owner.signal(OwnerSignal.RESTART)
            await anyio.sleep(0.05)
            assert owner.state == ExtensionState.HEALTHY
            assert starts_count == 2
            assert owner.state_machine.generation == 2
            assert owner.state_machine.restart_count == 1

            # Teardown
            owner.signal(OwnerSignal.STOP)
            await stop_event.wait()

    anyio.run(scenario)


def test_manager_coordinates_multiple_extensions() -> None:
    """Verify ExtensionLifecycleManager coordinates multiple extensions independently."""
    manager = ExtensionLifecycleManager()
    ext_a = _dummy_definition("ext-a", enabled=True)
    ext_b = _dummy_definition("ext-b", enabled=False)

    manager.register_extension(ext_a)
    manager.register_extension(ext_b)

    assert manager.get_state("ext-a") == ExtensionState.CONFIGURED
    assert manager.get_state("ext-b") == ExtensionState.DISABLED

    assert not manager.is_routable("ext-a")
    assert not manager.is_routable("ext-b")
    assert not manager.is_healthy("ext-a")
    assert not manager.is_healthy("ext-b")

    # Unknown extension
    assert manager.get_state("unknown") is None
    assert not manager.is_routable("unknown")
    assert not manager.signal_start("unknown")

    # Signal start for ext_a
    assert manager.signal_start("ext-a") is True


def test_exhaustive_pairwise_illegal_transitions() -> None:
    """Check every illegal state pair in the 8x8 matrix raises IllegalStateTransitionError."""
    states = list(ExtensionState)
    illegal_count = 0
    for s1 in states:
        for s2 in states:
            if s2 not in LEGAL_TRANSITIONS.get(s1, frozenset()):
                sm = ExtensionLifecycleStateMachine("test", initial_state=s1)
                assert not sm.can_transition(s2)
                with pytest.raises(IllegalStateTransitionError):
                    sm.transition(s2)
                assert sm.state == s1
                illegal_count += 1
    # Total pairs: 64, legal: 18, illegal: 46
    assert illegal_count == 46


def test_all_states_healthy_and_routable_strictness() -> None:
    """Verify that only HEALTHY is routable and healthy; all 7 other states are false."""
    for s in ExtensionState:
        sm = ExtensionLifecycleStateMachine("test", initial_state=s)
        if s == ExtensionState.HEALTHY:
            assert sm.is_healthy is True
            assert sm.is_routable is True
        else:
            assert sm.is_healthy is False, f"State {s} should not be healthy"
            assert sm.is_routable is False, f"State {s} should not be routable"


def test_owner_actor_runner_failure_transitions_to_failed() -> None:
    """Verify that an exception in the runner task transitions the owner to FAILED with error recorded."""
    definition = _dummy_definition("ext-failed")
    owner = ExtensionOwner(definition)
    stop_event = anyio.Event()

    async def failing_runner(own: ExtensionOwner) -> None:
        raise RuntimeError("Subprocess connection failed")

    async def scenario() -> None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(owner.run_owner_loop, failing_runner, stop_event)

            owner.signal(OwnerSignal.START)
            await anyio.sleep(0.05)
            assert owner.state == ExtensionState.FAILED
            assert owner.state_machine.last_error == "Subprocess connection failed"
            assert not owner.is_routable

            owner.signal(OwnerSignal.STOP)
            await stop_event.wait()

    anyio.run(scenario)


def test_slow_ab_concurrency_and_isolated_failure() -> None:
    """Verify slow A/B: Provider A fails/times out, Provider B succeeds, native unaffected."""
    manager = ExtensionLifecycleManager()
    ext_a = _dummy_definition("ext-slow-a", enabled=True)
    ext_b = _dummy_definition("ext-fast-b", enabled=True)

    owner_a = manager.register_extension(ext_a)
    owner_b = manager.register_extension(ext_b)

    async def slow_failing_runner(own: ExtensionOwner) -> None:
        await anyio.sleep(0.02)
        raise TimeoutError("Provider A startup timed out")

    async def fast_succeeding_runner(own: ExtensionOwner) -> None:
        await anyio.sleep(0.01)
        own.state_machine.transition(ExtensionState.HEALTHY)

    async def scenario() -> None:
        async with anyio.create_task_group() as tg:
            await manager.start_all_background(
                tg,
                custom_runners={
                    "ext-slow-a": slow_failing_runner,
                    "ext-fast-b": fast_succeeding_runner,
                },
            )
            await anyio.sleep(0.1)

            # Assert Provider A failed in isolation
            assert owner_a.state == ExtensionState.FAILED
            assert not owner_a.is_routable

            # Assert Provider B succeeded and is healthy
            assert owner_b.state == ExtensionState.HEALTHY
            assert owner_b.is_routable

            # Teardown
            owner_a.signal(OwnerSignal.STOP)
            owner_b.signal(OwnerSignal.STOP)

    anyio.run(scenario)


def test_tools_list_no_spawn_queries_memory_snapshot() -> None:
    """Verify listing tools queries cached registry snapshot without spawning subprocesses."""
    manager = ExtensionLifecycleManager()
    ext = _dummy_definition("ext-mem", enabled=True)
    manager.register_extension(ext)

    # Calling list_tools on manager performs zero spawns and returns in-memory descriptors
    tools = manager.list_tools()
    assert isinstance(tools, tuple)
    assert len(tools) == 0

    snapshot = manager.get_catalog_snapshot()
    assert snapshot.total_tools == 0
    assert snapshot.manager_generation == 0


def test_disabled_extensions_are_never_started() -> None:
    """Verify that disabled extensions are skipped by background startup and stay DISABLED."""
    manager = ExtensionLifecycleManager()
    ext_enabled = _dummy_definition("ext-active", enabled=True)
    ext_disabled = _dummy_definition("ext-inactive", enabled=False)

    owner_enabled = manager.register_extension(ext_enabled)
    owner_disabled = manager.register_extension(ext_disabled)

    assert owner_disabled.state == ExtensionState.DISABLED

    async def dummy_runner(own: ExtensionOwner) -> None:
        own.state_machine.transition(ExtensionState.HEALTHY)

    async def scenario() -> None:
        async with anyio.create_task_group() as tg:
            await manager.start_all_background(
                tg,
                custom_runners={
                    "ext-active": dummy_runner,
                    "ext-inactive": dummy_runner,
                },
            )
            await anyio.sleep(0.05)

            assert owner_enabled.state == ExtensionState.HEALTHY
            # Disabled extension must NEVER have run
            assert owner_disabled.state == ExtensionState.DISABLED
            assert not owner_disabled.is_routable

            owner_enabled.signal(OwnerSignal.STOP)

    anyio.run(scenario)


def test_real_fixture_mcp_discovery_integration(tmp_path: Path) -> None:
    """Verify real fake_mcp_server subprocess handshake, discovery, and catalog registration."""
    import sys

    from core.extensions.manager import connect_and_discover_tools

    fixture_path = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_server.py"

    definition = ExtensionDefinition.model_validate_json(
        json.dumps({
            "extension_id": "fixture-server",
            "display_name": "Fixture Server",
            "provider": "fixture",
            "command": sys.executable,
            "arguments": [str(fixture_path), "--workspace", str(tmp_path), "--mode", "readonly"],
            "working_directory": str(tmp_path),
            "platforms": [normalize_system()],
            "environment": {
                "values": {
                    "PYTHONUNBUFFERED": "1",
                }
            },
            "enabled": True,
            "launch_approved": True,
            "trust_class": "trusted",
        })
    )

    for p in ("home", "config", "state", "temp"):
        (tmp_path / p).mkdir(parents=True, exist_ok=True)

    extra_env = {
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
        "APPDATA": str(tmp_path / "config"),
        "LOCALAPPDATA": str(tmp_path / "state"),
        "TEMP": str(tmp_path / "temp"),
        "TMP": str(tmp_path / "temp"),
    }

    manager = ExtensionLifecycleManager()
    owner = manager.register_extension(definition)
    owner.state_machine.transition(ExtensionState.STARTING)

    async def scenario() -> None:
        descriptors = await connect_and_discover_tools(
            owner,
            manager.registry,
            extra_env=extra_env,
            startup_timeout=5.0,
            handshake_timeout=3.0,
            discovery_timeout=3.0,
        )
        assert len(descriptors) == 2
        names = {d.public_name for d in descriptors}
        assert "external.fixture-server.read_value" in names
        assert "external.fixture-server.increment_value" in names

        # State must now be HEALTHY after accepted catalog publication
        assert owner.state == ExtensionState.HEALTHY
        assert owner.is_routable

        # Catalog snapshot must reflect the discovered tools
        snapshot = manager.get_catalog_snapshot()
        assert snapshot.total_tools == 2
        assert manager.registry.has_tool("external.fixture-server.read_value")

    anyio.run(scenario)


def test_restart_budget_sliding_window_and_backoff() -> None:
    """Verify RestartBudget sliding window, exponential backoff, and circuit breaker."""
    current_time = 1000.0

    def fake_clock() -> float:
        return current_time

    budget = RestartBudget(
        max_attempts=3,
        window_sec=600.0,
        base_backoff_sec=0.5,
        max_backoff_sec=30.0,
        clock=fake_clock,
    )

    assert budget.can_restart()
    assert budget.attempt_count == 0

    # 1st attempt: 0.0 backoff on first restart from healthy
    b1 = budget.record_attempt()
    assert b1 == 0.0
    assert budget.consecutive_failures == 1
    assert budget.attempt_count == 1
    assert budget.can_restart()

    # 2nd attempt: base backoff (0.5s)
    current_time += 10.0
    b2 = budget.record_attempt()
    assert b2 == 0.5
    assert budget.consecutive_failures == 2
    assert budget.attempt_count == 2
    assert budget.can_restart()

    # 3rd attempt: 2x base backoff (1.0s)
    current_time += 10.0
    b3 = budget.record_attempt()
    assert b3 == 1.0
    assert budget.consecutive_failures == 3
    assert budget.attempt_count == 3

    # 4th attempt within same window: budget exhausted, trips circuit breaker
    assert not budget.can_restart()
    assert budget.circuit_breaker_tripped

    # Advance clock past 600s window: budget prunes old attempts and is available again
    current_time += 601.0
    assert budget.can_restart()
    assert budget.attempt_count == 0

    # Manual reset clears circuit breaker and counters
    budget.record_attempt()
    assert budget.attempt_count == 1
    budget.manual_reset()
    assert budget.attempt_count == 0
    assert budget.consecutive_failures == 0
    assert not budget.circuit_breaker_tripped


def test_disable_extension_closes_admission_and_retires_tools() -> None:
    """Verify disable_extension closes admission first, retires catalog, and ensures disabled is never callable."""
    manager = ExtensionLifecycleManager()
    definition = _dummy_definition("ext-disable-test", enabled=True)
    owner = manager.register_extension(definition)
    owner.state_machine.transition(ExtensionState.STARTING)

    tool = ExtensionToolDescriptor.create(
        extension_id="ext-disable-test",
        provider="fixture",
        remote_name="dummy_tool",
        description="Dummy tool description",
        input_schema={"type": "object"},
        catalog_generation=owner.generation,
    )
    manager.registry.publish_extension_catalog("ext-disable-test", owner.generation, (tool,))
    owner.state_machine.transition(ExtensionState.HEALTHY)

    assert owner.is_routable
    assert manager.is_routable("ext-disable-test")
    assert manager.registry.has_tool("external.ext-disable-test.dummy_tool")
    assert len(manager.list_tools()) == 1

    lease = manager.registry.create_lease("external.ext-disable-test.dummy_tool")
    assert lease is not None
    assert manager.registry.is_lease_valid(lease)

    async def scenario() -> None:
        ok = await manager.disable_extension("ext-disable-test")
        assert ok

        # Admission closed immediately
        assert owner.state == ExtensionState.DISABLED
        assert not owner.is_routable
        assert not manager.is_routable("ext-disable-test")

        # Catalog retired: disabled is never callable
        assert not manager.registry.has_tool("external.ext-disable-test.dummy_tool")
        assert len(manager.list_tools()) == 0

        # Old lease is immediately invalid
        assert not manager.registry.is_lease_valid(lease)

    anyio.run(scenario)


def test_crash_loop_stops_and_trips_circuit_breaker() -> None:
    """Verify that repeated failures exhaust restart budget and stop crash-loop via circuit breaker."""
    budget = RestartBudget(max_attempts=3, window_sec=600.0, base_backoff_sec=0.01)
    definition = _dummy_definition("ext-crash-test", enabled=True)
    owner = ExtensionOwner(definition, restart_budget=budget)

    attempts_executed = 0

    async def failing_runner(own: ExtensionOwner) -> None:
        nonlocal attempts_executed
        attempts_executed += 1
        raise RuntimeError(f"Crashing runner attempt {attempts_executed}")

    async def fast_sleep(_: float) -> None:
        return

    async def scenario() -> None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                owner.run_owner_loop,
                failing_runner,
                None,
                fast_sleep,
            )
            owner.signal(OwnerSignal.START)
            await anyio.sleep(0.02)
            assert owner.state == ExtensionState.FAILED

            # Restart 1
            owner.signal(OwnerSignal.RESTART)
            await anyio.sleep(0.02)
            assert owner.state == ExtensionState.FAILED

            # Restart 2
            owner.signal(OwnerSignal.RESTART)
            await anyio.sleep(0.02)
            assert owner.state == ExtensionState.FAILED

            # Restart 3
            owner.signal(OwnerSignal.RESTART)
            await anyio.sleep(0.02)
            assert owner.state == ExtensionState.FAILED

            # 4th restart: budget exhausted, should NOT restart
            assert not budget.can_restart()
            assert budget.circuit_breaker_tripped
            assert budget.attempt_count == 3
            assert owner.state == ExtensionState.FAILED

            owner.signal(OwnerSignal.STOP)

    anyio.run(scenario)


def test_transport_loss_reconnect_and_budget_exhaustion() -> None:
    """Verify handle_transport_loss triggers reconnect within budget and transitions to FAILED when exhausted."""
    manager = ExtensionLifecycleManager()
    budget = RestartBudget(max_attempts=2, window_sec=600.0, base_backoff_sec=0.01)
    definition = _dummy_definition("ext-loss-test", enabled=True)
    owner = manager.register_extension(definition, restart_budget=budget)
    owner.state_machine.transition(ExtensionState.STARTING)
    owner.state_machine.transition(ExtensionState.HEALTHY)

    # 1st transport loss: within budget, signals RESTART
    reconnect_signaled = owner.handle_transport_loss(registry=manager.registry)
    assert reconnect_signaled
    assert owner.state == ExtensionState.UNREACHABLE
    assert not owner.is_routable

    # Consume attempts
    budget.record_attempt()
    budget.record_attempt()
    assert not budget.can_restart()

    # Reset to HEALTHY
    owner.state_machine.transition(ExtensionState.STARTING)
    owner.state_machine.transition(ExtensionState.HEALTHY)

    # 2nd transport loss: budget exhausted, transitions directly to FAILED, does NOT reconnect
    reconnect_signaled_2 = owner.handle_transport_loss(registry=manager.registry)
    assert not reconnect_signaled_2
    assert owner.state == ExtensionState.FAILED
    assert budget.circuit_breaker_tripped


def test_fresh_session_and_generation_on_reconnect() -> None:
    """Verify reconnect assigns fresh generation and enforces single-use session semantics."""
    definition = _dummy_definition("ext-fresh-test", enabled=True)
    owner = ExtensionOwner(definition)

    gen_1 = owner.generation
    session_mock_1 = MagicMock()
    owner.state_machine.transition(ExtensionState.STARTING)
    gen_starting_1 = owner.generation
    assert gen_starting_1 == gen_1 + 1

    owner.register_session(session_mock_1, "session-1")
    owner.state_machine.transition(ExtensionState.HEALTHY)

    # Reconnect/restart
    owner.state_machine.transition(ExtensionState.STOPPING)
    owner.close_active_session()
    owner.state_machine.transition(ExtensionState.CONFIGURED)
    owner.state_machine.transition(ExtensionState.STARTING)
    gen_starting_2 = owner.generation
    assert gen_starting_2 == gen_starting_1 + 1

    # Attempting to register old session ID raises SessionReusedError
    with pytest.raises(SessionReusedError):
        owner.register_session(session_mock_1, "session-1")

    # Fresh session ID succeeds
    session_mock_2 = MagicMock()
    owner.register_session(session_mock_2, "session-2")
    assert owner.generation == 2


def test_drain_and_stop_closes_admission_fires_hooks_and_transitions_owners() -> None:
    """Verify drain_and_stop closes admission first, fires unresolved hooks, and transitions states."""
    manager = ExtensionLifecycleManager()
    definition = _dummy_definition("ext-drain-test", enabled=True)
    owner = manager.register_extension(definition)
    owner.state_machine.transition(ExtensionState.STARTING)

    tool = ExtensionToolDescriptor.create(
        extension_id="ext-drain-test",
        provider="fixture",
        remote_name="dummy_tool",
        description="Dummy tool description",
        input_schema={"type": "object"},
        catalog_generation=owner.generation,
    )
    manager.registry.publish_extension_catalog("ext-drain-test", owner.generation, (tool,))
    owner.state_machine.transition(ExtensionState.HEALTHY)

    # Register session mock
    session_mock = MagicMock()
    owner.register_session(session_mock, "session-drain-1")

    # Track hook invocations
    hook_calls: list[str] = []
    manager.register_unresolved_mutation_hook(lambda reason: hook_calls.append(reason))

    # Verify initial healthy routable state
    assert manager.is_routable("ext-drain-test")
    assert len(manager.list_tools()) == 1
    assert owner._active_session is not None

    async def scenario() -> None:
        await manager.drain_and_stop(timeout_sec=0.5)

        # 1. Admission closed
        assert not manager.is_routable("ext-drain-test")
        assert len(manager.list_tools()) == 0
        assert not manager.registry.has_tool("external.ext-drain-test.dummy_tool")

        # 2. Hook was called
        assert hook_calls == ["runtime_shutdown"]

        # 3. Transitions to CONFIGURED (enabled)
        assert owner.state == ExtensionState.CONFIGURED
        assert owner._active_session is None

    anyio.run(scenario)


def test_drain_and_stop_stops_running_background_extensions() -> None:
    """Verify drain_and_stop unblocks and cleanly stops background owner loops."""
    manager = ExtensionLifecycleManager()
    definition = _dummy_definition("ext-bg-drain", enabled=True)
    owner = manager.register_extension(definition)

    ready_event = anyio.Event()

    async def runner(own: ExtensionOwner) -> None:
        own.state_machine.transition(ExtensionState.HEALTHY)
        ready_event.set()
        await manager._shutdown_event.wait()

    async def scenario() -> None:
        async def run_bg() -> None:
            await manager.start_all_async(custom_runners={"ext-bg-drain": runner})

        async with anyio.create_task_group() as tg:
            tg.start_soon(run_bg)
            await ready_event.wait()
            assert owner.state == ExtensionState.HEALTHY
            assert owner.is_running

            # Now drain and stop
            await manager.drain_and_stop(timeout_sec=1.0)
            assert not owner.is_running
            assert owner.state == ExtensionState.CONFIGURED

    anyio.run(scenario)


def test_lifespan_integration_with_extensions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify heartbeat lifespan stop order coordinates RUNTIME_LIFECYCLE, extension drain, and task cancel."""
    from dataclasses import replace

    from core import heartbeat
    from core.lifecycle import RuntimeLifecycle

    stop_events_order: list[str] = []

    # Mock lifecycle to record mark_stopping
    mock_lifecycle = RuntimeLifecycle()
    original_mark = mock_lifecycle.mark_stopping

    def tracked_mark_stopping() -> None:
        stop_events_order.append("mark_stopping")
        original_mark()

    monkeypatch.setattr(mock_lifecycle, "mark_stopping", tracked_mark_stopping)
    monkeypatch.setattr(heartbeat, "RUNTIME_LIFECYCLE", mock_lifecycle)

    # Settings mock with extensions enabled
    monkeypatch.setattr(
        heartbeat, "SETTINGS", replace(heartbeat.SETTINGS, state_dir=tmp_path, extensions_enabled=True)
    )
    for name in (
        "schedule_artifact_retention",
        "schedule_job_history_retention",
        "schedule_workflow_history_retention",
        "flush_audit",
    ):
        monkeypatch.setattr(heartbeat, name, lambda **kwargs: None)
    monkeypatch.setattr(heartbeat.BACKUP_RETENTION, "schedule", lambda: None)
    monkeypatch.setattr(heartbeat, "flush_audit", lambda: stop_events_order.append("flush_audit"))

    # Mock extension manager
    class FakeExtensionManager:
        def __init__(self) -> None:
            self.started = False
            self.drained = False

        async def start_all_async(self) -> None:
            self.started = True
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                pass

        async def drain_and_stop(self, timeout_sec: float = 5.0) -> None:
            stop_events_order.append("drain_and_stop")
            self.drained = True

    class FakeServer:
        def __init__(self) -> None:
            self.extension_manager = FakeExtensionManager()

    server = FakeServer()

    async def scenario() -> None:
        async with heartbeat.lifespan(server):
            await asyncio.sleep(0.05)
            assert server.extension_manager.started

    asyncio.run(scenario())

    # Stop order must be: mark_stopping -> drain_and_stop -> flush_audit
    assert stop_events_order == ["mark_stopping", "drain_and_stop", "flush_audit"]
    assert server.extension_manager.drained


def test_lifespan_native_zero_extension_compatibility(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify lifespan without extensions enabled has zero impact and leaves server untouched."""
    from dataclasses import replace

    from core import heartbeat

    monkeypatch.setattr(
        heartbeat, "SETTINGS", replace(heartbeat.SETTINGS, state_dir=tmp_path, extensions_enabled=False)
    )
    for name in (
        "schedule_artifact_retention",
        "schedule_job_history_retention",
        "schedule_workflow_history_retention",
        "flush_audit",
    ):
        monkeypatch.setattr(heartbeat, name, lambda **kwargs: None)
    monkeypatch.setattr(heartbeat.BACKUP_RETENTION, "schedule", lambda: None)

    class PlainServer:
        pass

    server = PlainServer()

    async def scenario() -> None:
        async with heartbeat.lifespan(server):
            await asyncio.sleep(0.05)
            assert getattr(server, "extension_manager", None) is None

    asyncio.run(scenario())
    assert getattr(server, "extension_manager", None) is None

