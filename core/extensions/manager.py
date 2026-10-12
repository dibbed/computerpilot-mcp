"""Lifecycle manager and state machine for isolated MCP extensions."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import anyio
import mcp.types as types
from mcp.client.session import ClientSession
from pydantic import ValidationError

from core.errors import ToolError
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
from core.extensions.output import normalize_result, validate_arguments
from core.extensions.registry import (
    CatalogLimitExceededError,
    ExtensionLease,
    ExtensionRegistry,
    ToolEligibilityResult,
    evaluate_tool_eligibility,
)
from core.extensions.security import CredentialRedactor, suppress_sdk_payload_logs
from core.extensions.transport import BoundedStderrDrain, bounded_stdio_transport
from core.platform import normalize_system

logger = logging.getLogger(__name__)


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
        self.call_semaphore = anyio.Semaphore(LIMITS.calls_per_extension)
        self._credential_values: tuple[str, ...] = ()

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

        if self.state == ExtensionState.UNREACHABLE:
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
                                    await runner_fn(self)
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
                            await sleep_fn(backoff)

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
                                    await runner_fn(self)
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


async def connect_and_discover_tools(
    owner: ExtensionOwner,
    registry: ExtensionRegistry | None = None,
    parent_env: Mapping[str, str] | None = None,
    startup_timeout: float = LIMITS.startup_sec,
    handshake_timeout: float = LIMITS.handshake_sec,
    discovery_timeout: float = LIMITS.discovery_sec,
    max_tools: int = LIMITS.tools_per_extension,
    keep_alive_event: anyio.Event | None = None,
) -> tuple[ExtensionToolDescriptor, ...]:
    """Connect to subprocess via bounded stdio, perform paginated discovery, and register."""
    owner.require_launch_authorization()
    definition = owner.definition
    cmd = [definition.command, *definition.arguments]
    cwd = Path(definition.working_directory).resolve()
    base_env = build_extension_environment(definition, parent=parent_env)
    env = {**base_env, "MCP_EXTENSIONS_ENABLED": "0"}
    owner._credential_values = tuple(base_env[name] for name in definition.environment.references)

    session_id = f"{owner.extension_id}-gen{owner.generation}-{time.monotonic()}"

    with suppress_sdk_payload_logs(), anyio.fail_after(startup_timeout):
        async with bounded_stdio_transport(
            cmd,
            cwd=cwd,
            env=env,
            shutdown_timeout=5.0,
            stderr_drain=BoundedStderrDrain(credential_bearing=bool(owner._credential_values)),
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
                            output_schema=t.output_schema,
                            policy=definition.tools.get(t.name),
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
        max_global_calls: int = LIMITS.global_calls,
    ) -> None:
        self._registry = registry or ExtensionRegistry()
        self._parallel_startup = parallel_startup
        self._startup_semaphore = anyio.Semaphore(parallel_startup)
        self._max_global_calls = max_global_calls
        self._global_call_semaphore = anyio.Semaphore(max_global_calls)
        self._owners: dict[str, ExtensionOwner] = {}
        self._unresolved_mutation_hooks: list[Callable[[str], None]] = []
        self._shutdown_event: anyio.Event = anyio.Event()

    @property
    def registry(self) -> ExtensionRegistry:
        return self._registry

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

    def tool_eligibility(self, name: str, active_profile: str = "full") -> ToolEligibilityResult:
        """Read current operator/runtime authority without spawning, RPC or filesystem IO."""
        snapshot = self.get_catalog_snapshot()
        descriptor = snapshot.get_tool(name)
        if descriptor is None:
            return ToolEligibilityResult(False, "tool_not_found", "The external tool is unavailable.")
        owner = self.get_owner(descriptor.extension_id)
        if owner is None or not owner.is_enabled:
            return ToolEligibilityResult(False, "extension_disabled", "The external tool is disabled.")
        refs = tuple(key for key, ref in owner.definition.environment.references.items() if os.environ.get(ref.name))
        eligibility = evaluate_tool_eligibility(
            descriptor, snapshot=snapshot, definition=owner.definition, runtime_state=owner.state,
            active_profile=active_profile, platform=normalize_system(), resolved_env_refs=refs,
        )
        if not eligibility.eligible:
            return eligibility
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
        if owner.state == ExtensionState.DISABLED or not owner.is_enabled:
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

        # 6. Check mutation closure (closed until M10)
        # Non-authoritative provider hints cannot bypass operator mutation classification!
        if descriptor.policy.mutation_class != MutationClass.READ_ONLY:
            raise ToolError(
                code="mutation_withheld",
                message=f"Mutating tool '{lease.public_name}' is withheld until M10 activation",
            )

        eligibility = self.tool_eligibility(lease.public_name, active_profile)
        if not eligibility.eligible:
            raise ToolError(eligibility.rejection_code or "policy_denied", "The external tool is not eligible.")

        # 7. Acquire global semaphore bounded by timeout
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
        dispatched = False
        try:
            with anyio.fail_after(timeout_sec):
                descriptor = self._registry.get_tool(lease.public_name) if isinstance(lease, ExtensionLease) else None
                args = validate_arguments(descriptor, arguments) if descriptor is not None else arguments
                async with self.guard_call_admission(
                    lease, timeout_sec=timeout_sec, request_callback=request_callback, active_profile=active_profile,
                ) as (owner, descriptor):
                    dispatched = True
                    result = await owner._call_tool(descriptor.remote_name, args, timeout_sec=timeout_sec)
                    return normalize_result(descriptor, result, owner.credential_redactor())
        except TimeoutError:
            code = "call_timeout" if dispatched else "admission_limit_exceeded"
            raise ToolError(code, "The external call timed out within its total admission/execution deadline.") from None
