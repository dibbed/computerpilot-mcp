"""Lifecycle manager and state machine for isolated MCP extensions."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from enum import Enum
from pathlib import Path
from typing import Any

import anyio
import mcp.types as types
from mcp.client.session import ClientSession

from core.extensions.config import build_extension_environment
from core.extensions.models import (
    LIMITS,
    ExtensionCatalogSnapshot,
    ExtensionDefinition,
    ExtensionState,
    ExtensionToolDescriptor,
)
from core.extensions.registry import CatalogLimitExceededError, ExtensionRegistry
from core.extensions.transport import bounded_stdio_transport

logger = logging.getLogger(__name__)


class IllegalStateTransitionError(ValueError):
    """Raised when an illegal lifecycle state transition is attempted."""


class SessionReusedError(RuntimeError):
    """Raised when an already closed or failed session is attempted to be reused."""


# Exact state transition matrix per Section 21 of the V0.8.0 Master Plan
LEGAL_TRANSITIONS: dict[ExtensionState, frozenset[ExtensionState]] = {
    ExtensionState.CONFIGURED: frozenset({ExtensionState.DISABLED, ExtensionState.STARTING}),
    ExtensionState.DISABLED: frozenset({ExtensionState.STARTING}),
    ExtensionState.STARTING: frozenset({
        ExtensionState.HEALTHY,
        ExtensionState.FAILED,
        ExtensionState.STOPPING,
    }),
    ExtensionState.HEALTHY: frozenset({
        ExtensionState.DEGRADED,
        ExtensionState.UNREACHABLE,
        ExtensionState.STOPPING,
    }),
    ExtensionState.DEGRADED: frozenset({
        ExtensionState.HEALTHY,
        ExtensionState.UNREACHABLE,
        ExtensionState.STOPPING,
    }),
    ExtensionState.UNREACHABLE: frozenset({
        ExtensionState.STARTING,
        ExtensionState.FAILED,
        ExtensionState.STOPPING,
    }),
    ExtensionState.FAILED: frozenset({ExtensionState.STARTING}),
    ExtensionState.STOPPING: frozenset({ExtensionState.DISABLED, ExtensionState.CONFIGURED}),
}


class ExtensionLifecycleStateMachine:
    """Manages legal state transitions, generations, and health flags for one extension."""

    def __init__(
        self,
        extension_id: str,
        initial_state: ExtensionState = ExtensionState.CONFIGURED,
    ) -> None:
        self.extension_id = extension_id
        self._state: ExtensionState = initial_state
        self.generation: int = 0
        self.restart_count: int = 0
        self.last_error: str | None = None
        self.history: list[tuple[ExtensionState, str | None]] = [(initial_state, "initial")]

    @property
    def state(self) -> ExtensionState:
        return self._state

    @property
    def is_routable(self) -> bool:
        """Only HEALTHY state is eligible for direct routing and tool execution."""
        return self._state == ExtensionState.HEALTHY

    @property
    def is_healthy(self) -> bool:
        """Explicit check: CONFIGURED, DISABLED, STARTING, etc. are NOT healthy."""
        return self._state == ExtensionState.HEALTHY

    def can_transition(self, target: ExtensionState) -> bool:
        return target in LEGAL_TRANSITIONS.get(self._state, frozenset())

    def transition(self, target: ExtensionState, reason: str | None = None) -> None:
        """Apply a validated state transition according to the Section 21 matrix."""
        if not self.can_transition(target):
            raise IllegalStateTransitionError(
                f"Illegal lifecycle state transition for '{self.extension_id}': "
                f"cannot transition from {self._state.value} to {target.value}"
            )

        if target == ExtensionState.STARTING:
            self.generation += 1

        if target in {ExtensionState.FAILED, ExtensionState.UNREACHABLE}:
            self.last_error = reason

        self._state = target
        self.history.append((target, reason))
        logger.debug(
            "Extension '%s' transitioned to %s (gen=%d, reason=%s)",
            self.extension_id,
            target.value,
            self.generation,
            reason,
        )


class OwnerSignal(str, Enum):
    START = "start"
    STOP = "stop"
    RESTART = "restart"


class ExtensionOwner:
    """Actor that exclusively owns subprocess connection and lifecycle transitions.

    Only this owner task executes within the transport context and ClientSession,
    guaranteeing no foreign task enters or exits the session.
    """

    def __init__(
        self,
        definition: ExtensionDefinition,
        initial_state: ExtensionState = ExtensionState.CONFIGURED,
    ) -> None:
        self.definition = definition
        self.extension_id = definition.extension_id
        self.state_machine = ExtensionLifecycleStateMachine(
            self.extension_id, initial_state=initial_state
        )
        self._send_channel, self._receive_channel = anyio.create_memory_object_stream[OwnerSignal](
            max_buffer_size=16
        )
        self._used_session_ids: set[str] = set()
        self._active_session: ClientSession | None = None
        self._active_session_id: str | None = None
        self._is_running = False

    @property
    def state(self) -> ExtensionState:
        return self.state_machine.state

    @property
    def is_routable(self) -> bool:
        return self.state_machine.is_routable

    @property
    def is_healthy(self) -> bool:
        return self.state_machine.is_healthy

    @property
    def generation(self) -> int:
        return self.state_machine.generation

    def signal(self, sig: OwnerSignal) -> bool:
        """Send a control signal to the owner actor. Duplicate starts are coalesced."""
        # Coalesce duplicate start signals if already starting, healthy, or degraded
        if sig == OwnerSignal.START and self.state in {
            ExtensionState.STARTING,
            ExtensionState.HEALTHY,
            ExtensionState.DEGRADED,
        }:
            logger.debug(
                "Coalescing duplicate START signal for '%s' in state %s",
                self.extension_id,
                self.state.value,
            )
            return False

        try:
            self._send_channel.send_nowait(sig)
            return True
        except (anyio.WouldBlock, anyio.ClosedResourceError):
            logger.warning("Could not send signal %s to '%s'", sig, self.extension_id)
            return False

    def register_session(self, session: ClientSession, session_id: str) -> None:
        """Register a session ensuring single-use semantics."""
        if session_id in self._used_session_ids:
            raise SessionReusedError(
                f"Session '{session_id}' for extension '{self.extension_id}' has already been used and cannot be reused"
            )
        self._active_session = session
        self._active_session_id = session_id
        self._used_session_ids.add(session_id)

    def close_active_session(self) -> None:
        """Retire active session reference to prevent any reuse."""
        self._active_session = None
        self._active_session_id = None

    async def run_owner_loop(
        self,
        runner_fn: Callable[[ExtensionOwner], Any] | None = None,
        stop_event: anyio.Event | None = None,
    ) -> None:
        """Actor event loop processing signals for this extension."""
        self._is_running = True
        try:
            async with self._receive_channel:
                async for sig in self._receive_channel:
                    if sig == OwnerSignal.START:
                        if self.state_machine.can_transition(ExtensionState.STARTING):
                            self.state_machine.transition(ExtensionState.STARTING)
                            if runner_fn is not None:
                                try:
                                    await runner_fn(self)
                                except Exception as exc:
                                    logger.exception("Error in extension runner: %s", exc)
                                    if self.state_machine.can_transition(ExtensionState.FAILED):
                                        self.state_machine.transition(
                                            ExtensionState.FAILED, reason=str(exc)
                                        )

                    elif sig == OwnerSignal.STOP:
                        if self.state_machine.can_transition(ExtensionState.STOPPING):
                            self.state_machine.transition(ExtensionState.STOPPING)
                        self.close_active_session()
                        target = (
                            ExtensionState.DISABLED
                            if not self.definition.enabled
                            else ExtensionState.CONFIGURED
                        )
                        if self.state_machine.can_transition(target):
                            self.state_machine.transition(target, reason="manual_stop")
                        if stop_event:
                            stop_event.set()
                        break

                    elif sig == OwnerSignal.RESTART:
                        if self.state_machine.can_transition(ExtensionState.STOPPING):
                            self.state_machine.transition(ExtensionState.STOPPING)
                        self.close_active_session()
                        self.state_machine.restart_count += 1
                        if self.state_machine.can_transition(ExtensionState.CONFIGURED):
                            self.state_machine.transition(
                                ExtensionState.CONFIGURED, reason="restart"
                            )
                        if self.state_machine.can_transition(ExtensionState.STARTING):
                            self.state_machine.transition(
                                ExtensionState.STARTING, reason="restart"
                            )
                            if runner_fn is not None:
                                await runner_fn(self)
        finally:
            self._is_running = False
            self.close_active_session()


async def connect_and_discover_tools(
    owner: ExtensionOwner,
    registry: ExtensionRegistry | None = None,
    parent_env: Mapping[str, str] | None = None,
    extra_env: Mapping[str, str] | None = None,
    startup_timeout: float = LIMITS.startup_sec,
    handshake_timeout: float = LIMITS.handshake_sec,
    discovery_timeout: float = LIMITS.discovery_sec,
    max_tools: int = LIMITS.tools_per_extension,
    keep_alive_event: anyio.Event | None = None,
) -> tuple[ExtensionToolDescriptor, ...]:
    """Connect to subprocess via bounded stdio, perform paginated discovery, and register."""
    definition = owner.definition
    cmd = [definition.command, *definition.arguments]
    cwd = Path(definition.working_directory).resolve()
    base_env = build_extension_environment(definition, parent=parent_env)
    env = {**base_env, "MCP_EXTENSIONS_ENABLED": "0"}
    if extra_env:
        env.update(extra_env)

    session_id = f"{owner.extension_id}-gen{owner.generation}-{time.monotonic()}"

    with anyio.fail_after(startup_timeout):
        async with bounded_stdio_transport(
            cmd,
            cwd=cwd,
            env=env,
            shutdown_timeout=5.0,
        ) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                owner.register_session(session, session_id)

                with anyio.fail_after(handshake_timeout):
                    await session.initialize()

                with anyio.fail_after(discovery_timeout):
                    discovered_tools: list[types.Tool] = []
                    cursor: str | None = None
                    while True:
                        req_params = (
                            types.PaginatedRequestParams(cursor=cursor)
                            if cursor is not None
                            else None
                        )
                        res = await session.list_tools(params=req_params)
                        discovered_tools.extend(res.tools)
                        if len(discovered_tools) > max_tools:
                            raise CatalogLimitExceededError(
                                f"Discovered tool count {len(discovered_tools)} exceeded limit {max_tools}"
                            )
                        if not res.next_cursor:
                            break
                        cursor = res.next_cursor

                descriptors_list: list[ExtensionToolDescriptor] = []
                for t in discovered_tools:
                    raw_schema = getattr(t, "input_schema", getattr(t, "inputSchema", {}))
                    tool_schema = raw_schema if isinstance(raw_schema, dict) else {}
                    descriptors_list.append(
                        ExtensionToolDescriptor.create(
                            extension_id=owner.extension_id,
                            provider=definition.provider,
                            remote_name=t.name,
                            description=t.description or "",
                            input_schema=tool_schema,
                            catalog_generation=owner.generation,
                        )
                    )
                descriptors = tuple(descriptors_list)

                if registry is not None:
                    registry.publish_extension_catalog(
                        owner.extension_id,
                        owner.generation,
                        descriptors,
                    )

                owner.state_machine.transition(ExtensionState.HEALTHY)

                if keep_alive_event is not None:
                    await keep_alive_event.wait()

                return descriptors


class ExtensionLifecycleManager:
    """Manages lifecycles, states, and owner channels across all configured extensions."""

    def __init__(
        self,
        registry: ExtensionRegistry | None = None,
        parallel_startup: int = LIMITS.parallel_startup,
    ) -> None:
        self._registry = registry or ExtensionRegistry()
        self._parallel_startup = parallel_startup
        self._startup_semaphore = anyio.Semaphore(parallel_startup)
        self._owners: dict[str, ExtensionOwner] = {}

    @property
    def registry(self) -> ExtensionRegistry:
        return self._registry

    def register_extension(
        self,
        definition: ExtensionDefinition,
        initial_state: ExtensionState | None = None,
    ) -> ExtensionOwner:
        state = initial_state or (
            ExtensionState.CONFIGURED if definition.enabled else ExtensionState.DISABLED
        )
        owner = ExtensionOwner(definition, initial_state=state)
        self._owners[definition.extension_id] = owner
        return owner

    def get_owner(self, extension_id: str) -> ExtensionOwner | None:
        return self._owners.get(extension_id)

    def get_state(self, extension_id: str) -> ExtensionState | None:
        owner = self.get_owner(extension_id)
        return owner.state if owner else None

    def is_routable(self, extension_id: str) -> bool:
        owner = self.get_owner(extension_id)
        return owner.is_routable if owner else False

    def is_healthy(self, extension_id: str) -> bool:
        owner = self.get_owner(extension_id)
        return owner.is_healthy if owner else False

    def signal_start(self, extension_id: str) -> bool:
        owner = self.get_owner(extension_id)
        if not owner:
            return False
        return owner.signal(OwnerSignal.START)

    def signal_stop(self, extension_id: str) -> bool:
        owner = self.get_owner(extension_id)
        if not owner:
            return False
        return owner.signal(OwnerSignal.STOP)

    def signal_restart(self, extension_id: str) -> bool:
        owner = self.get_owner(extension_id)
        if not owner:
            return False
        return owner.signal(OwnerSignal.RESTART)

    def list_tools(self) -> tuple[ExtensionToolDescriptor, ...]:
        """Return cached discovered tools from registry memory without spawning any processes."""
        return tuple(self._registry.active_snapshot.tools.values())

    def get_catalog_snapshot(self) -> ExtensionCatalogSnapshot:
        """Return authoritative in-memory catalog snapshot."""
        return self._registry.active_snapshot

    async def start_extension_background(
        self,
        extension_id: str,
        keep_alive_event: anyio.Event | None = None,
        custom_runner: Callable[[ExtensionOwner], Any] | None = None,
    ) -> None:
        """Start a single extension under bounded concurrency semaphore."""
        owner = self.get_owner(extension_id)
        if not owner:
            return
        # Do not start disabled or unapproved extensions
        if owner.state == ExtensionState.DISABLED or not owner.definition.enabled:
            logger.info("Extension '%s' is disabled, skipping startup", extension_id)
            return

        async def runner(own: ExtensionOwner) -> None:
            if custom_runner is not None:
                await custom_runner(own)
            else:
                await connect_and_discover_tools(
                    own,
                    self._registry,
                    keep_alive_event=keep_alive_event,
                )

        async with self._startup_semaphore:
            # Signal start and run owner loop
            owner.signal(OwnerSignal.START)
            await owner.run_owner_loop(runner_fn=runner)

    async def start_all_background(
        self,
        tg: anyio.abc.TaskGroup,
        keep_alive_event: anyio.Event | None = None,
        custom_runners: dict[str, Callable[[ExtensionOwner], Any]] | None = None,
    ) -> None:
        """Start all enabled extensions concurrently in background under parallel budget."""
        runners = custom_runners or {}
        for extension_id, owner in self._owners.items():
            if owner.state != ExtensionState.DISABLED and owner.definition.enabled:
                runner = runners.get(extension_id)
                tg.start_soon(
                    self.start_extension_background,
                    extension_id,
                    keep_alive_event,
                    runner,
                )
