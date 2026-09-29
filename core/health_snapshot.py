"""Shared runtime health snapshot used by the MCP health tool and local panel."""

from __future__ import annotations

import asyncio
import os
import platform
from typing import Any

from core.config import SETTINGS, Settings
from core.platform import available_domains, detect_capabilities
from core.recovery import OPERATION_RECOVERY
from core.resource_health import collect_resource_metrics
from core.tool_profiles import resolve_profile
from core.workflow_store import workflow_store
from tools.browser.manager import MANAGER as BROWSER_MANAGER


async def collect_server_health(
    server: Any,
    *,
    settings: Settings = SETTINGS,
    browser_manager: Any = BROWSER_MANAGER,
    resource_collector: Any = collect_resource_metrics,
    recovery: Any = OPERATION_RECOVERY,
    workflow_store_factory: Any = workflow_store,
) -> dict[str, Any]:
    """Return the redacted runtime health payload from the live MCP process."""

    profile = resolve_profile(settings.tool_profile)
    capabilities = detect_capabilities()
    active_domains = available_domains(profile.domains, capabilities)
    tools = await server.list_tools()
    browser_stats = await browser_manager.stats()

    def collect_durable_metrics() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        # File walks, psutil and SQLite operations are synchronous. The health
        # publisher runs periodically on the MCP loop, so keep them together
        # in a worker thread without changing their read order.
        resources = resource_collector(browser_stats)
        workflows = workflow_store_factory(settings.workflow_db)
        return (
            resources,
            workflows.health_summary(),
            workflows.list(offset=0, limit=20),
            recovery.summary(max_items=20),
        )

    resources, workflow_health, recent_workflows, operation_recovery = await asyncio.to_thread(collect_durable_metrics)
    resource_usage = resources.pop("budgets")
    degraded_reasons: list[str] = []
    if int(operation_recovery["uncertain_count"]) > 0:
        degraded_reasons.append("operation_recovery_uncertain")
    if int(operation_recovery["pending_count"]) > 0:
        degraded_reasons.append("operation_recovery_pending")
    if int(workflow_health["unresolved_operation_count"]) > 0:
        degraded_reasons.append("unresolved_uncertain_operations")
    if int(workflow_health["workflow_db_bytes"]) >= settings.workflow_db_warn_bytes:
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
        "server": settings.server_name,
        "version": settings.version,
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
        "audit_log": str(settings.audit_log),
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
        "recent_workflows": recent_workflows,
        "resource_budgets": settings.resource_budgets(),
        "resource_usage": resource_usage,
        **resources,
    }
