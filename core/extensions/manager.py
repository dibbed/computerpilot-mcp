"""Lifecycle manager and state machine for isolated MCP extensions."""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum
from typing import Any

import anyio
from mcp.client.session import ClientSession

from core.extensions.models import ExtensionDefinition, ExtensionState

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


class ExtensionLifecycleManager:
    """Manages lifecycles, states, and owner channels across all configured extensions."""

    def __init__(self) -> None:
        self._owners: dict[str, ExtensionOwner] = {}

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
