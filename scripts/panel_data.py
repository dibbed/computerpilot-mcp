"""Read-only data collectors for the loopback control panel."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil

from core.config import PROJECT_ROOT, SETTINGS
from scripts.tunnel_runtime import TunnelRuntimeError, current_runtime

_JSON_TAIL_BYTES = 512 * 1024


def _safe_text(value: Any, limit: int = 2_000) -> str:
    return str(value or "")[:limit]


def tail_jsonl(path: Path, *, limit: int = 100, max_bytes: int = _JSON_TAIL_BYTES) -> list[dict[str, Any]]:
    """Read a bounded tail of JSONL records in newest-first order."""

    bounded = min(max(int(limit), 1), 500)
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(size - max_bytes, 0)
            handle.seek(start)
            raw = handle.read(max_bytes)
    except OSError:
        return []
    if start:
        newline = raw.find(b"\n")
        raw = raw[newline + 1:] if newline >= 0 else b""
    items: list[dict[str, Any]] = []
    for line in reversed(raw.splitlines()):
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError):
            continue
        if isinstance(value, dict):
            items.append(value)
            if len(items) >= bounded:
                break
    return items


def build_identity(project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return bounded build/runtime identity without reading user credentials."""

    def git(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip()[:500]

    status = git("status", "--porcelain")
    return {
        "product": "ComputerPilot MCP",
        "version": SETTINGS.version,
        "server": SETTINGS.server_name,
        "git_commit": git("rev-parse", "--short=12", "HEAD"),
        "git_branch": git("branch", "--show-current"),
        "git_clean": status == "" if status is not None else None,
        "python": platform.python_version(),
        "python_executable": str(Path(sys.executable).resolve()),
    }


def tunnel_snapshot(*, readiness_url: str | None, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return managed tunnel identity or an explicit local-http marker."""

    if readiness_url is None:
        return {"mode": "local-http", "enabled": False}
    result: dict[str, Any] = {"mode": "tunnel", "enabled": True}
    try:
        selection = current_runtime(project_root)
    except TunnelRuntimeError as exc:
        result.update({"available": False, "error": _safe_text(exc)})
        return result
    result.update({
        "available": True,
        "version": selection.version,
        "source": selection.source,
        "platform": selection.platform_key,
        "binary_path": str(selection.path),
        "warning": selection.warning,
    })
    metadata_path = project_root / ".agent_state" / "tunnel-runtime" / "current.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        metadata = {}
    if isinstance(metadata, dict):
        for key in (
            "latest_seen",
            "checked_at",
            "runtime_flavor",
            "archive_asset",
            "archive_sha256",
            "upstream_repository",
        ):
            if key in metadata:
                result[key] = metadata[key]
    return result


def system_snapshot(project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return low-cost host health for diagnosing local resource pressure."""

    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    try:
        disk = psutil.disk_usage(str(project_root.anchor or project_root))
    except OSError:
        disk = None
    boot = psutil.boot_time()
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "logical_cpu_count": psutil.cpu_count(logical=True),
        "physical_cpu_count": psutil.cpu_count(logical=False),
        "memory": {
            "total": memory.total,
            "available": memory.available,
            "used": memory.used,
            "percent": memory.percent,
        },
        "swap": {
            "total": swap.total,
            "used": swap.used,
            "free": swap.free,
            "percent": swap.percent,
        },
        "disk": None if disk is None else {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent,
        },
        "boot_time": boot,
        "uptime_seconds": max(time.time() - boot, 0.0),
    }


def _storage_category(name: str) -> str:
    key = name.casefold()
    if key == "tunnel-runtime":
        return "Tunnel Runtimes"
    if key == "backups":
        return "Backups"
    if key == "jobs":
        return "Job Outputs"
    if key == "artifacts":
        return "Artifacts"
    if key.startswith("release-") or key in {"dist", "build"}:
        return "Release Builds"
    if key == "screenshots":
        return "Screenshots"
    if key in {"search_snapshots", "search-snapshots"}:
        return "Search Snapshots"
    if key == "runtime_lifecycle":
        return "Runtime Lifecycle"
    if key.startswith("supervisor.log"):
        return "Supervisor Logs"
    if key.startswith("transport-health"):
        return "Transport History"
    if key.startswith("operation-recovery"):
        return "Recovery Journal"
    if key.startswith("audit"):
        return "Audit"
    if key.startswith("timings"):
        return "Timings"
    if key.startswith("workflow"):
        return "Workflows"
    if key == "memory":
        return "Project Memory"
    if key.endswith((".sqlite", ".sqlite3", ".db", ".sqlite-wal", ".sqlite-shm", ".sqlite3-wal", ".sqlite3-shm")):
        return "Databases"
    return "Other"


def storage_breakdown(state_dir: Path) -> dict[str, Any]:
    """Return categorized top-level .agent_state consumers without following symlinks."""

    items: list[dict[str, Any]] = []
    total = 0
    try:
        entries = list(state_dir.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        size = 0
        files = 0
        try:
            if entry.is_symlink():
                continue
            entry_type = "file" if entry.is_file() else "directory" if entry.is_dir() else "other"
            if entry_type == "file":
                size = entry.stat().st_size
                files = 1
            elif entry_type == "directory":
                for child in entry.rglob("*"):
                    try:
                        if child.is_file() and not child.is_symlink():
                            size += child.stat().st_size
                            files += 1
                    except OSError:
                        continue
            else:
                continue
        except OSError:
            continue
        total += size
        items.append({
            "name": entry.name,
            "bytes": size,
            "files": files,
            "type": entry_type,
            "category": _storage_category(entry.name),
        })
    items.sort(key=lambda item: (-int(item["bytes"]), str(item["name"]).casefold()))
    category_map: dict[str, dict[str, Any]] = {}
    for item in items:
        category = str(item["category"])
        bucket = category_map.setdefault(category, {"category": category, "bytes": 0, "files": 0, "entries": 0})
        bucket["bytes"] += int(item["bytes"])
        bucket["files"] += int(item["files"])
        bucket["entries"] += 1
    categories = sorted(
        category_map.values(),
        key=lambda item: (-int(item["bytes"]), str(item["category"]).casefold()),
    )
    return {"total": total, "items": items[:30], "count": len(items), "categories": categories}


def summarize_transport_history(
    path: Path,
    *,
    limit: int = 500,
    recent_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize persisted transport classifications and recent response deadline drops."""

    items = tail_jsonl(path, limit=limit)
    counts: dict[str, int] = {}
    incident_count = 0
    poll_stall_count = 0
    watchdog_recovery_count = 0
    restart_recommended_count = 0
    longest_poll_age_seconds = 0.0
    latest_incident: dict[str, Any] | None = None
    healthy = {"HEALTHY", "LOCAL_MODE"}

    for item in items:
        diagnosis = _safe_text(item.get("diagnosis"), 100).upper() or "UNKNOWN"
        counts[diagnosis] = counts.get(diagnosis, 0) + 1
        if diagnosis not in healthy:
            incident_count += 1
            if latest_incident is None:
                latest_incident = item
        if diagnosis == "POLL_STALLED":
            poll_stall_count += 1
        if item.get("restart_confirmed") is True:
            watchdog_recovery_count += 1
        if item.get("restart_recommended") is True:
            restart_recommended_count += 1
        control = item.get("control_plane")
        if isinstance(control, dict):
            try:
                age = float(control.get("current_poll_age_seconds") or 0.0)
            except (TypeError, ValueError):
                age = 0.0
            longest_poll_age_seconds = max(longest_poll_age_seconds, age)

    response_deadline_drop_count = 0
    for event in recent_events or []:
        message = _safe_text(event.get("message"), 4_000).casefold()
        if "response deadline" in message and "dropp" in message:
            response_deadline_drop_count += 1

    return {
        "items": items,
        "count": len(items),
        "diagnosis_counts": counts,
        "incident_count": incident_count,
        "poll_stall_count": poll_stall_count,
        "watchdog_recovery_count": watchdog_recovery_count,
        "restart_recommended_count": restart_recommended_count,
        "response_deadline_drop_count": response_deadline_drop_count,
        "longest_poll_age_seconds": round(longest_poll_age_seconds, 3),
        "latest_incident": latest_incident,
    }


def doctor_snapshot(
    *,
    runtime_health: dict[str, Any],
    transport: dict[str, Any],
    system: dict[str, Any],
    tunnel: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate bounded read-only health checks without executing diagnostic commands."""

    checks: list[dict[str, str]] = []

    def add(name: str, status: str, summary: str) -> None:
        checks.append({"name": name, "status": status, "summary": summary})

    health_status = str(runtime_health.get("health_status") or "pending").casefold()
    reasons = runtime_health.get("degraded_reasons")
    reason_text = ", ".join(str(item) for item in reasons) if isinstance(reasons, list) and reasons else "no degradation reasons"
    if health_status == "unhealthy":
        add("Runtime health", "critical", reason_text)
    elif health_status == "degraded":
        add("Runtime health", "warning", reason_text)
    elif health_status == "healthy":
        add("Runtime health", "ok", "runtime health is healthy")
    else:
        add("Runtime health", "pending", "runtime health snapshot is not available yet")

    pressure = str(runtime_health.get("resource_pressure") or "pending").casefold()
    add(
        "Resource pressure",
        (
            "critical" if pressure == "critical"
            else "warning" if pressure == "warning"
            else "ok" if pressure == "normal"
            else "pending"
        ),
        f"resource pressure: {pressure}",
    )

    unique = runtime_health.get("unique_tool_names")
    add(
        "Tool registry",
        "critical" if unique is False else "ok" if unique is True else "pending",
        (
            "tool names are unique" if unique is True
            else "duplicate tool names detected" if unique is False
            else "tool registry snapshot pending"
        ),
    )

    diagnosis = str(transport.get("diagnosis") or "UNKNOWN").upper()
    severity = str(transport.get("severity") or "").casefold()
    if diagnosis in {"HEALTHY", "LOCAL_MODE"}:
        transport_status = "ok"
    elif severity in {"critical", "error", "bad", "unhealthy"}:
        transport_status = "critical"
    elif diagnosis == "UNKNOWN":
        transport_status = "pending"
    else:
        transport_status = "warning"
    add("Transport", transport_status, diagnosis.replace("_", " ").title())

    unresolved = int(runtime_health.get("unresolved_workflow_operations") or 0)
    add(
        "Workflow recovery",
        "warning" if unresolved else "ok",
        f"{unresolved} unresolved workflow operation(s)" if unresolved else "no unresolved workflow operations",
    )

    recovery = runtime_health.get("operation_recovery")
    recovery = recovery if isinstance(recovery, dict) else {}
    uncertain = int(recovery.get("uncertain_count") or 0)
    pending = int(recovery.get("pending_count") or 0)
    add(
        "Operation recovery",
        "warning" if uncertain or pending else "ok",
        f"{uncertain} uncertain, {pending} pending" if uncertain or pending else "recovery journal is clear",
    )

    if str(tunnel.get("mode") or "") == "tunnel":
        available = tunnel.get("available")
        add(
            "Secure tunnel",
            "critical" if available is False else "ok" if available is True else "pending",
            (
                "managed tunnel runtime available" if available is True
                else "managed tunnel runtime unavailable" if available is False
                else "tunnel runtime status pending"
            ),
        )
    else:
        add("Secure tunnel", "ok", "local-http mode does not require the managed tunnel")

    memory = system.get("memory")
    memory_percent = float(memory.get("percent") or 0.0) if isinstance(memory, dict) else 0.0
    add(
        "Host memory",
        "critical" if memory_percent >= 95 else "warning" if memory_percent >= 85 else "ok",
        f"{memory_percent:.1f}% used",
    )
    disk = system.get("disk")
    if isinstance(disk, dict):
        disk_percent = float(disk.get("percent") or 0.0)
        add(
            "Host disk",
            "critical" if disk_percent >= 98 else "warning" if disk_percent >= 90 else "ok",
            f"{disk_percent:.1f}% used",
        )
    else:
        add("Host disk", "pending", "disk usage is unavailable")

    statuses = {item["status"] for item in checks}
    status = (
        "critical" if "critical" in statuses
        else "warning" if "warning" in statuses
        else "pending" if "pending" in statuses
        else "healthy"
    )
    return {
        "status": status,
        "checks": checks,
        "issue_count": sum(item["status"] in {"warning", "critical"} for item in checks),
        "pending_count": sum(item["status"] == "pending" for item in checks),
    }


_SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "password",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
)


def redact_diagnostics(value: Any, *, secret_values: tuple[str, ...] = ()) -> Any:
    """Recursively redact sensitive fields and known secret literals from diagnostics."""

    secrets = tuple(secret for secret in secret_values if secret)

    def redact(item: Any, key: str | None = None) -> Any:
        if key is not None and any(part in key.casefold() for part in _SENSITIVE_KEY_PARTS):
            return "[redacted]"
        if isinstance(item, dict):
            return {str(name): redact(child, str(name)) for name, child in item.items()}
        if isinstance(item, list):
            return [redact(child) for child in item]
        if isinstance(item, tuple):
            return [redact(child) for child in item]
        if isinstance(item, str):
            result = item
            for secret in secrets:
                result = result.replace(secret, "[redacted]")
            return result
        return item

    return redact(value)


def config_snapshot() -> dict[str, Any]:
    """Expose only non-secret runtime limits and retention/watchdog settings."""

    keys = (
        "tool_profile",
        "ast_cache_max_files",
        "ast_cache_max_bytes",
        "search_snapshot_ttl_sec",
        "search_snapshot_max_bytes",
        "search_snapshot_max_count",
        "browser_idle_sec",
        "browser_pool_idle_sec",
        "browser_max_sessions",
        "browser_max_pools",
        "max_running_jobs",
        "supervisor_drain_sec",
        "supervisor_watchdog_drain_sec",
        "transport_upstream_idle_sec",
        "tunnel_poll_stall_grace_sec",
        "tunnel_poll_stall_confirmations",
        "tunnel_poll_watchdog_enabled",
        "mcp_tool_stall_sec",
        "transport_history_interval_sec",
        "artifact_max_bytes",
        "artifact_max_age_hours",
        "artifact_max_count",
        "backup_max_bytes",
        "backup_max_age_days",
        "job_history_max_age_days",
        "job_history_max_count",
        "job_history_max_bytes",
        "workflow_history_max_count",
        "workflow_history_max_age_days",
        "workflow_db_warn_bytes",
        "audit_max_file_bytes",
        "audit_keep_files",
    )
    return {key: getattr(SETTINGS, key) for key in keys}


def memory_summary(memory_dir: Path = SETTINGS.memory_dir) -> dict[str, Any]:
    """Return memory file metadata only, never memory contents."""

    items: list[dict[str, Any]] = []
    try:
        paths = sorted(memory_dir.glob("*.json"))
    except OSError:
        paths = []
    for path in paths[:100]:
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append({
            "project": path.stem,
            "bytes": stat.st_size,
            "updated_at": stat.st_mtime,
        })
    return {"items": items, "count": len(items)}


def timing_summary(path: Path) -> dict[str, Any]:
    """Aggregate recent timing records without retaining tool arguments or results."""

    items = tail_jsonl(path, limit=500)
    if not items:
        return {"enabled": path.is_file(), "sample_count": 0, "phases": [], "tools": []}

    def percentile(values: list[float], fraction: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(round((len(ordered) - 1) * fraction), len(ordered) - 1)
        return round(ordered[index], 3)

    phase_values: dict[str, list[float]] = {}
    tool_values: dict[str, list[float]] = {}
    for item in items:
        try:
            duration = float(item.get("duration_ms", 0.0))
        except (TypeError, ValueError):
            continue
        phase = _safe_text(item.get("phase"), 100) or "unknown"
        phase_values.setdefault(phase, []).append(duration)
        tool = _safe_text(item.get("tool"), 200)
        if tool and phase in {"request_pipeline", "tool_body"}:
            tool_values.setdefault(tool, []).append(duration)

    def aggregate(mapping: dict[str, list[float]], *, name_key: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for name, values in mapping.items():
            result.append({
                name_key: name,
                "count": len(values),
                "p50_ms": percentile(values, 0.50),
                "p95_ms": percentile(values, 0.95),
                "p99_ms": percentile(values, 0.99),
                "max_ms": round(max(values), 3),
            })
        result.sort(key=lambda item: (-float(item["p95_ms"]), str(item[name_key])))
        return result

    return {
        "enabled": True,
        "sample_count": len(items),
        "phases": aggregate(phase_values, name_key="phase"),
        "tools": aggregate(tool_values, name_key="tool")[:20],
    }


def process_details(pid: int, allowed_pids: set[int]) -> dict[str, Any] | None:
    """Inspect only a process that belongs to the current runtime tree."""

    if pid not in allowed_pids:
        return None
    try:
        process = psutil.Process(pid)
        info: dict[str, Any] = {
            "pid": pid,
            "name": process.name(),
            "status": process.status(),
            "created_at": process.create_time(),
            "cpu_percent": process.cpu_percent(interval=0.05),
            "memory_rss": process.memory_info().rss,
            "threads": process.num_threads(),
        }
        try:
            info["parent_pid"] = process.ppid()
            info["command_line"] = subprocess.list2cmdline(process.cmdline())[:8_000]
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass
        try:
            io = process.io_counters()
            info["io"] = {
                "read_bytes": getattr(io, "read_bytes", 0),
                "write_bytes": getattr(io, "write_bytes", 0),
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass
        try:
            info["handles"] = process.num_handles()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, AttributeError):
            pass
        return info
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None
