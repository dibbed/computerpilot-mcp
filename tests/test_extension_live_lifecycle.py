from __future__ import annotations

import json
from pathlib import Path

import anyio
import psutil
import pytest
from test_extension_refresh import configured_manager

from core.errors import ToolError
from core.extensions.manager import RestartBudget, connect_and_discover_tools
from core.extensions.models import ExtensionState


def test_disable_reaps_a_real_live_provider_before_runtime_shutdown(tmp_path: Path) -> None:
    manager = configured_manager(tmp_path, "readonly", starting=False)
    owner = manager.get_owner("refresh-fixture")
    assert owner is not None

    async def scenario() -> None:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(manager.start_all_async)
            try:
                with anyio.fail_after(5):
                    while owner.state != ExtensionState.HEALTHY:
                        await anyio.sleep(0.01)
                process = json.loads((tmp_path / "root_process").read_text(encoding="utf-8"))
                assert await manager.disable_extension(owner.extension_id)
                with anyio.fail_after(3):
                    while owner.is_running or psutil.pid_exists(process["pid"]):
                        await anyio.sleep(0.01)
                assert owner.state == ExtensionState.DISABLED
                assert not manager.list_tools()
            finally:
                await manager.drain_and_stop(timeout_sec=3)
                tasks.cancel_scope.cancel()

    anyio.run(scenario)


def test_operator_revocation_during_startup_queue_prevents_process_launch(tmp_path: Path) -> None:
    manager = configured_manager(tmp_path, "readonly")
    owner = manager.get_owner("refresh-fixture")
    assert owner is not None

    async def scenario() -> None:
        gate = anyio.Semaphore(1)
        await gate.acquire()

        async def launch() -> None:
            with pytest.raises(ToolError):
                await connect_and_discover_tools(owner, manager.registry, startup_gate=gate)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(launch)
            await anyio.wait_all_tasks_blocked()
            owner.is_enabled = False
            gate.release()
        assert not (tmp_path / "root_process").exists()

    anyio.run(scenario)


def test_completed_shutdown_never_starts_a_queued_provider(tmp_path: Path) -> None:
    manager = configured_manager(tmp_path, "readonly", starting=False)

    async def scenario() -> None:
        await manager.drain_and_stop()
        await manager.start_extension_background("refresh-fixture")
        assert not (tmp_path / "root_process").exists()

    anyio.run(scenario)


def test_operator_startup_deadline_stops_a_real_delayed_provider(tmp_path: Path) -> None:
    manager = configured_manager(tmp_path, "startup_delayed", starting=False)
    owner = manager.get_owner("refresh-fixture")
    assert owner is not None
    owner.definition = owner.definition.model_copy(update={
        "timeouts": owner.definition.timeouts.model_copy(update={"startup_sec": 0.1}),
    })

    async def scenario() -> None:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(manager.start_all_async)
            try:
                with anyio.fail_after(5):
                    while owner.state in {ExtensionState.CONFIGURED, ExtensionState.STARTING}:
                        await anyio.sleep(0.01)
                assert owner.state == ExtensionState.FAILED
                assert not manager.list_tools()
                assert owner._active_session is None
                marker = tmp_path / "root_process"
                if marker.exists():
                    assert not psutil.pid_exists(json.loads(marker.read_text(encoding="utf-8"))["pid"])
            finally:
                await manager.drain_and_stop(timeout_sec=3)
                tasks.cancel_scope.cancel()

    anyio.run(scenario)


@pytest.mark.parametrize("deadline", ["startup_timeout", "handshake_timeout", "discovery_timeout"])
def test_invalid_startup_deadlines_never_launch_a_provider(tmp_path: Path, deadline: str) -> None:
    manager = configured_manager(tmp_path, "readonly")
    owner = manager.get_owner("refresh-fixture")
    assert owner is not None
    timeouts = {"startup_timeout": 1.0, "handshake_timeout": 1.0, "discovery_timeout": 1.0}
    timeouts[deadline] = float("nan")

    async def scenario() -> None:
        with pytest.raises(ToolError, match="positive and finite"):
            await connect_and_discover_tools(
                owner, manager.registry, startup_timeout=timeouts["startup_timeout"],
                handshake_timeout=timeouts["handshake_timeout"], discovery_timeout=timeouts["discovery_timeout"],
            )
        assert not (tmp_path / "root_process").exists()

    anyio.run(scenario)


def test_real_transport_loss_reconnects_once_without_replaying_the_call(tmp_path: Path) -> None:
    manager = configured_manager(tmp_path, "crash", starting=False)
    owner = manager.get_owner("refresh-fixture")
    assert owner is not None
    owner.restart_budget = RestartBudget(max_attempts=1)

    async def scenario() -> None:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(manager.start_all_async)
            try:
                with anyio.fail_after(5):
                    while owner.state != ExtensionState.HEALTHY:
                        await anyio.sleep(0.01)
                first = json.loads((tmp_path / "root_process").read_text(encoding="utf-8"))
                lease = manager.registry.create_lease("external.refresh-fixture.read_value")
                assert lease is not None
                with pytest.raises(ToolError, match="provider call failed"):
                    await manager.execute_guarded_call(lease, {"value": "only-once"})
                with anyio.fail_after(5):
                    while owner.state != ExtensionState.HEALTHY or owner.state_machine.restart_count != 1:
                        await anyio.sleep(0.01)
                second = json.loads((tmp_path / "root_process").read_text(encoding="utf-8"))
                assert second != first
                assert not psutil.pid_exists(first["pid"])
                current = manager.registry.create_lease("external.refresh-fixture.read_value")
                assert current is not None and current.catalog_generation > lease.catalog_generation
                assert (tmp_path / "calls.jsonl").read_text(encoding="utf-8").count("only-once") == 1
                with pytest.raises(ToolError, match="provider call failed"):
                    await manager.execute_guarded_call(current)
                with anyio.fail_after(5):
                    while owner.state != ExtensionState.FAILED:
                        await anyio.sleep(0.01)
                assert not manager.list_tools()
                assert owner.restart_budget.attempt_count == 1
            finally:
                await manager.drain_and_stop(timeout_sec=3)
                tasks.cancel_scope.cancel()

    anyio.run(scenario)


def test_three_real_providers_can_finish_startup_while_all_remain_alive(tmp_path: Path) -> None:
    manager = configured_manager(tmp_path, "readonly", starting=False)
    original = manager.get_owner("refresh-fixture")
    assert original is not None
    owners = [original]
    for number in (2, 3):
        workspace = tmp_path / str(number)
        workspace.mkdir()
        arguments = list(original.definition.arguments)
        arguments[arguments.index("--workspace") + 1] = str(workspace)
        definition = original.definition.model_copy(update={
            "extension_id": f"refresh-fixture-{number}", "working_directory": str(workspace), "arguments": tuple(arguments),
        })
        owners.append(manager.register_extension(definition))

    async def scenario() -> None:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(manager.start_all_async)
            try:
                with anyio.fail_after(5):
                    while any(owner.state != ExtensionState.HEALTHY for owner in owners):
                        await anyio.sleep(0.01)
                assert all(owner.is_running for owner in owners)
                assert manager.registry.active_snapshot.total_tools == 6
            finally:
                await manager.drain_and_stop(timeout_sec=3)
                tasks.cancel_scope.cancel()

    anyio.run(scenario)
