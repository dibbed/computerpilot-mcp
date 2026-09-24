"""Portable system, resource, software, and service MCP tools."""

from __future__ import annotations

import os
import platform
import socket
import sys
from datetime import datetime, timezone
from typing import Annotated, Any

import psutil
from mcp.server import MCPServer
from pydantic import Field

from core.response import page
from core.tooling import READ_ONLY, compact_errors
from tools.system.backends import installed_software_rows, service_rows


def _bytes_gb(value: int) -> float:
    return round(value / 1_073_741_824, 2)


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("system_info")
    def system_info() -> dict[str, Any]:
        """Return portable OS, Python, CPU, host, and uptime metadata."""

        boot = psutil.boot_time()
        return {
            "ok": True,
            "os": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "architecture": platform.machine(),
            "hostname": socket.gethostname(),
            "python": platform.python_version(),
            "python_executable": sys.executable,
            "cpu_logical": psutil.cpu_count(logical=True),
            "cpu_physical": psutil.cpu_count(logical=False),
            "boot_time": datetime.fromtimestamp(boot, timezone.utc).isoformat(timespec="seconds"),
            "uptime_seconds": round(datetime.now(timezone.utc).timestamp() - boot),
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("cpu_usage")
    def cpu_usage(
        interval_sec: Annotated[float, Field(ge=0, le=2)] = 0.1,
        per_core: bool = False,
    ) -> dict[str, Any]:
        """Measure bounded CPU utilization, optionally per logical core."""

        value = psutil.cpu_percent(interval=interval_sec, percpu=per_core)
        return {"ok": True, "percent": value, "per_core": per_core, "interval_sec": interval_sec}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("memory_usage")
    def memory_usage() -> dict[str, Any]:
        """Return virtual-memory and swap totals in compact gigabyte units."""

        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return {
            "ok": True,
            "memory": {
                "total_gb": _bytes_gb(memory.total),
                "available_gb": _bytes_gb(memory.available),
                "used_gb": _bytes_gb(memory.used),
                "percent": memory.percent,
            },
            "swap": {
                "total_gb": _bytes_gb(swap.total),
                "used_gb": _bytes_gb(swap.used),
                "percent": swap.percent,
            },
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("disk_usage")
    def disk_usage(
        device_filter: Annotated[str | None, Field(max_length=200)] = None,
        include_all: bool = False,
        offset: Annotated[int, Field(ge=0, le=10_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        """List mounted volumes with usage pagination and inaccessible volumes skipped."""

        needle = device_filter.casefold() if device_filter else None
        rows = []
        for partition in psutil.disk_partitions(all=include_all):
            if needle and needle not in f"{partition.device} {partition.mountpoint}".casefold():
                continue
            try:
                usage = psutil.disk_usage(partition.mountpoint)
            except (PermissionError, OSError):
                continue
            rows.append(
                {
                    "device": partition.device,
                    "mountpoint": partition.mountpoint,
                    "filesystem": partition.fstype,
                    "total_gb": _bytes_gb(usage.total),
                    "free_gb": _bytes_gb(usage.free),
                    "percent": usage.percent,
                }
            )
        rows.sort(key=lambda item: f"{item['device']} {item['mountpoint']}".casefold())
        total = len(rows)
        return {"ok": True, **page(rows[offset : offset + max_items], total=total, offset=offset, limit=max_items)}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("environment_variables")
    def environment_variables(
        name_filter: Annotated[str | None, Field(max_length=200)] = None,
        include_values: bool = False,
        value_max_chars: Annotated[int | None, Field(ge=1)] = None,
        offset: Annotated[int, Field(ge=0, le=100_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> dict[str, Any]:
        """List environment names by default; values are opt-in with an optional character limit."""

        needle = name_filter.casefold() if name_filter else None
        rows = []
        for name, value in sorted(os.environ.items(), key=lambda pair: pair[0].casefold()):
            if needle and needle not in name.casefold():
                continue
            row: dict[str, Any] = {"name": name}
            if include_values:
                row["value"] = value if value_max_chars is None else value[:value_max_chars]
                row["value_truncated"] = value_max_chars is not None and len(value) > value_max_chars
            rows.append(row)
        total = len(rows)
        return {
            "ok": True,
            "values_included": include_values,
            **page(rows[offset : offset + max_items], total=total, offset=offset, limit=max_items),
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("installed_software")
    def installed_software(
        name_filter: Annotated[str | None, Field(max_length=200)] = None,
        publisher_filter: Annotated[str | None, Field(max_length=200)] = None,
        offset: Annotated[int, Field(ge=0, le=100_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=1_000)] = 100,
    ) -> dict[str, Any]:
        """List installed software through the native package/application backend when available."""

        backend, discovered = installed_software_rows()
        name_needle = name_filter.casefold() if name_filter else None
        publisher_needle = publisher_filter.casefold() if publisher_filter else None
        rows = []
        for row in discovered:
            if name_needle and name_needle not in str(row.get("name", "")).casefold():
                continue
            if publisher_needle and publisher_needle not in str(row.get("publisher") or "").casefold():
                continue
            rows.append(row)
        rows.sort(key=lambda item: str(item.get("name", "")).casefold())
        total = len(rows)
        return {
            "ok": backend != "unavailable",
            "backend": backend,
            "capability_available": backend != "unavailable",
            **page(rows[offset : offset + max_items], total=total, offset=offset, limit=max_items),
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("system_services")
    def system_services(
        name_filter: Annotated[str | None, Field(max_length=200)] = None,
        status_filter: Annotated[str | None, Field(max_length=100)] = None,
        offset: Annotated[int, Field(ge=0, le=100_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=1_000)] = 100,
    ) -> dict[str, Any]:
        """List native system services through Windows SCM, systemd/SysV, or launchd."""

        backend, discovered = service_rows()
        needle = name_filter.casefold() if name_filter else None
        status = status_filter.casefold() if status_filter else None
        rows = []
        for row in discovered:
            searchable = f"{row.get('name', '')} {row.get('display_name', '')}".casefold()
            if needle and needle not in searchable:
                continue
            if status and str(row.get("status", "")).casefold() != status:
                continue
            rows.append(row)
        rows.sort(key=lambda item: str(item.get("name", "")).casefold())
        total = len(rows)
        return {
            "ok": backend != "unavailable",
            "backend": backend,
            "capability_available": backend != "unavailable",
            **page(rows[offset : offset + max_items], total=total, offset=offset, limit=max_items),
        }
