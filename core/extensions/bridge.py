"""Expose approved external tools through public MCPServer override seams."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext
from contextvars import ContextVar
from typing import Any

import anyio
from mcp import types
from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver.context import Context
from mcp.server.subscriptions import InMemorySubscriptionBus
from mcp.shared.subscriptions import ToolsListChanged

from core.errors import ToolError
from core.extensions.manager import ExtensionLifecycleManager
from core.extensions.models import LIMITS
from core.extensions.output import ExtensionResult, ResultLimits, finalize_envelope, public_tool_descriptor

_EXTERNAL_VISIBLE: ContextVar[bool] = ContextVar("external_catalog_visible", default=True)


class ExtensionMCPServer(MCPServer):
    def __init__(self, *args: Any, tool_profile: str = "full", **kwargs: Any) -> None:
        self.tool_profile = tool_profile
        self.extension_manager: ExtensionLifecycleManager | None = None
        subscriptions = kwargs.pop("subscriptions", None)
        self.catalog_subscriptions = subscriptions if subscriptions is not None else InMemorySubscriptionBus()
        self._legacy_sessions: dict[int, Any] = {}
        original_lifespan = kwargs.pop("lifespan", None)

        @asynccontextmanager
        async def managed_lifespan(server: MCPServer[Any]) -> AsyncIterator[Any]:
            assert isinstance(server, ExtensionMCPServer)
            async with original_lifespan(server) if original_lifespan is not None else nullcontext({}) as state:
                if server.extension_manager is None:
                    yield state
                    return
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(server.notify_catalog_changes)
                    try:
                        yield state
                    finally:
                        tasks.cancel_scope.cancel()
                        server._legacy_sessions.clear()

        middleware = [*kwargs.pop("middleware", ()), self.observe_client]
        super().__init__(*args, **kwargs, subscriptions=self.catalog_subscriptions, lifespan=managed_lifespan, middleware=middleware)

    async def observe_client(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
        visible = True
        if ctx.protocol_version < "2026-07-28":
            params = ctx.session.client_params
            key = id(params)
            visible = params is not None and (key in self._legacy_sessions or len(self._legacy_sessions) < LIMITS.notification_pending)
            if visible:
                # SDK session wrappers are request-scoped; initialize params identify the live connection.
                self._legacy_sessions[key] = ctx.session
        token = _EXTERNAL_VISIBLE.set(visible)
        try:
            return await call_next(ctx)
        finally:
            _EXTERNAL_VISIBLE.reset(token)

    async def notify_catalog_changes(self) -> None:
        manager = self.extension_manager
        assert manager is not None
        generation = manager.registry.manager_generation

        async def legacy_notify(key: int, session: Any) -> None:
            try:
                with anyio.fail_after(1.0):
                    await session.send_tool_list_changed()
            except Exception:
                if self._legacy_sessions.get(key) is session:
                    self._legacy_sessions.pop(key, None)

        while True:
            await anyio.sleep(0.1)
            current = manager.registry.manager_generation
            if current == generation:
                continue
            generation = current
            await self.catalog_subscriptions.publish(ToolsListChanged())
            async with anyio.create_task_group() as tasks:
                for key, session in tuple(self._legacy_sessions.items()):
                    tasks.start_soon(legacy_notify, key, session)

    def external_tools(self) -> list[types.Tool]:
        manager = self.extension_manager
        if manager is None or not _EXTERNAL_VISIBLE.get():
            return []
        tools: list[types.Tool] = []
        size = 0
        for descriptor in manager.list_tools():
            if not manager.tool_eligibility(descriptor.public_name, self.tool_profile).eligible:
                continue
            owner = manager.get_owner(descriptor.extension_id)
            assert owner is not None
            tool = public_tool_descriptor(descriptor, owner.credential_redactor())
            size += len(tool.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8"))
            if size > LIMITS.catalog_bytes:
                return []
            tools.append(tool)
        return tools

    async def list_tools(self) -> list[types.Tool]:
        return [*(await super().list_tools()), *self.external_tools()]

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context[Any, Any] | None = None,
    ) -> types.CallToolResult | types.InputRequiredResult:
        if not name.startswith("external."):
            return await super().call_tool(name, arguments, context)
        manager = self.extension_manager
        visible = {tool.name for tool in self.external_tools()}
        descriptor = manager.registry.get_tool(name) if manager is not None and name in visible else None
        owner = manager.get_owner(descriptor.extension_id) if manager is not None and descriptor is not None else None
        error = "tool_unavailable"
        if descriptor is not None and manager is not None and owner is not None:
            try:
                lease = manager.registry.create_lease(name)
                if lease is None:
                    raise ToolError("stale_generation", "The external tool is unavailable.")
                envelope = await manager.execute_guarded_call(
                    lease, arguments, active_profile=self.tool_profile,
                    timeout_sec=owner.definition.timeouts.call_sec,
                )
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text="External MCP result; inspect the Core envelope.")],
                    structured_content=envelope, is_error=not envelope["ok"],
                )
            except ToolError as exc:
                error = exc.code
            except Exception:
                error = "external_call_failed"
        failed = error in {
            "provider_error", "call_timeout", "malformed_result", "output_too_large", "output_unavailable", "external_call_failed",
        }
        envelope = finalize_envelope(ExtensionResult(
            ok=False, extension_id=descriptor.extension_id if descriptor is not None else "unavailable",
            provider=descriptor.identity.provider if descriptor is not None else "unavailable",
            tool=descriptor.public_name if descriptor is not None else "external.unavailable",
            catalog_generation=descriptor.catalog_generation if descriptor is not None else 0,
            outcome="failed" if failed else "not_dispatched", limits=ResultLimits(returned_bytes=0, original_bytes_if_known=None),
            error=error, message="The external call was rejected or failed under Core policy.",
        ).model_dump(mode="json"))
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="The external call was rejected or failed under Core policy.")],
            structured_content=envelope, is_error=True,
        )
