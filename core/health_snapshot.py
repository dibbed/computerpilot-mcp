"""Shared runtime health snapshot used by the MCP health tool and local panel."""

from __future__ import annotations

import asyncio
import json
import math
import os
import platform
import threading
import time
from datetime import datetime, timezone
from typing import Any

from mcp.server import MCPServer

from core.config import SETTINGS, Settings
from core.extensions.manager import empty_extension_health
from core.extensions.models import LIMITS
from core.platform import available_domains, detect_capabilities
from core.recovery import OPERATION_RECOVERY
from core.resource_health import collect_resource_metrics
from core.router_metrics import ROUTER_METRICS
from core.tool_profiles import resolve_profile
from core.workflow_store import workflow_store
from tools.browser.manager import MANAGER as BROWSER_MANAGER

_HEALTH_SYNC_LOCK = threading.Lock()
MAX_RUNTIME_HEALTH_BYTES = 512 * 1024
RUNTIME_HEALTH_MAX_AGE_SEC = 30.0


def bound_runtime_health(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep a complete payload or an explicit bounded summary; never truncate JSON bytes."""
    result = dict(payload)
    result.setdefault("snapshot_truncated", False)

    def encode(value: dict[str, Any]) -> bytes:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encode(result)) <= MAX_RUNTIME_HEALTH_BYTES:
        return result
    keep = {
        "ok", "server", "version", "tool_profile", "tool_count", "unique_tool_names", "health_status", "degraded_reasons",
        "snapshot_schema_version", "runtime_generation", "collected_at", "collected_monotonic", "extensions",
        "workflow_total", "workflow_operation_total", "workflow_event_total", "workflow_db_bytes", "active_workflow_leases",
        "queued_workflows", "running_workflows", "uncertain_workflows", "unresolved_workflow_operations", "resource_pressure",
    }
    summary = {key: value for key, value in result.items() if key in keep}
    for key in ("operation_recovery", "workflow_health", "execution_router"):
        value = result.get(key)
        if isinstance(value, dict):
            summary[key] = {name: item for name, item in value.items() if item is None or isinstance(item, (int, float, bool))}
    summary["snapshot_truncated"] = True
    summary["snapshot_omitted_fields"] = sorted(set(result) - set(summary))[:64]
    if len(encode(summary)) > MAX_RUNTIME_HEALTH_BYTES:
        return {"ok": False, "health_status": "unavailable", "snapshot_truncated": True, "snapshot_reason": "payload_limit"}
    return summary


def validate_runtime_health(payload: dict[str, Any] | None, expected_generation: str | None, *, now: float | None = None) -> dict[str, Any]:
    """Validate generation and monotonic publication age without probes or config reads."""
    if payload is None:
        return {"health_status": "unavailable", "snapshot_status": "unavailable"}
    result = dict(payload)
    if "extensions" not in result and "snapshot_schema_version" not in result:
        # Old native snapshots retain their values; they contain no verified extension observation.
        result["snapshot_status"] = "unverified"
        return result
    observed = result.get("collected_monotonic")
    try:
        age = (
            (time.monotonic() if now is None else now) - float(observed)
            if isinstance(observed, (int, float)) and not isinstance(observed, bool) else math.inf
        )
    except (OverflowError, TypeError, ValueError):
        age = math.inf
    generation_matches = expected_generation is None or result.get("runtime_generation") == expected_generation
    extensions = result.get("extensions")
    fresh = (
        type(result.get("snapshot_schema_version")) is int and result["snapshot_schema_version"] == 1
        and isinstance(extensions, dict) and isinstance(extensions.get("items"), list)
        and math.isfinite(age) and 0 <= age <= RUNTIME_HEALTH_MAX_AGE_SEC
    )
    result["snapshot_age_sec"] = age if math.isfinite(age) else None
    result["snapshot_status"] = "fresh" if fresh and generation_matches else "stale"
    if result["snapshot_status"] == "stale":
        result["health_status"] = "stale"
        result["snapshot_reason"] = "generation_mismatch" if not generation_matches else "snapshot_expired_or_invalid"
        if isinstance(extensions, dict):
            rows = extensions.get("items")
            result["extensions"] = {
                **extensions, "eligible_tool_count": 0, "healthy_count": 0, "live_count": 0,
                "items": [{**row, "live": False, "routable": False, "eligible_tool_count": 0, "snapshot_status": "stale"}
                          for row in (rows[:LIMITS.configured] if isinstance(rows, list) else []) if isinstance(row, dict)],
            }
    return result


async def collect_server_health(
    server: Any,
    *,
    settings: Settings = SETTINGS,
    browser_manager: Any = BROWSER_MANAGER,
    resource_collector: Any = collect_resource_metrics,
    recovery: Any = OPERATION_RECOVERY,
    workflow_store_factory: Any = workflow_store,
    router_metrics: Any = ROUTER_METRICS,
) -> dict[str, Any]:
    """Return the redacted runtime health payload from the live MCP process."""

    profile = resolve_profile(settings.tool_profile)
    capabilities = detect_capabilities()
    active_domains = available_domains(profile.domains, capabilities)
    browser_stats = await browser_manager.stats()
    router_snapshot = router_metrics.snapshot()
    extension_manager = getattr(server, "extension_manager", None)

    def collect_durable_metrics() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], int]:
        # File walks, psutil and SQLite operations are synchronous. The health
        # publisher runs periodically on the MCP loop, so keep them together
        # in a worker thread without changing their read order.
        # These reads used to execute serially on the event loop. Retain that
        # order for the shared workflow-store singleton across health requests.
        with _HEALTH_SYNC_LOCK:
            resources = resource_collector(browser_stats)
            workflows = workflow_store_factory(settings.workflow_db)
            return (
                resources,
                workflows.health_summary(),
                workflows.list(offset=0, limit=20),
                recovery.summary(max_items=20),
                extension_manager.recovery_journal.unresolved_external_count() if extension_manager is not None else 0,
            )

    (resources, workflow_health, recent_workflows, operation_recovery,
     unresolved_external_count) = await asyncio.to_thread(collect_durable_metrics)
    listed = await MCPServer.list_tools(server) if isinstance(server, MCPServer) else await server.list_tools()
    native_tools = [tool for tool in listed if not tool.name.startswith("external.")]
    extensions = (
        extension_manager.health_snapshot(profile.name, unresolved_external_count=unresolved_external_count)
        if extension_manager is not None else empty_extension_health()
    )
    extensions["enabled"] = settings.extensions_enabled or extension_manager is not None
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
    if sum(extensions[f"{state}_count"] for state in ("failed", "degraded", "unreachable")):
        degraded_reasons.append("extension_unavailable")
    if resource_pressure == "critical":
        health_status = "unhealthy"
    elif degraded_reasons:
        health_status = "degraded"
    else:
        health_status = "healthy"
    return bound_runtime_health({
        "ok": True,
        "server": settings.server_name,
        "version": settings.version,
        "tool_profile": profile.name,
        "tool_domains": list(active_domains),
        "requested_tool_domains": list(profile.domains),
        "tool_count": len(native_tools) + extensions["eligible_tool_count"],
        "unique_tool_names": len({tool.name for tool in native_tools}) == len(native_tools),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "platform_key": capabilities.platform_key,
        "windows": os.name == "nt",
        "capabilities": capabilities.as_dict(),
        "browser_optional_installed": capabilities.browser,
        "semantic_desktop_available": capabilities.semantic_ui,
        "audit_log": str(settings.audit_log),
        "execution_router": {
            "enabled": settings.execution_router_enabled,
            "policy_version": settings.execution_router_policy,
            **router_snapshot,
        },
        "operation_recovery": operation_recovery,
        "extensions": extensions,
        "snapshot_schema_version": 1,
        "runtime_generation": os.environ.get("MCP_RUNTIME_GENERATION_ID", ""),
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "collected_monotonic": time.monotonic(),
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
    })
