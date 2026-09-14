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
from core.timings import ToolRequestTimingMiddleware, install_sdk_timing_hooks
from core.tooling import READ_ONLY, compact_errors
from tools.browser import register as register_browser
from tools.desktop import register as register_desktop
from tools.filesystem import register as register_filesystem
from tools.git import register as register_git
from tools.jobs import register as register_jobs
from tools.memory import register as register_memory
from tools.process import register as register_process
from tools.project import register as register_project
from tools.terminal import register as register_terminal
from tools.testing import register as register_testing
from tools.windows import register as register_windows

REGISTRARS = (
    register_filesystem,
    register_terminal,
    register_process,
    register_windows,
    register_project,
    register_testing,
    register_git,
    register_browser,
    register_desktop,
    register_memory,
    register_jobs,
)


def create_server() -> MCPServer:
    install_sdk_timing_hooks()
    ArgModelBase.model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    ArgModelBase.model_rebuild(force=True)
    ensure_runtime_dirs()
    server = MCPServer(
        name=SETTINGS.server_name,
        title="Ali Windows Agent MCP",
        description="Local full-access Windows developer-agent backend with structured output.",
        instructions=(
            "Use absolute paths for external projects. Text output is complete by default and accepts optional explicit limits. "
            "Paginate listing and analysis results. Prefer replace_exact, anchored, or AST symbol edits over write_file. "
            "Mutating operations are metadata-audited locally."
        ),
        version=SETTINGS.version,
        log_level="WARNING",
        lifespan=lifespan,
        middleware=[ToolRequestTimingMiddleware()],
    )
    for registrar in REGISTRARS:
        registrar(server)

    @server.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("server_health")
    async def server_health() -> dict[str, Any]:
        """Return compact runtime, registration, platform, and optional-browser health."""

        tools = await server.list_tools()
        return {
            "ok": True,
            "server": SETTINGS.server_name,
            "version": SETTINGS.version,
            "tool_count": len(tools),
            "unique_tool_names": len({tool.name for tool in tools}) == len(tools),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "windows": os.name == "nt",
            "browser_optional_installed": importlib.util.find_spec("playwright") is not None,
            "audit_log": str(SETTINGS.audit_log),
            "operation_recovery": OPERATION_RECOVERY.summary(),
        }

    return server
