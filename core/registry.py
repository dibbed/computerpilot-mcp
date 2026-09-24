"""Create the MCP server and register each independent tool domain."""

from __future__ import annotations

import os
import platform
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase
from pydantic import ConfigDict

from core.config import SETTINGS, ensure_runtime_dirs
from core.heartbeat import lifespan
from core.platform import available_domains, detect_capabilities
from core.recovery import OPERATION_RECOVERY
from core.resource_health import collect_resource_metrics
from core.timings import ToolRequestTimingMiddleware, install_sdk_timing_hooks
from core.tool_profiles import ALL_DOMAINS, PREFERRED_USE, PROFILE_DOMAINS, resolve_profile
from core.tooling import READ_ONLY, compact_errors
from core.workflow_store import workflow_store
from tools.browser import register as register_browser
from tools.browser.manager import MANAGER as BROWSER_MANAGER
from tools.desktop import register as register_desktop
from tools.filesystem import register as register_filesystem
from tools.git import register as register_git
from tools.jobs import register as register_jobs
from tools.language import register as register_language
from tools.memory import register as register_memory
from tools.process import register as register_process
from tools.project import register as register_project
from tools.recovery import register as register_recovery
from tools.terminal import register as register_terminal
from tools.testing import register as register_testing
from tools.windows import register as register_windows
from tools.workflows import register as register_workflows

REGISTRARS = {
    "filesystem": register_filesystem,
    "terminal": register_terminal,
    "process": register_process,
    "windows": register_windows,
    "project": register_project,
    "language": register_language,
    "testing": register_testing,
    "git": register_git,
    "browser": register_browser,
    "desktop": register_desktop,
    "memory": register_memory,
    "jobs": register_jobs,
    "recovery": register_recovery,
    "workflows": register_workflows,
}


def create_server() -> MCPServer:
    install_sdk_timing_hooks()
    ArgModelBase.model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    ArgModelBase.model_rebuild(force=True)
    ensure_runtime_dirs()
    profile = resolve_profile(SETTINGS.tool_profile)
    capabilities = detect_capabilities()
    active_domains = available_domains(profile.domains, capabilities)
    server = MCPServer(
        name=SETTINGS.server_name,
        title="Ali Windows Agent MCP",
        description="Local full-access Windows developer-agent backend with structured output.",
        instructions=(
            "Use absolute paths for external projects. Text output is complete by default and accepts optional explicit limits. "
            "Paginate listing and analysis results. Prefer replace_exact, anchored, or AST symbol edits over write_file. "
            "For browser screenshots, pass the returned path to read_file with delivery=auto for "
            "catalog-compatible ChatGPT vision; view_image is equivalent after tool refresh. "
            "Mutating operations are metadata-audited locally."
        ),
        version=SETTINGS.version,
        log_level="WARNING",
        lifespan=lifespan,
        middleware=[ToolRequestTimingMiddleware()],
    )
    for domain in active_domains:
        registrar = REGISTRARS.get(domain)
        if registrar is not None:
            registrar(server)

    @server.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("discover_tool_domains")
    def discover_tool_domains() -> dict[str, Any]:
        """List deterministic tool profiles/domains and the domains active in this server."""

        return {
            "ok": True,
            "active_profile": profile.name,
            "active_domains": list(active_domains),
            "requested_domains": list(profile.domains),
            "available_domains": list(available_domains(ALL_DOMAINS, capabilities)),
            "unavailable_domains": [domain for domain in profile.domains if domain not in active_domains],
            "platform": capabilities.platform_key,
            "capabilities": capabilities.as_dict(),
            "profiles": {name: list(domains) for name, domains in PROFILE_DOMAINS.items()},
        }

    @server.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("recommend_tools")
    async def recommend_tools(
        query: str,
        limit: int = 8,
    ) -> dict[str, Any]:
        """Recommend registered tools by name, description, and preferred-use guidance."""

        normalized = query.strip().casefold()
        if not normalized:
            raise ValueError("query must not be empty")
        bounded_limit = min(max(limit, 1), 25)
        terms = set(normalized.replace("_", " ").split())
        scored: list[tuple[int, str, str, str | None]] = []
        for tool in await server.list_tools():
            haystack = f"{tool.name} {tool.description or ''} {PREFERRED_USE.get(tool.name, '')}".casefold()
            score = sum(3 if term in tool.name.casefold() else 1 for term in terms if term in haystack)
            if normalized in haystack:
                score += 4
            if score:
                scored.append((score, tool.name, tool.description or "", PREFERRED_USE.get(tool.name)))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return {
            "ok": True,
            "query": query,
            "items": [
                {"name": name, "description": description, "preferred_use": guidance}
                for _, name, description, guidance in scored[:bounded_limit]
            ],
            "count": min(len(scored), bounded_limit),
            "total_matches": len(scored),
        }

    @server.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("server_health")
    async def server_health() -> dict[str, Any]:
        """Return compact runtime, registration, platform, and optional-browser health."""

        tools = await server.list_tools()
        browser_stats = await BROWSER_MANAGER.stats()
        resources = collect_resource_metrics(browser_stats)
        resource_usage = resources.pop("budgets")
        workflow_health = workflow_store(SETTINGS.workflow_db).health_summary()
        operation_recovery = OPERATION_RECOVERY.summary()
        degraded_reasons: list[str] = []
        if int(operation_recovery["uncertain_count"]) > 0:
            degraded_reasons.append("operation_recovery_uncertain")
        if int(operation_recovery["pending_count"]) > 0:
            degraded_reasons.append("operation_recovery_pending")
        if int(workflow_health["unresolved_operation_count"]) > 0:
            degraded_reasons.append("unresolved_uncertain_operations")
        if int(workflow_health["workflow_db_bytes"]) >= SETTINGS.workflow_db_warn_bytes:
            degraded_reasons.append("workflow_db_pressure")
        resource_pressure = resources.get("resource_pressure")
        if resource_pressure in {"warning", "critical"}:
            degraded_reasons.append("resource_pressure")
        if resource_pressure == "critical":
            health_status = "unhealthy"
        elif degraded_reasons:
            health_status = "degraded"
        else:
            health_status = "healthy"
        return {
            "ok": True,
            "server": SETTINGS.server_name,
            "version": SETTINGS.version,
            "tool_profile": profile.name,
            "tool_domains": list(active_domains),
            "requested_tool_domains": list(profile.domains),
            "tool_count": len(tools),
            "unique_tool_names": len({tool.name for tool in tools}) == len(tools),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "platform_key": capabilities.platform_key,
            "windows": os.name == "nt",
            "capabilities": capabilities.as_dict(),
            "browser_optional_installed": capabilities.browser,
            "semantic_desktop_available": capabilities.semantic_ui,
            "audit_log": str(SETTINGS.audit_log),
            "operation_recovery": operation_recovery,
            "health_status": health_status,
            "degraded_reasons": degraded_reasons,
            "workflow_total": workflow_health["workflow_total"],
            "workflow_operation_total": workflow_health["workflow_operation_total"],
            "workflow_event_total": workflow_health["workflow_event_total"],
            "workflow_db_bytes": workflow_health["workflow_db_bytes"],
            "active_workflow_leases": workflow_health["active_workflow_leases"],
            "queued_workflows": workflow_health["queued_workflows"],
            "running_workflows": workflow_health["running_workflows"],
            "uncertain_workflows": workflow_health["uncertain_workflows"],
            "unresolved_workflow_operations": workflow_health["unresolved_workflow_operations"],
            "workflow_health": workflow_health,
            "resource_budgets": SETTINGS.resource_budgets(),
            "resource_usage": resource_usage,
            **resources,
        }

    return server
