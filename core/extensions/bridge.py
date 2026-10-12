"""Expose approved external tools through public MCPServer override seams."""

from __future__ import annotations

import json
from typing import Any

from mcp import types
from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context

from core.errors import ToolError
from core.extensions.manager import ExtensionLifecycleManager
from core.extensions.models import LIMITS
from core.extensions.output import ExtensionResult, ResultLimits, finalize_envelope

_RESULT_SCHEMA_JSON = json.dumps(ExtensionResult.model_json_schema())


class ExtensionMCPServer(MCPServer):
    def __init__(self, *args: Any, tool_profile: str = "full", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.tool_profile = tool_profile
        self.extension_manager: ExtensionLifecycleManager | None = None

    def external_tools(self) -> list[types.Tool]:
        manager = self.extension_manager
        if manager is None:
            return []
        tools: list[types.Tool] = []
        size = 0
        for descriptor in manager.list_tools():
            if not manager.tool_eligibility(descriptor.public_name, self.tool_profile).eligible:
                continue
            owner = manager.get_owner(descriptor.extension_id)
            assert owner is not None
            description = owner.credential_redactor().text(descriptor.description)
            tool = types.Tool(
                name=descriptor.public_name,
                description=(
                    f"External MCP ({descriptor.identity.provider}); untrusted provider description: {description}"
                )[:LIMITS.description_chars],
                input_schema=descriptor.input_schema,
                output_schema=json.loads(_RESULT_SCHEMA_JSON),
                annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True),
                _meta={
                    "source_type": "external_mcp", "extension_id": descriptor.extension_id,
                    "provider": descriptor.identity.provider, "catalog_generation": descriptor.catalog_generation,
                    "domain": descriptor.policy.domain,
                },
            )
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
