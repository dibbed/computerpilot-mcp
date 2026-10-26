"""Create the MCP server and register each independent tool domain."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from mcp.server import MCPServer
from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase
from pydantic import ConfigDict

from core.config import SETTINGS
from core.errors import ToolError
from core.execution_models import ExecutionIntent, ExternalRouteMetadata
from core.execution_router import ExecutionRouter
from core.extensions.bridge import ExtensionMCPServer
from core.health_snapshot import collect_server_health
from core.heartbeat import lifespan
from core.jobs import JobStore, lazy_job_store
from core.platform import available_domains, detect_capabilities
from core.recovery import OPERATION_RECOVERY
from core.resource_health import collect_resource_metrics
from core.response_models import HealthResult, RouteRecommendation, validate_tool_output
from core.timings import ToolRequestTimingMiddleware, install_sdk_timing_hooks
from core.tool_catalog import ToolExecutionMetadata, external_tool_execution_metadata, tool_execution_metadata
from core.tool_profiles import ALL_DOMAINS, PREFERRED_USE, PROFILE_DOMAINS, resolve_profile
from core.tooling import READ_ONLY, compact_errors
from core.workflow_store import workflow_store
from tools.browser import register as register_browser
from tools.browser.manager import MANAGER as BROWSER_MANAGER
from tools.desktop import register as register_desktop
from tools.documents import register as register_documents
from tools.filesystem import register as register_filesystem
from tools.git import register as register_git
from tools.jobs import register as register_jobs
from tools.language import register as register_language
from tools.language.providers import load_lsp_providers
from tools.language.sessions import LspSessionManager
from tools.memory import register as register_memory
from tools.process import register as register_process
from tools.project import register as register_project
from tools.recovery import register as register_recovery
from tools.system import register as register_system
from tools.terminal import register as register_terminal
from tools.testing import register as register_testing
from tools.windows import register as register_windows
from tools.workflows import register as register_workflows

REGISTRARS = {
    "filesystem": register_filesystem,
    "terminal": register_terminal,
    "process": register_process,
    "system": register_system,
    "windows": register_windows,
    "project": register_project,
    "language": register_language,
    "testing": register_testing,
    "git": register_git,
    "browser": register_browser,
    "desktop": register_desktop,
    "documents": register_documents,
    "memory": register_memory,
    "jobs": register_jobs,
    "recovery": register_recovery,
    "workflows": register_workflows,
}


def create_server() -> MCPServer:
    install_sdk_timing_hooks()
    ArgModelBase.model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    ArgModelBase.model_rebuild(force=True)
    profile = resolve_profile(SETTINGS.tool_profile)
    capabilities = detect_capabilities()
    active_domains = available_domains(profile.domains, capabilities)
    store_getter = lazy_job_store(lambda: JobStore()) if {"jobs", "testing"}.intersection(active_domains) else None
    server = ExtensionMCPServer(
        tool_profile=profile.name,
        native_allowlist=profile.native_allowlist,
        name=SETTINGS.server_name,
        title="ComputerPilot MCP",
        description="Cross-platform computer-use and developer-automation MCP backend with structured output.",
        instructions=(
            "Use absolute paths for external projects. Text output is complete by default and accepts optional explicit limits. "
            "Paginate listing and analysis results. Prefer replace_exact, anchored, or AST symbol edits over write_file. "
            "For browser screenshots, pass the returned path to read_file with delivery=auto for "
            "catalog-compatible ChatGPT vision; view_image is equivalent after tool refresh. "
            "Mutating operations are metadata-audited locally."
            " For long verification use start_verification when available, continue independent work, then collect job_wait/job_output. "
            "Acceptance is not test success; require a successful job and a current final result before dependent decisions."
        ),
        version=SETTINGS.version,
        log_level="WARNING",
        lifespan=lifespan,
        middleware=[ToolRequestTimingMiddleware()],
    )
    server.job_store_getter = store_getter
    for domain in active_domains:
        registrar = REGISTRARS.get(domain)
        if registrar is not None:
            if domain == "testing":
                register_testing(server, background_enabled="jobs" in active_domains, store_getter=store_getter)
            elif domain == "jobs":
                register_jobs(server, store_getter=store_getter)
            elif domain == "language" and SETTINGS.persistent_enabled:
                if not SETTINGS.providers_config:
                    raise ToolError("lsp_configuration_invalid", "Persistent language tools require a provider configuration.")
                manager = LspSessionManager(load_lsp_providers(Path(SETTINGS.providers_config)), generation=uuid4().hex)
                cast(Any, server).lsp_manager = manager
                register_language(server, manager=manager)
            else:
                registrar(server)

    @server.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("discover_tool_domains")
    async def discover_tool_domains() -> dict[str, Any]:
        """List deterministic tool profiles/domains and the domains active in this server."""

        native = await MCPServer.list_tools(server)
        external = server.external_tools()
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
            "tool_counts": {"native": len(native), "external": len(external), "total": len(native) + len(external)},
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
        scored: list[tuple[int, str, str, str | None, dict[str, Any], ToolExecutionMetadata | None]] = []
        for tool in await server.list_tools():
            meta = tool.meta or {}
            metadata = tool_execution_metadata(tool.name)
            if meta.get("source_type") == "external_mcp":
                manager = server.extension_manager
                descriptor = manager.registry.get_tool(tool.name) if manager is not None else None
                if (
                    manager is None or descriptor is None or not manager.tool_eligibility(tool.name, profile.name).eligible
                    or descriptor.catalog_generation != meta.get("catalog_generation")
                ):
                    continue
                metadata = external_tool_execution_metadata(tool.name, descriptor.policy, ExternalRouteMetadata(
                    descriptor.extension_id, descriptor.identity.provider, descriptor.catalog_generation,
                ))
            haystack = f"{tool.name} {tool.description or ''} {PREFERRED_USE.get(tool.name, '')}".casefold()
            score = sum(3 if term in tool.name.casefold() else 1 for term in terms if term in haystack)
            if normalized in haystack:
                score += 4
            if score:
                attribution = {
                    key: meta[key] for key in ("source_type", "extension_id", "provider", "domain", "catalog_generation")
                    if key in meta
                } if meta.get("source_type") == "external_mcp" else {}
                scored.append((score, tool.name, tool.description or "", PREFERRED_USE.get(tool.name), attribution, metadata))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return {
            "ok": True,
            "query": query,
            "items": [
                {
                    "name": name,
                    "description": description,
                    "preferred_use": guidance,
                    **attribution,
                    **(
                        {
                            "execution": {
                                "route": metadata.route.value,
                                "determinism": metadata.determinism,
                                "risk_class": metadata.risk.value,
                                "recoverability": metadata.recoverability.value,
                            }
                        }
                        if metadata is not None
                        else {}
                    ),
                }
                for _, name, description, guidance, attribution, metadata in scored[:bounded_limit]
            ],
            "count": min(len(scored), bounded_limit),
            "total_matches": len(scored),
        }

    async def _execution_router() -> ExecutionRouter:
        registered = frozenset(tool.name for tool in await server.list_tools())
        return ExecutionRouter(
            registered_tools=registered,
            capabilities=capabilities,
            policy_version=SETTINGS.execution_router_policy,
            enabled=SETTINGS.execution_router_enabled,
            extension_manager=server.extension_manager if server.external_catalog_visible else None,
            active_profile=profile.name,
        )

    def _execution_intent(
        intent: str,
        *,
        destructive: bool,
        semantic_ambiguous: bool,
        semantic_stale: bool,
        requires_macro_preservation: bool,
        allow_raw_desktop: bool,
        preferred_tool: str | None,
    ) -> ExecutionIntent:
        return ExecutionIntent(
            intent,
            destructive=destructive,
            semantic_ambiguous=semantic_ambiguous,
            semantic_stale=semantic_stale,
            requires_macro_preservation=requires_macro_preservation,
            allow_raw_desktop=allow_raw_desktop,
            preferred_tool=preferred_tool,
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("execution_candidates")
    async def execution_candidates(
        intent: str,
        max_candidates: int = 8,
        destructive: bool = False,
        semantic_ambiguous: bool = False,
        semantic_stale: bool = False,
        requires_macro_preservation: bool = False,
        allow_raw_desktop: bool = False,
        preferred_tool: str | None = None,
    ) -> dict[str, Any]:
        """List bounded execution-route candidates without performing the requested action."""

        router = await _execution_router()
        request = _execution_intent(
            intent,
            destructive=destructive,
            semantic_ambiguous=semantic_ambiguous,
            semantic_stale=semantic_stale,
            requires_macro_preservation=requires_macro_preservation,
            allow_raw_desktop=allow_raw_desktop,
            preferred_tool=preferred_tool,
        )
        return router.candidates(request, max_candidates=max_candidates)

    @server.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("execution_recommend")
    async def execution_recommend(
        intent: str,
        max_candidates: int = 8,
        explain: bool | None = None,
        destructive: bool = False,
        semantic_ambiguous: bool = False,
        semantic_stale: bool = False,
        requires_macro_preservation: bool = False,
        allow_raw_desktop: bool = False,
        preferred_tool: str | None = None,
    ) -> RouteRecommendation:
        """Recommend one explainable execution route without executing it."""

        router = await _execution_router()
        request = _execution_intent(
            intent,
            destructive=destructive,
            semantic_ambiguous=semantic_ambiguous,
            semantic_stale=semantic_stale,
            requires_macro_preservation=requires_macro_preservation,
            allow_raw_desktop=allow_raw_desktop,
            preferred_tool=preferred_tool,
        )
        result = router.recommend(
            request,
            explain=SETTINGS.execution_router_explain if explain is None else explain,
            max_candidates=max_candidates,
        )
        return cast(RouteRecommendation, validate_tool_output(RouteRecommendation, result, operation="execution_recommend"))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("server_health")
    async def server_health() -> HealthResult:
        if not getattr(server, "runtime_started", False):
            native = await MCPServer.list_tools(server)
            result = {
                "ok": True, "health_status": "unavailable", "snapshot_reason": "runtime_not_started",
                "server": SETTINGS.server_name, "version": SETTINGS.version, "tool_profile": profile.name,
                "tool_count": len(native), "unique_tool_names": len({tool.name for tool in native}) == len(native),
            }
        else:
            result = await collect_server_health(
                server,
                settings=SETTINGS,
                browser_manager=BROWSER_MANAGER,
                resource_collector=collect_resource_metrics,
                recovery=OPERATION_RECOVERY,
                workflow_store_factory=workflow_store,
            )
        return cast(HealthResult, validate_tool_output(HealthResult, result, operation="server_health"))

    if profile.native_allowlist is not None and server.native_registered_names != profile.native_allowlist:
        raise ToolError("profile_invalid", "The static profile's native tool registrations are incomplete.")
    return server
