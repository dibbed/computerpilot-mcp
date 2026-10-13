"""Lifecycle manager and state machine for isolated MCP extensions."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import partial
from pathlib import Path
from typing import Any

import anyio
import mcp.types as types
from mcp.client.session import ClientSession
from pydantic import ValidationError

from core.audit import audit_action
from core.errors import ToolError
from core.execution_models import ExecutionIntent, ExternalRouteMetadata, RouteCandidate
from core.extensions.config import build_extension_environment
from core.extensions.models import (
    LIMITS,
    ExtensionCatalogSnapshot,
    ExtensionDefinition,
    ExtensionState,
    ExtensionToolDescriptor,
    MutationClass,
    TrustClass,
)
from core.extensions.output import (
    ExtensionResult,
    ResultLimits,
    finalize_envelope,
    normalize_result,
    validate_arguments,
)
from core.extensions.registry import (
    CatalogLimitExceededError,
    ExtensionLease,
    ExtensionRegistry,
    ToolEligibilityResult,
    evaluate_tool_eligibility,
)
from core.extensions.security import CredentialRedactor, suppress_sdk_payload_logs
from core.extensions.transport import BoundedStderrDrain, bounded_stdio_transport
from core.lifecycle import RUNTIME_LIFECYCLE
from core.platform import normalize_system
from core.recovery import OPERATION_RECOVERY, OperationRecoveryJournal, external_dispatch_deadline
from core.recovery_models import ExternalOperationMetadata
from core.timings import tool_timing
from core.tool_activity import TOOL_ACTIVITY
from core.tool_catalog import external_tool_execution_metadata
from core.tool_profiles import PROFILE_DOMAINS

logger = logging.getLogger(__name__)
_HEALTH_ERROR_CODES = frozenset({
    "config_invalid", "config_version_unsupported", "startup_failed", "ownership_unavailable", "handshake_timeout",
    "handshake_failed", "discovery_failed", "schema_unsupported", "catalog_limit", "transport_eof", "transport_lost",
    "connection_closed", "extension_crashed", "call_timeout", "provider_error", "malformed_result", "output_too_large",
    "recovery_unavailable", "recovery_journal_failed", "operation_limit", "uncertain_side_effect", "shutdown_timeout",
    "restart_budget_exhausted", "external_failure",
})
_HEALTH_COUNT_NAMES = ("enabled", "healthy", "degraded", "unreachable", "failed", "disabled", "blocked", "pending", "live")
_CALL_OUTCOMES = ("succeeded", "failed", "uncertain", "not_dispatched")


def empty_extension_health() -> dict[str, Any]:
    return {
        "configured_count": 0, **{f"{key}_count": 0 for key in _HEALTH_COUNT_NAMES},
        "discovered_tool_count": 0, "approved_tool_count": 0, "eligible_tool_count": 0,
        "catalog_generation": 0, "observed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "unresolved_external_count": 0, "items": [], "truncated": False,
    }


def _bounded_timeout(requested: float, configured: float, ceiling: float) -> float:
    if not math.isfinite(requested) or requested <= 0:
        raise ToolError("invalid_timeout", "The extension deadline must be positive and finite.")
    return min(requested, configured, ceiling)


class IllegalStateTransitionError(ValueError):
    """Raised when an illegal lifecycle state transition is attempted."""


class SessionReusedError(RuntimeError):
    """Raised when an already closed or failed session is attempted to be reused."""


@dataclass
class RestartBudget:
    """Manages reconnect and restart attempt budgets, backoffs, and crash-loop circuit breaker."""

    max_attempts: int = LIMITS.restart_attempts
    window_sec: float = float(LIMITS.restart_window_sec)
    base_backoff_sec: float = 0.5
    max_backoff_sec: float = 30.0
    clock: Callable[[], float] = time.monotonic

    _restart_timestamps: list[float] = field(default_factory=list)
    consecutive_failures: int = 0
    circuit_breaker_tripped: bool = False
    last_backoff: float = 0.0

    def can_restart(self, now: float | None = None) -> bool:
        """Check if restart attempt is permitted within the sliding window budget."""
        current_time = self.clock() if now is None else now
        cutoff = current_time - self.window_sec
        self._restart_timestamps = [t for t in self._restart_timestamps if t >= cutoff]
        if len(self._restart_timestamps) >= self.max_attempts:
            self.circuit_breaker_tripped = True
            return False
        return True

    def record_attempt(self, now: float | None = None) -> float:
        """Record a restart attempt, calculate monotonic backoff, and return backoff duration."""
        current_time = self.clock() if now is None else now
        self._restart_timestamps.append(current_time)
        if self.consecutive_failures == 0:
            backoff = 0.0
        else:
            backoff = min(
                self.max_backoff_sec,
                self.base_backoff_sec * (2 ** (self.consecutive_failures - 1)),
            )
        self.last_backoff = backoff
        self.consecutive_failures += 1
        return backoff

    def record_success(self) -> None:
        """Reset consecutive failures upon achieving healthy status."""
        self.consecutive_failures = 0
        self.circuit_breaker_tripped = False

    def manual_reset(self) -> None:
        """Reset budget and circuit breaker on explicit operator intervention."""
        self._restart_timestamps.clear()
        self.consecutive_failures = 0
        self.circuit_breaker_tripped = False
        self.last_backoff = 0.0

    @property
    def attempt_count(self) -> int:
        """Current number of attempts recorded in the active sliding window."""
        current_time = self.clock()
        cutoff = current_time - self.window_sec
        return sum(1 for t in self._restart_timestamps if t >= cutoff)


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
        self.last_changed_monotonic = time.monotonic()
        self.last_changed_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        self.last_success_at = self.last_changed_at if initial_state is ExtensionState.HEALTHY else None
        self.last_error_at: str | None = None
        self.history: deque[tuple[ExtensionState, str | None]] = deque([(initial_state, "initial")], maxlen=LIMITS.recent_errors)

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

        if target in {ExtensionState.FAILED, ExtensionState.UNREACHABLE, ExtensionState.DEGRADED}:
            self.last_error = reason

        self._state = target
        self.last_changed_monotonic = time.monotonic()
        self.last_changed_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        if target in {ExtensionState.FAILED, ExtensionState.UNREACHABLE, ExtensionState.DEGRADED}:
            self.last_error_at = self.last_changed_at
        if target is ExtensionState.HEALTHY:
            self.last_success_at = self.last_changed_at
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
        restart_budget: RestartBudget | None = None,
    ) -> None:
        self.definition = definition
        self.extension_id = definition.extension_id
        self.state_machine = ExtensionLifecycleStateMachine(
            self.extension_id, initial_state=initial_state
        )
        self.restart_budget = restart_budget or RestartBudget()
        self.is_enabled: bool = definition.enabled
        self._send_channel, self._receive_channel = anyio.create_memory_object_stream[OwnerSignal](
            max_buffer_size=16
        )
        self._used_session_ids: set[str] = set()
        self._active_session: ClientSession | None = None
        self._active_session_id: str | None = None
        self._is_running = False
        self._stopped = False
        self.call_semaphore = anyio.Semaphore(LIMITS.calls_per_extension)
        self._credential_values: tuple[str, ...] = ()
        self._runner_scope: anyio.CancelScope | None = None
        self.call_observations: deque[tuple[str, float]] = deque(maxlen=64)

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

    @property
    def is_running(self) -> bool:
        return self._is_running

    def signal(self, sig: OwnerSignal) -> bool:
        """Send a control signal to the owner actor. Duplicate starts are coalesced."""
        if sig == OwnerSignal.STOP:
            self._stopped = True
        elif self._stopped and sig in {OwnerSignal.START, OwnerSignal.RESTART}:
            return False

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
            if sig in {OwnerSignal.STOP, OwnerSignal.RESTART} and self._runner_scope is not None:
                self._runner_scope.cancel()
            return True
        except (anyio.WouldBlock, anyio.ClosedResourceError, anyio.BrokenResourceError):
            logger.warning("Could not send signal %s to '%s'", sig, self.extension_id)
            return False

    def handle_transport_loss(
        self,
        reason: str = "transport_lost",
        registry: ExtensionRegistry | None = None,
    ) -> bool:
        """Handle unexpected transport loss: withdraw catalog, transition to UNREACHABLE, evaluate restart budget."""
        if self.state in {ExtensionState.HEALTHY, ExtensionState.DEGRADED}:
            if registry is not None:
                registry.retire_extension_catalog(self.extension_id)
            self.state_machine.transition(ExtensionState.UNREACHABLE, reason=reason)
            self.close_active_session()

        if self.state == ExtensionState.UNREACHABLE and not self._stopped:
            if self.restart_budget.can_restart():
                return self.signal(OwnerSignal.RESTART)
            else:
                logger.warning(
                    "Restart budget exhausted for '%s'; transitioning to FAILED (circuit breaker tripped)",
                    self.extension_id,
                )
                self.state_machine.transition(ExtensionState.FAILED, reason="restart_budget_exhausted")
                return False
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

    def require_launch_authorization(self) -> None:
        """Recheck operator authority before entering any provider execution path."""
        if not self.is_enabled:
            raise ToolError("extension_disabled", "The extension is disabled.")
        if not self.definition.launch_approved:
            raise ToolError("launch_not_approved", "The extension has no operator launch approval.")
        if self.definition.trust_class not in {TrustClass.RESTRICTED, TrustClass.TRUSTED}:
            raise ToolError("trust_denied", "The extension trust policy prohibits execution.")

    def credential_redactor(self) -> CredentialRedactor:
        values = self._credential_values
        if self.definition.environment.references:
            values += tuple(os.environ.get(ref.name, "") for ref in self.definition.environment.references.values())
        return CredentialRedactor(values)

    async def _call_tool(
        self,
        remote_name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        timeout_sec: float = LIMITS.call_sec,
    ) -> types.CallToolResult:
        """Call a remote tool on the active session bounded by timeout."""
        self.require_launch_authorization()
        timeout_sec = _bounded_timeout(timeout_sec, self.definition.timeouts.call_sec, LIMITS.call_sec)
        session = self._active_session
        if session is None:
            raise ToolError(
                code="session_not_active",
                message=f"Extension '{self.extension_id}' has no active session",
            )
        try:
            with suppress_sdk_payload_logs(), anyio.fail_after(timeout_sec):
                args_dict = dict(arguments) if arguments is not None else {}
                return await session.send_request(
                    types.CallToolRequest(params=types.CallToolRequestParams(name=remote_name, arguments=args_dict)),
                    types.CallToolResult, request_read_timeout_seconds=timeout_sec,
                )
        except ValidationError:
            raise ToolError("malformed_result", "The provider returned an invalid tool result.") from None
        except TimeoutError as exc:
            raise ToolError(
                code="call_timeout",
                message=f"Tool call '{remote_name}' on extension '{self.extension_id}' timed out after {timeout_sec}s",
            ) from exc
        except ToolError:
            raise
        except Exception:
            raise ToolError("provider_error", "The external provider call failed.") from None

    async def run_owner_loop(
        self,
        runner_fn: Callable[[ExtensionOwner], Any] | None = None,
        stop_event: anyio.Event | None = None,
        sleep_fn: Callable[[float], Any] = anyio.sleep,
    ) -> None:
        """Actor event loop processing signals for this extension."""
        self._is_running = True
        try:
            async with self._receive_channel:
                async for sig in self._receive_channel:
                    if self._stopped and sig != OwnerSignal.STOP:
                        continue
                    if sig in {OwnerSignal.START, OwnerSignal.RESTART}:
                        try:
                            self.require_launch_authorization()
                        except ToolError as denied:
                            logger.warning("Extension launch denied: %s", denied.code)
                            continue
                    if sig == OwnerSignal.START:
                        if self.state_machine.can_transition(ExtensionState.STARTING):
                            self.state_machine.transition(ExtensionState.STARTING)
                            if runner_fn is not None:
                                try:
                                    await self._run_service(runner_fn)
                                    if self.state == ExtensionState.HEALTHY:
                                        self.restart_budget.record_success()
                                except Exception:
                                    logger.warning("Extension runner failed.")
                                    if self.state_machine.can_transition(ExtensionState.FAILED):
                                        self.state_machine.transition(
                                            ExtensionState.FAILED, reason="startup_failed"
                                        )

                    elif sig == OwnerSignal.STOP:
                        if self.state_machine.can_transition(ExtensionState.STOPPING):
                            self.state_machine.transition(ExtensionState.STOPPING)
                        self.close_active_session()
                        target = (
                            ExtensionState.DISABLED
                            if not self.is_enabled
                            else ExtensionState.CONFIGURED
                        )
                        if self.state_machine.can_transition(target):
                            self.state_machine.transition(target, reason="manual_stop")
                        if stop_event:
                            stop_event.set()
                        break

                    elif sig == OwnerSignal.RESTART:
                        if not self.restart_budget.can_restart():
                            logger.warning(
                                "Restart budget exhausted for '%s' (circuit breaker tripped)",
                                self.extension_id,
                            )
                            if self.state_machine.can_transition(ExtensionState.FAILED):
                                self.state_machine.transition(
                                    ExtensionState.FAILED, reason="restart_budget_exhausted"
                                )
                            continue

                        backoff = self.restart_budget.record_attempt()
                        if backoff > 0:
                            with anyio.CancelScope() as scope:
                                self._runner_scope = scope
                                try:
                                    await sleep_fn(backoff)
                                finally:
                                    self._runner_scope = None
                            if scope.cancel_called:
                                continue

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
                                try:
                                    await self._run_service(runner_fn)
                                    if self.state == ExtensionState.HEALTHY:
                                        self.restart_budget.record_success()
                                except Exception:
                                    logger.warning("Extension restart failed.")
                                    if self.state_machine.can_transition(ExtensionState.FAILED):
                                        self.state_machine.transition(
                                            ExtensionState.FAILED, reason="startup_failed"
                                        )
        finally:
            self._is_running = False
            self.close_active_session()

    async def _run_service(self, runner: Callable[[ExtensionOwner], Any]) -> None:
        with anyio.CancelScope() as scope:
            self._runner_scope = scope
            try:
                await runner(self)
            finally:
                self._runner_scope = None


async def connect_and_discover_tools(
    owner: ExtensionOwner,
    registry: ExtensionRegistry | None = None,
    parent_env: Mapping[str, str] | None = None,
    startup_timeout: float = LIMITS.startup_sec,
    handshake_timeout: float = LIMITS.handshake_sec,
    discovery_timeout: float = LIMITS.discovery_sec,
    max_tools: int = LIMITS.tools_per_extension,
    keep_alive_event: anyio.Event | None = None,
    startup_gate: anyio.Semaphore | None = None,
) -> tuple[ExtensionToolDescriptor, ...]:
    """Own one session, with bounded startup/discovery and coalesced live refresh."""
    owner.require_launch_authorization()
    definition = owner.definition
    startup_timeout = _bounded_timeout(startup_timeout, definition.timeouts.startup_sec, LIMITS.startup_sec)
    handshake_timeout = _bounded_timeout(handshake_timeout, definition.timeouts.handshake_sec, LIMITS.handshake_sec)
    discovery_timeout = _bounded_timeout(discovery_timeout, definition.timeouts.discovery_sec, LIMITS.discovery_sec)
    cwd = Path(definition.working_directory).resolve()
    base_env = build_extension_environment(definition, parent=parent_env)
    owner._credential_values = tuple(base_env[name] for name in definition.environment.references)
    refresh_requested = anyio.Event()
    startup_deadline = time.monotonic() + startup_timeout
    session_id = f"{owner.extension_id}-gen{owner.generation}-{time.monotonic()}"

    async def notification(message: Any) -> None:
        if isinstance(message, types.ToolListChangedNotification):
            if owner.state_machine.can_transition(ExtensionState.DEGRADED):
                owner.state_machine.transition(ExtensionState.DEGRADED, reason="catalog_refresh_pending")
            refresh_requested.set()

    gate_held = False
    if startup_gate is not None:
        with anyio.fail_after(max(0.0, startup_deadline - time.monotonic())):
            await startup_gate.acquire()
        gate_held = True
    try:
        owner.require_launch_authorization()
        with suppress_sdk_payload_logs():
            async with bounded_stdio_transport(
                [definition.command, *definition.arguments], cwd=cwd,
                env={**base_env, "MCP_EXTENSIONS_ENABLED": "0"}, shutdown_timeout=definition.timeouts.shutdown_sec,
                stderr_drain=BoundedStderrDrain(credential_bearing=bool(owner._credential_values)),
            ) as (read_stream, write_stream), ClientSession(read_stream, write_stream, message_handler=notification) as session:
                owner.register_session(session, session_id)
                descriptors: tuple[ExtensionToolDescriptor, ...] = ()

                async def discover() -> tuple[ExtensionToolDescriptor, ...]:
                    generation = registry.next_catalog_generation(owner.extension_id, owner.generation) if registry else owner.generation
                    with anyio.fail_after(discovery_timeout):
                        discovered: list[types.Tool] = []
                        cursor: str | None = None
                        seen_cursors: set[str] = set()
                        pages = 0
                        size = 0
                        while True:
                            pages += 1
                            if pages > LIMITS.tools_per_extension:
                                raise CatalogLimitExceededError("Discovery page limit exceeded")
                            params = types.PaginatedRequestParams(cursor=cursor) if cursor is not None else None
                            result = await session.list_tools(params=params)
                            size += len(result.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8"))
                            if (
                                size > LIMITS.catalog_bytes
                                or len(discovered) + len(result.tools) > min(max_tools, LIMITS.tools_per_extension)
                            ):
                                raise CatalogLimitExceededError("Discovery aggregate tool/byte limit exceeded")
                            discovered.extend(result.tools)
                            if not result.next_cursor:
                                break
                            if result.next_cursor in seen_cursors:
                                raise CatalogLimitExceededError("Discovery cursor cycle detected")
                            seen_cursors.add(result.next_cursor)
                            cursor = result.next_cursor
                        candidate = tuple(ExtensionToolDescriptor.create(
                            extension_id=owner.extension_id, provider=definition.provider, remote_name=tool.name,
                            description=tool.description or "", input_schema=tool.input_schema, output_schema=tool.output_schema,
                            policy=definition.tools.get(tool.name), catalog_generation=generation,
                        ) for tool in discovered)
                        if registry is not None:
                            registry.publish_extension_catalog(owner.extension_id, generation, candidate)
                        if owner.state_machine.can_transition(ExtensionState.HEALTHY):
                            owner.state_machine.transition(ExtensionState.HEALTHY)
                        return candidate

                async def refresh() -> None:
                    nonlocal refresh_requested, descriptors
                    while True:
                        await refresh_requested.wait()
                        await anyio.sleep(0.1)
                        refresh_requested = anyio.Event()
                        try:
                            descriptors = await discover()
                        except Exception:
                            if registry is not None:
                                registry.retire_extension_catalog(owner.extension_id)
                            if owner.state_machine.can_transition(ExtensionState.UNREACHABLE):
                                owner.state_machine.transition(ExtensionState.UNREACHABLE, reason="catalog_refresh_failed")
                            raise ToolError("catalog_refresh_failed", "The external catalog refresh failed.") from None

                async def watch_transport() -> None:
                    while read_stream.statistics().open_send_streams:
                        await anyio.sleep(0.05)
                    owner.handle_transport_loss(registry=registry)
                    if owner._runner_scope is None or not owner._runner_scope.cancel_called:
                        raise ToolError("transport_lost", "The external provider connection closed.")

                try:
                    # The startup scope ends before the live session's keep-alive wait.
                    with anyio.fail_after(max(0.0, startup_deadline - time.monotonic())):
                        with anyio.fail_after(handshake_timeout):
                            await session.initialize()
                        descriptors = await discover()
                    if gate_held and startup_gate is not None:
                        startup_gate.release()
                        gate_held = False
                    if keep_alive_event is not None:
                        async with anyio.create_task_group() as tasks:
                            tasks.start_soon(refresh)
                            tasks.start_soon(watch_transport)

                            async def watch_stop() -> None:
                                while not owner._stopped:
                                    await anyio.sleep(0.05)
                                tasks.cancel_scope.cancel()

                            tasks.start_soon(watch_stop)
                            await keep_alive_event.wait()
                            tasks.cancel_scope.cancel()
                    return descriptors
                finally:
                    owner.close_active_session()
                    if registry is not None and keep_alive_event is not None:
                        registry.retire_extension_catalog(owner.extension_id)
    finally:
        if gate_held and startup_gate is not None:
            startup_gate.release()


class ExtensionLifecycleManager:
    """Manages lifecycles, states, and owner channels across all configured extensions."""

    def __init__(
        self,
        registry: ExtensionRegistry | None = None,
        parallel_startup: int = LIMITS.parallel_startup,
        max_global_calls: int = LIMITS.global_calls,
        recovery_journal: OperationRecoveryJournal | None = None,
        *,
        _mutation_gate_closed: bool = False,
    ) -> None:
        self._registry = registry or ExtensionRegistry()
        self._parallel_startup = parallel_startup
        self._startup_semaphore = anyio.Semaphore(parallel_startup)
        self._max_global_calls = max_global_calls
        self._global_call_semaphore = anyio.Semaphore(max_global_calls)
        self._recovery_journal = recovery_journal if recovery_journal is not None else OPERATION_RECOVERY
        self._mutation_gate_closed = _mutation_gate_closed
        self._owners: dict[str, ExtensionOwner] = {}
        self._unresolved_mutation_hooks: list[Callable[[str], None]] = []
        self._shutdown_event: anyio.Event = anyio.Event()

    @property
    def registry(self) -> ExtensionRegistry:
        return self._registry

    @property
    def recovery_journal(self) -> OperationRecoveryJournal:
        return self._recovery_journal

    @property
    def is_mutation_gate_closed(self) -> bool:
        return self._mutation_gate_closed

    def _is_recovery_available(self) -> bool:
        if not self._recovery_journal.enabled:
            return False
        return RUNTIME_LIFECYCLE.snapshot().get("state") == "RUNNING"

    def register_extension(
        self,
        definition: ExtensionDefinition,
        initial_state: ExtensionState | None = None,
        restart_budget: RestartBudget | None = None,
    ) -> ExtensionOwner:
        state = initial_state or (
            ExtensionState.CONFIGURED if definition.enabled else ExtensionState.DISABLED
        )
        owner = ExtensionOwner(
            definition,
            initial_state=state,
            restart_budget=restart_budget,
        )
        self._owners[definition.extension_id] = owner
        return owner

    def get_owner(self, extension_id: str) -> ExtensionOwner | None:
        return self._owners.get(extension_id)

    def get_state(self, extension_id: str) -> ExtensionState | None:
        owner = self.get_owner(extension_id)
        return owner.state if owner else None

    def health_snapshot(self, active_profile: str = "full", *, unresolved_external_count: int | None = None) -> dict[str, Any]:
        """Project cached owner/catalog authority without provider I/O or journal reads."""
        catalog = self.get_catalog_snapshot()
        observed = time.monotonic()
        counts = dict.fromkeys(_HEALTH_COUNT_NAMES, 0)
        items: list[dict[str, Any]] = []
        discovered_total = approved_total = eligible_total = 0
        capacity = unresolved_external_count is not None and unresolved_external_count < self._recovery_journal.external_mutation_limit
        for owner in self._owners.values():
            definition = owner.definition
            enabled = owner.is_enabled and definition.enabled
            blocked = enabled and (not definition.launch_approved or definition.trust_class is TrustClass.UNTRUSTED)
            live = enabled and not blocked and owner._active_session is not None
            descriptors = tuple(tool for tool in catalog.tools.values() if tool.extension_id == owner.extension_id)
            eligible = sum(
                self.tool_eligibility(tool.public_name, active_profile, check_capacity=False).eligible
                for tool in descriptors if tool.policy.mutation_class is MutationClass.READ_ONLY or capacity
            )
            approved = sum(tool.policy.allow_call for tool in descriptors)
            discovered_total += len(descriptors)
            approved_total += approved
            eligible_total += eligible
            counts["enabled"] += int(enabled)
            counts["live"] += int(live)
            if not enabled:
                counts["disabled"] += 1
            elif blocked:
                counts["blocked"] += 1
            elif owner.state is ExtensionState.HEALTHY and live:
                counts["healthy"] += 1
            elif owner.state.value.casefold() in {"degraded", "unreachable", "failed"}:
                counts[owner.state.value.casefold()] += 1
            else:
                counts["pending"] += 1
            redactor = owner.credential_redactor()
            error = owner.state_machine.last_error
            safe_error = error if error in _HEALTH_ERROR_CODES else "external_failure" if error else None
            items.append({
                "extension_id": redactor.text(owner.extension_id), "display_name": redactor.text(definition.display_name)[:180],
                "provider": redactor.text(definition.provider)[:64], "enabled": enabled,
                "state": owner.state.value, "trust_class": definition.trust_class.value,
                "live": live, "routable": live and eligible > 0, "blocked": blocked,
                "discovered_tool_count": len(descriptors), "approved_tool_count": approved, "eligible_tool_count": eligible,
                "owner_generation": owner.generation, "catalog_generation": catalog.extension_generations.get(owner.extension_id, 0),
                "last_changed_at": owner.state_machine.last_changed_at, "last_success_at": owner.state_machine.last_success_at,
                "state_age_sec": max(0.0, observed - owner.state_machine.last_changed_monotonic),
                "last_error_code": safe_error if redactor.text(safe_error or "") == (safe_error or "") else "external_failure",
                "last_error_at": owner.state_machine.last_error_at,
                "restart_count": owner.state_machine.restart_count,
                "calls": {key: sum(outcome == key for outcome, _ in owner.call_observations) for key in _CALL_OUTCOMES},
                "call_window_size": len(owner.call_observations),
                "call_duration_mean_sec": sum(duration for _, duration in owner.call_observations) / max(1, len(owner.call_observations)),
                "call_duration_max_sec": max((duration for _, duration in owner.call_observations), default=0.0),
            })
        result: dict[str, Any] = {
            "configured_count": len(self._owners), **{f"{key}_count": value for key, value in counts.items()},
            "discovered_tool_count": discovered_total, "approved_tool_count": approved_total, "eligible_tool_count": eligible_total,
            "catalog_generation": catalog.manager_generation, "observed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "unresolved_external_count": unresolved_external_count, "items": items[:LIMITS.configured], "truncated": False,
        }
        while len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > LIMITS.health_bytes and result["items"]:
            result["items"].pop()
            result["truncated"] = True
        result["truncated"] = result["truncated"] or len(items) > len(result["items"])
        return result

    def get_restart_budget(self, extension_id: str) -> RestartBudget | None:
        owner = self.get_owner(extension_id)
        return owner.restart_budget if owner else None

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

    def restart_extension(self, extension_id: str, manual_override: bool = False) -> bool:
        """Request restart for an extension under budget control."""
        owner = self.get_owner(extension_id)
        if not owner:
            return False
        if manual_override:
            owner.restart_budget.manual_reset()
        if not owner.restart_budget.can_restart():
            logger.warning(
                "Cannot restart '%s': budget exhausted (circuit breaker tripped)",
                extension_id,
            )
            return False
        return owner.signal(OwnerSignal.RESTART)

    async def disable_extension(self, extension_id: str) -> bool:
        """Disable an extension: admission closes first, catalog retired, owner stopped."""
        owner = self.get_owner(extension_id)
        if not owner:
            return False

        # Admission closes first: retire catalog from authoritative registry
        self._registry.retire_extension_catalog(extension_id)
        owner.is_enabled = False

        # Signal stop to owner actor loop if running
        owner.signal(OwnerSignal.STOP)

        # Transition state machine directly if not in loop
        if owner.state_machine.can_transition(ExtensionState.STOPPING):
            owner.state_machine.transition(ExtensionState.STOPPING, reason="disabled")
        owner.close_active_session()
        if owner.state_machine.can_transition(ExtensionState.DISABLED):
            owner.state_machine.transition(ExtensionState.DISABLED, reason="disabled")
        return True

    def enable_extension(self, extension_id: str) -> bool:
        """Enable an extension that was previously disabled."""
        owner = self.get_owner(extension_id)
        if not owner:
            return False
        owner.is_enabled = True
        owner.restart_budget.manual_reset()
        return True

    def list_tools(self) -> tuple[ExtensionToolDescriptor, ...]:
        """Return cached discovered tools from registry memory without spawning any processes."""
        return tuple(self._registry.active_snapshot.tools.values())

    def tool_eligibility(
        self, name: str, active_profile: str = "full", *, check_capacity: bool = True,
    ) -> ToolEligibilityResult:
        """Read current operator/runtime authority without spawning, RPC or filesystem IO."""
        if active_profile not in PROFILE_DOMAINS:
            return ToolEligibilityResult(False, "profile_mismatch", "The external execution profile is unavailable.")
        snapshot = self.get_catalog_snapshot()
        descriptor = snapshot.get_tool(name)
        if descriptor is None:
            if snapshot.get_withheld_reason(name) is not None:
                return ToolEligibilityResult(False, "schema_withheld", "The external schema is withheld.")
            return ToolEligibilityResult(False, "tool_not_found", "The external tool is unavailable.")
        owner = self.get_owner(descriptor.extension_id)
        if owner is None or not owner.is_enabled:
            return ToolEligibilityResult(False, "extension_disabled", "The external tool is disabled.")
        refs = tuple(key for key, ref in owner.definition.environment.references.items() if os.environ.get(ref.name))
        eligibility = evaluate_tool_eligibility(
            descriptor, snapshot=snapshot, definition=owner.definition, runtime_state=owner.state,
            active_profile=active_profile, platform=normalize_system(), resolved_env_refs=refs,
            is_mutation_gate_closed=self._mutation_gate_closed,
            is_recovery_available=descriptor.policy.mutation_class is MutationClass.READ_ONLY or self._is_recovery_available(),
        )
        if not eligibility.eligible:
            return eligibility
        if check_capacity and descriptor.policy.mutation_class != MutationClass.READ_ONLY:
            if not self._recovery_journal.has_external_mutation_capacity():
                return ToolEligibilityResult(False, "operation_limit", "Unresolved external operation admission quota is exhausted.")
        if owner._active_session is None:
            return ToolEligibilityResult(False, "session_not_active", "The external session is unavailable.")
        try:
            redactor = owner.credential_redactor()
            schema = descriptor.input_schema
            identity = (descriptor.public_name, descriptor.extension_id, descriptor.identity.provider)
            if redactor.json(schema) != schema or any(redactor.text(value) != value for value in identity):
                return ToolEligibilityResult(False, "credential_metadata_withheld", "Credential-bearing schema is withheld.")
        except ToolError:
            return ToolEligibilityResult(False, "credential_metadata_withheld", "Credential-bearing schema is withheld.")
        return eligibility

    def get_catalog_snapshot(self) -> ExtensionCatalogSnapshot:
        """Return authoritative in-memory catalog snapshot."""
        return self._registry.active_snapshot

    def routing_candidates(self, intent: ExecutionIntent, active_profile: str = "full") -> tuple[RouteCandidate, ...]:
        snapshot = self.get_catalog_snapshot()
        candidates: list[RouteCandidate] = []
        for owner in self._owners.values():
            definition = owner.definition
            redactor = owner.credential_redactor()
            for remote_name, configured_policy in definition.tools.items():
                name = f"external.{definition.extension_id}.{remote_name}"
                descriptor = snapshot.get_tool(name)
                policy = descriptor.policy if descriptor is not None else configured_policy
                if intent.name not in policy.intents:
                    continue
                provider = descriptor.identity.provider if descriptor is not None else definition.provider
                if redactor.text(name) != name or redactor.text(provider) != provider:
                    continue
                eligibility = self.tool_eligibility(name, active_profile)
                if descriptor is not None and self._registry.get_tool(name) is not descriptor:
                    eligibility = ToolEligibilityResult(False, "stale_generation", "The external catalog changed.")
                metadata = external_tool_execution_metadata(name, policy, ExternalRouteMetadata(
                    definition.extension_id, provider, descriptor.catalog_generation if descriptor is not None else 0,
                ))
                candidates.append(RouteCandidate(
                    route=metadata.route, representative_tool=name, determinism=metadata.determinism,
                    risk=metadata.risk, recoverability=metadata.recoverability,
                    capability_confidence=policy.capability_confidence, cost_tier=policy.cost_tier,
                    latency_tier=policy.latency_tier, fallback_level=1, external_metadata=metadata.external_metadata,
                    supported=eligibility.eligible, rejection_code=eligibility.rejection_code,
                    capability_checks=("source:external_mcp", "classification:operator_policy", "eligibility:core_policy"),
                    reason=(
                        "operator policy estimates; current Core eligibility permits this capability"
                        if eligibility.eligible else "current Core eligibility rejects this external capability"
                    ),
                ))
        return tuple(candidates)

    async def start_extension_background(
        self,
        extension_id: str,
        keep_alive_event: anyio.Event | None = None,
        custom_runner: Callable[[ExtensionOwner], Any] | None = None,
    ) -> None:
        """Start a single extension under bounded concurrency semaphore."""
        owner = self.get_owner(extension_id)
        if not owner or self._shutdown_event.is_set():
            return
        # Do not start disabled or unapproved extensions
        if owner.state == ExtensionState.DISABLED or not owner.is_enabled:
            logger.info("Extension '%s' is disabled, skipping startup", extension_id)
            return

        async def runner(own: ExtensionOwner) -> None:
            if custom_runner is not None:
                async with self._startup_semaphore:
                    await custom_runner(own)
            else:
                await connect_and_discover_tools(
                    own,
                    self._registry,
                    keep_alive_event=keep_alive_event,
                    startup_gate=self._startup_semaphore,
                )

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
            if owner.state != ExtensionState.DISABLED and owner.is_enabled:
                runner = runners.get(extension_id)
                tg.start_soon(
                    self.start_extension_background,
                    extension_id,
                    keep_alive_event,
                    runner,
                )

    def register_unresolved_mutation_hook(self, hook: Callable[[str], None]) -> None:
        """Register a hook invoked during drain to mark in-flight mutations unresolved."""
        self._unresolved_mutation_hooks.append(hook)

    def close_all_admission(self) -> None:
        """Close admission across all extensions by retiring catalogs from registry."""
        for ext_id in list(self._owners.keys()):
            self._registry.retire_extension_catalog(ext_id)

    async def drain_and_stop(self, timeout_sec: float = LIMITS.shutdown_sec) -> None:
        """Drain and stop all extensions within bounded timeout and reap resources."""
        # 1. Close admission first across all extensions
        self.close_all_admission()

        # 2. Invoke mark mutation unresolved hooks before teardown
        for hook in self._unresolved_mutation_hooks:
            try:
                hook("runtime_shutdown")
            except Exception:
                logger.warning("Unresolved mutation hook failed.")

        # 3. Trigger shutdown event to release waiting background discovery tasks
        self._shutdown_event.set()

        # 4. Signal stop and close sessions bounded by timeout
        with anyio.move_on_after(timeout_sec):
            for owner in list(self._owners.values()):
                owner.signal(OwnerSignal.STOP)

            while any(owner.is_running for owner in self._owners.values()):
                for owner in self._owners.values():
                    if owner._runner_scope is not None:
                        owner._runner_scope.cancel()
                await anyio.sleep(0.01)

            for owner in list(self._owners.values()):
                owner.close_active_session()
                if owner.state_machine.can_transition(ExtensionState.STOPPING):
                    owner.state_machine.transition(ExtensionState.STOPPING, reason="drain_and_stop")
                target = (
                    ExtensionState.DISABLED
                    if not owner.is_enabled
                    else ExtensionState.CONFIGURED
                )
                if owner.state_machine.can_transition(target):
                    owner.state_machine.transition(target, reason="drain_and_stop")

    async def start_all_async(
        self,
        keep_alive_event: anyio.Event | None = None,
        custom_runners: dict[str, Callable[[ExtensionOwner], Any]] | None = None,
    ) -> None:
        """Run all enabled extensions in background task group."""
        event = keep_alive_event or self._shutdown_event
        async with anyio.create_task_group() as tg:
            await self.start_all_background(tg, keep_alive_event=event, custom_runners=custom_runners)

    @asynccontextmanager
    async def guard_call_admission(
        self,
        lease: ExtensionLease,
        *,
        timeout_sec: float = LIMITS.call_sec,
        request_callback: bool = False,
        active_profile: str = "full",
    ) -> AsyncIterator[tuple[ExtensionOwner, ExtensionToolDescriptor]]:
        """Guard call admission across all paths enforcing semaphores, lease validity, and security policies.

        - Rejects callbacks under security policy.
        - Validates lease structure and confirms descriptor exists in registry snapshot.
        - Validates extension owner is enabled and in routable (HEALTHY) state.
        - Enforces mutation closed gate (withheld until M10) regardless of provider hints.
        - Enforces operator allow_call policy.
        - Acquires global and per-extension semaphores bounded by timeout.
        - Re-checks snapshot and lease validity after queue wait to prevent race conditions.
        - Guarantees semaphores are released upon exit or failure.
        """
        # 1. Deny callbacks
        if request_callback:
            raise ToolError(
                code="callbacks_denied",
                message="MCP callbacks are denied under security policy",
            )

        # 2. Validate lease structure and lookup descriptor
        if not isinstance(lease, ExtensionLease):
            raise ToolError(
                code="invalid_lease",
                message="Expected valid ExtensionLease",
            )

        descriptor = self._registry.get_tool(lease.public_name)
        if descriptor is None:
            raise ToolError(
                code="tool_not_found",
                message=f"Tool '{lease.public_name}' not found in registry",
            )

        # 3. Check lease validity against current registry state
        if not self._registry.is_lease_valid(lease):
            raise ToolError(
                code="stale_generation",
                message="Tool lease is invalid or catalog generation has changed",
            )

        # 4. Check extension owner
        owner = self.get_owner(descriptor.extension_id)
        if owner is None or not owner.is_enabled:
            raise ToolError(
                code="extension_disabled",
                message=f"Extension '{descriptor.extension_id}' is disabled",
            )

        if not owner.is_routable:
            raise ToolError(
                code="extension_not_routable",
                message=f"Extension '{descriptor.extension_id}' is not routable in state {owner.state.value}",
            )
        owner.require_launch_authorization()

        # 5. Check operator policy allow_call
        if not descriptor.policy.allow_call:
            raise ToolError(
                code="policy_denied",
                message=f"Tool '{lease.public_name}' is disallowed by operator policy",
            )

        # 6. Check mutation classification and recovery availability
        # Non-authoritative provider hints cannot bypass operator mutation classification!
        if descriptor.policy.mutation_class == MutationClass.UNKNOWN:
            raise ToolError(
                code="unknown_mutation_class_forbidden",
                message=f"Tool '{lease.public_name}' has unknown mutation classification",
            )
        if descriptor.policy.mutation_class != MutationClass.READ_ONLY:
            if self._mutation_gate_closed:
                raise ToolError(
                    code="mutation_withheld",
                    message=f"Mutating tool '{lease.public_name}' is withheld until M10 activation",
                )
            if not self._is_recovery_available():
                raise ToolError(
                    code="recovery_unavailable",
                    message="Mutating external tool requires an enabled recovery journal and running lifecycle.",
                )
            if not self._recovery_journal.has_external_mutation_capacity():
                raise ToolError(
                    code="operation_limit",
                    message="Unresolved external operation admission quota is exhausted.",
                )

        eligibility = self.tool_eligibility(lease.public_name, active_profile)
        if not eligibility.eligible:
            raise ToolError(eligibility.rejection_code or "policy_denied", "The external tool is not eligible.")

        # 7. Acquire global semaphore bounded by timeout
        timeout_sec = _bounded_timeout(timeout_sec, owner.definition.timeouts.call_sec, LIMITS.call_sec)
        try:
            with anyio.fail_after(timeout_sec):
                await self._global_call_semaphore.acquire()
        except TimeoutError as exc:
            raise ToolError(
                code="admission_limit_exceeded",
                message="Global call admission semaphore queue exceeded timeout",
            ) from exc

        try:
            # 8. Acquire per-extension semaphore bounded by timeout
            try:
                with anyio.fail_after(timeout_sec):
                    await owner.call_semaphore.acquire()
            except TimeoutError as exc:
                raise ToolError(
                    code="admission_limit_exceeded",
                    message=f"Extension '{owner.extension_id}' call semaphore queue exceeded timeout",
                ) from exc

            try:
                # 9. Recheck snapshot after queue wait to guard against races
                owner.require_launch_authorization()
                if not owner.is_routable:
                    raise ToolError(
                        code="extension_not_routable",
                        message=f"Extension '{owner.extension_id}' is not routable in state {owner.state.value}",
                    )
                if not self._registry.is_lease_valid(lease):
                    raise ToolError(
                        code="stale_generation",
                        message="Tool lease became invalid while waiting in admission queue",
                    )
                eligibility = self.tool_eligibility(lease.public_name, active_profile)
                if not eligibility.eligible:
                    raise ToolError(eligibility.rejection_code or "policy_denied", "The external tool is no longer eligible.")

                yield owner, descriptor
            finally:
                with anyio.CancelScope(shield=True):
                    owner.call_semaphore.release()
        finally:
            with anyio.CancelScope(shield=True):
                self._global_call_semaphore.release()

    async def execute_guarded_call(
        self,
        lease: ExtensionLease,
        arguments: Mapping[str, Any] | None = None,
        *,
        timeout_sec: float = LIMITS.call_sec,
        request_callback: bool = False,
        active_profile: str = "full",
    ) -> dict[str, Any]:
        """Admit and execute a tool call under guarded boundaries."""
        initial = self._registry.get_tool(lease.public_name) if isinstance(lease, ExtensionLease) else None
        initial_owner = self.get_owner(initial.extension_id) if initial is not None else None
        timeout_sec = _bounded_timeout(
            timeout_sec, initial_owner.definition.timeouts.call_sec if initial_owner else LIMITS.call_sec, LIMITS.call_sec,
        )
        dispatched = False
        observed_start = time.monotonic()
        observed_outcome = "not_dispatched"
        observed_error: str | None = None
        may_dispatch_durable = False
        uncertain_envelope: dict[str, Any] | None = None
        try:
            with anyio.fail_after(timeout_sec):
                descriptor = self._registry.get_tool(lease.public_name) if isinstance(lease, ExtensionLease) else None
                args = validate_arguments(descriptor, arguments) if descriptor is not None else arguments
                async with self.guard_call_admission(
                    lease, timeout_sec=timeout_sec, request_callback=request_callback, active_profile=active_profile,
                ) as (owner, descriptor):
                    with TOOL_ACTIVITY.track(descriptor.public_name), tool_timing(descriptor.public_name):
                        if descriptor.policy.mutation_class == MutationClass.READ_ONLY:
                            dispatched = True
                            observed_outcome = "failed"
                            result = await owner._call_tool(descriptor.remote_name, args, timeout_sec=timeout_sec)
                            envelope = normalize_result(descriptor, result, owner.credential_redactor())
                            observed_outcome = "succeeded" if envelope.get("ok") else "failed"
                            observed_error = envelope.get("error")
                            return envelope

                        with RUNTIME_LIFECYCLE.mutation(descriptor.public_name):
                            # Mutating external call: enforce M10.2 durable dispatch barrier
                            correlation = self._recovery_journal.correlate_external_request(
                                extension_id=descriptor.extension_id,
                                public_name=descriptor.public_name,
                                catalog_generation=descriptor.catalog_generation,
                                schema_fingerprint=descriptor.schema_fingerprint,
                                arguments=args if isinstance(args, Mapping) else None,
                            )
                            ext_meta = ExternalOperationMetadata(
                                extension_id=descriptor.extension_id,
                                provider=descriptor.identity.provider,
                                public_name=descriptor.public_name,
                                remote_name=descriptor.remote_name,
                                catalog_generation=descriptor.catalog_generation,
                                schema_fingerprint=descriptor.schema_fingerprint,
                                mutation_class=descriptor.policy.mutation_class.value,
                                policy_revision=hashlib.sha256(json.dumps(
                                    descriptor.policy.model_dump(mode="json", exclude={"required_env_refs"}),
                                    sort_keys=True, separators=(",", ":"),
                                ).encode("utf-8")).hexdigest(),
                                dispatch_stage="prepared",
                                correlation=correlation,
                            )
                            deadline = time.monotonic() + max(0.0, anyio.current_effective_deadline() - anyio.current_time())
                            with external_dispatch_deadline(deadline):
                                handle = await anyio.to_thread.run_sync(partial(self._recovery_journal.begin,
                                    descriptor.public_name,
                                    f"extension={descriptor.extension_id};tool={descriptor.remote_name}",
                                    external_metadata=ext_meta,
                                ))
                            if handle is None:
                                raise ToolError(
                                    "recovery_unavailable",
                                    "Operation recovery journal must be enabled for external mutations.",
                                )

                            # Re-check lease and owner before durable may_dispatch checkpoint
                            if not owner.is_routable or not self._registry.is_lease_valid(lease):
                                with anyio.CancelScope(shield=True):
                                    await anyio.to_thread.run_sync(self._recovery_journal.finish_not_dispatched, handle, "stale_generation")
                                raise ToolError(
                                    "stale_generation",
                                    "Tool lease or extension state changed before dispatch.",
                                )

                            # Durable may_dispatch barrier BEFORE transport receives the call
                            deadline = time.monotonic() + max(0.0, anyio.current_effective_deadline() - anyio.current_time())
                            with external_dispatch_deadline(deadline):
                                await anyio.to_thread.run_sync(self._recovery_journal.checkpoint_external_dispatch, handle)
                            may_dispatch_durable = True
                            dispatched = True
                            observed_outcome = "uncertain"

                            async def _record_uncertain(reason: str) -> dict[str, Any]:
                                nonlocal observed_error
                                observed_error = "uncertain_side_effect"
                                with anyio.CancelScope(shield=True):
                                    try:
                                        await anyio.to_thread.run_sync(self._recovery_journal.mark_uncertain, handle, reason)
                                    except ToolError:
                                        if owner.state_machine.can_transition(ExtensionState.DEGRADED):
                                            owner.state_machine.transition(
                                                ExtensionState.DEGRADED,
                                                reason="recovery_journal_failed",
                                            )
                                    for hook in self._unresolved_mutation_hooks:
                                        try:
                                            hook(reason)
                                        except Exception:
                                            pass
                                return finalize_envelope(
                                    ExtensionResult(
                                        ok=False,
                                        extension_id=descriptor.extension_id,
                                        provider=descriptor.identity.provider,
                                        tool=descriptor.public_name,
                                        catalog_generation=descriptor.catalog_generation,
                                        operation_id=handle.operation_id,
                                        outcome="uncertain",
                                        limits=ResultLimits(returned_bytes=0, original_bytes_if_known=None),
                                        error="uncertain_side_effect",
                                        message="External mutation outcome is uncertain; do not retry automatically.",
                                    ).model_dump(mode="json")
                                )

                            try:
                                eligibility = self.tool_eligibility(lease.public_name, active_profile, check_capacity=False)
                                if not self._registry.is_lease_valid(lease) or not eligibility.eligible:
                                    return await _record_uncertain(eligibility.rejection_code or "stale_generation")
                                result = await owner._call_tool(descriptor.remote_name, args, timeout_sec=timeout_sec)
                                envelope = normalize_result(descriptor, result, owner.credential_redactor())
                            except ToolError as exc:
                                uncertain_envelope = await _record_uncertain(exc.code)
                                return uncertain_envelope
                            except TimeoutError:
                                uncertain_envelope = await _record_uncertain("call_timeout")
                                return uncertain_envelope
                            except BaseException as exc:
                                reason = (
                                    "call_timeout"
                                    if isinstance(exc, anyio.get_cancelled_exc_class())
                                    else "external_call_failed"
                                )
                                uncertain_envelope = await _record_uncertain(reason)
                                if isinstance(exc, anyio.get_cancelled_exc_class()):
                                    raise
                                return uncertain_envelope

                            if not envelope.get("ok"):
                                uncertain_envelope = await _record_uncertain(str(envelope.get("error") or "provider_error"))
                                return uncertain_envelope

                            structured = envelope.get("structured_content")
                            receipt_id: str | None = None
                            if isinstance(structured, Mapping) and isinstance(structured.get("receipt_id"), str):
                                candidate_rcpt = structured["receipt_id"]
                                if 1 <= len(candidate_rcpt) <= 128:
                                    receipt_id = candidate_rcpt

                            with anyio.CancelScope(shield=True):
                                try:
                                    await anyio.to_thread.run_sync(partial(self._recovery_journal.finish,
                                        handle,
                                        known_result="ok",
                                        external_outcome="succeeded",
                                        receipt_id=receipt_id,
                                    ))
                                except ToolError:
                                    observed_error = "uncertain_side_effect"
                                    if owner.state_machine.can_transition(ExtensionState.DEGRADED):
                                        owner.state_machine.transition(
                                            ExtensionState.DEGRADED,
                                            reason="recovery_journal_failed",
                                        )
                                    return finalize_envelope(
                                        ExtensionResult(
                                            ok=False,
                                            extension_id=descriptor.extension_id,
                                            provider=descriptor.identity.provider,
                                            tool=descriptor.public_name,
                                            catalog_generation=descriptor.catalog_generation,
                                            operation_id=handle.operation_id,
                                            outcome="uncertain",
                                            limits=ResultLimits(returned_bytes=0, original_bytes_if_known=None),
                                            error="uncertain_side_effect",
                                            message="The external mutation executed, but terminal recovery journal update failed.",
                                        ).model_dump(mode="json")
                                    )

                            envelope["operation_id"] = handle.operation_id
                            observed_outcome = "succeeded"
                            return finalize_envelope(envelope)
        except TimeoutError:
            if may_dispatch_durable and uncertain_envelope is not None:
                return uncertain_envelope
            code = "call_timeout" if dispatched else "admission_limit_exceeded"
            observed_error = code
            raise ToolError(code, "The external call timed out within its total admission/execution deadline.") from None
        except ToolError as exc:
            observed_error = exc.code
            raise
        finally:
            if initial_owner is not None and initial is not None:
                duration = max(0.0, time.monotonic() - observed_start)
                initial_owner.call_observations.append((observed_outcome, duration))
                safe_error = observed_error if observed_error in _HEALTH_ERROR_CODES else "external_failure" if observed_error else None
                try:
                    redactor = initial_owner.credential_redactor()
                    audit_action("external_tool_call", target=redactor.text(initial.public_name),
                                 outcome=observed_outcome, details=redactor.json({
                                     "extension_id": initial.extension_id, "catalog_generation": initial.catalog_generation,
                                     "mutation_class": initial.policy.mutation_class.value,
                                     "error_code": safe_error, "duration_sec": duration,
                                 }))
                except Exception:
                    # Optional telemetry cannot replace the durable dispatch/recovery outcome.
                    pass
