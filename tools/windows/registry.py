"""MCP registration for Windows system, resource, program, and service data."""

from __future__ import annotations

import os
import platform
import socket
import sys
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

import psutil
from mcp.server import MCPServer
from pydantic import Field

from core.errors import ToolError
from core.response import page
from core.tooling import READ_ONLY, compact_errors


def _bytes_gb(value: int) -> float:
    return round(value / 1_073_741_824, 2)


def _require_windows() -> None:
    if os.name != "nt":
        raise ToolError("windows_only", "This tool requires Windows.")


def _installed_program_rows() -> list[dict[str, Any]]:
    _require_windows()
    import winreg

    locations = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0),
    ]
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for hive, key_path, view in locations:
        try:
            root = winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with root:
            count = winreg.QueryInfoKey(root)[0]
            for index in range(count):
                try:
                    child = winreg.OpenKey(root, winreg.EnumKey(root, index))
                except OSError:
                    continue
                with child:
                    values: dict[str, Any] = {}
                    for name in ("DisplayName", "DisplayVersion", "Publisher", "InstallDate"):
                        try:
                            values[name] = winreg.QueryValueEx(child, name)[0]
                        except OSError:
                            values[name] = None
                display_name = values["DisplayName"]
                if not display_name:
                    continue
                identity = (str(display_name), values["DisplayVersion"], values["Publisher"])
                if identity in seen:
                    continue
                seen.add(identity)
                rows.append(
                    {
                        "name": display_name,
                        "version": values["DisplayVersion"],
                        "publisher": values["Publisher"],
                        "install_date": values["InstallDate"],
                    }
                )
    return rows


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("system_info")
    def system_info() -> dict[str, Any]:
        """Return a compact operating-system, Python, CPU, host, and uptime summary."""

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
        rows.sort(key=lambda item: item["device"].casefold())
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
    @compact_errors("installed_programs")
    def installed_programs(
        name_filter: Annotated[str | None, Field(max_length=200)] = None,
        publisher_filter: Annotated[str | None, Field(max_length=200)] = None,
        offset: Annotated[int, Field(ge=0, le=100_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> dict[str, Any]:
        """Read installed-program registry metadata with filtering and pagination."""

        name_needle = name_filter.casefold() if name_filter else None
        publisher_needle = publisher_filter.casefold() if publisher_filter else None
        rows = []
        for row in _installed_program_rows():
            if name_needle and name_needle not in str(row["name"]).casefold():
                continue
            if publisher_needle and publisher_needle not in str(row["publisher"] or "").casefold():
                continue
            rows.append(row)
        rows.sort(key=lambda item: str(item["name"]).casefold())
        total = len(rows)
        return {"ok": True, **page(rows[offset : offset + max_items], total=total, offset=offset, limit=max_items)}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("windows_services")
    def windows_services(
        name_filter: Annotated[str | None, Field(max_length=200)] = None,
        status_filter: Literal["running", "stopped", "paused", "start_pending", "stop_pending"] | None = None,
        offset: Annotated[int, Field(ge=0, le=100_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> dict[str, Any]:
        """List Windows services with status filtering and compact pagination."""

        _require_windows()
        needle = name_filter.casefold() if name_filter else None
        rows = []
        for service in psutil.win_service_iter():
            try:
                data = service.as_dict()
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
            searchable = f"{data.get('name', '')} {data.get('display_name', '')}".casefold()
            if needle and needle not in searchable:
                continue
            if status_filter and data.get("status") != status_filter:
                continue
            rows.append(
                {
                    "name": data.get("name"),
                    "display_name": data.get("display_name"),
                    "status": data.get("status"),
                    "start_type": data.get("start_type"),
                    "pid": data.get("pid"),
                }
            )
        rows.sort(key=lambda item: str(item["name"]).casefold())
        total = len(rows)
        return {"ok": True, **page(rows[offset : offset + max_items], total=total, offset=offset, limit=max_items)}
