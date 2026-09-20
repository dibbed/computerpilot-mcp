"""Create the MCP server and register each independent tool domain."""

from __future__ import annotations

import importlib.util
import os
import platform
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase
from pydantic import ConfigDict

from core.config import SETTINGS, ensure_runtime_dirs
from core.heartbeat import lifespan
from core.recovery import OPERATION_RECOVERY
from core.resource_health import collect_resource_metrics
from core.timings import ToolRequestTimingMiddleware, install_sdk_timing_hooks
from core.tool_profiles import ALL_DOMAINS, PREFERRED_USE, PROFILE_DOMAINS, resolve_profile
from core.tooling import READ_ONLY, compact_errors
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
}


def create_server() -> MCPServer:
    install_sdk_timing_hooks()
    ArgModelBase.model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    ArgModelBase.model_rebuild(force=True)
    ensure_runtime_dirs()
    profile = resolve_profile(SETTINGS.tool_profile)
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
    for domain in profile.domains:
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
            "active_domains": list(profile.domains),
            "available_domains": list(ALL_DOMAINS),
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
        return {
            "ok": True,
            "server": SETTINGS.server_name,
            "version": SETTINGS.version,
            "tool_profile": profile.name,
            "tool_domains": list(profile.domains),
            "tool_count": len(tools),
            "unique_tool_names": len({tool.name for tool in tools}) == len(tools),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "windows": os.name == "nt",
            "browser_optional_installed": importlib.util.find_spec("playwright") is not None,
            "semantic_desktop_available": os.name == "nt" and importlib.util.find_spec("uiautomation") is not None,
            "audit_log": str(SETTINGS.audit_log),
            "operation_recovery": OPERATION_RECOVERY.summary(),
            "resource_budgets": SETTINGS.resource_budgets(),
            "resource_usage": resource_usage,
            **resources,
        }

    return server
