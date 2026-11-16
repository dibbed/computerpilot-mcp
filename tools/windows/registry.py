"""Windows compatibility tools for installed programs and services."""

from __future__ import annotations

import os
from importlib import import_module
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from core.errors import ToolError
from core.response import page
from core.tooling import READ_ONLY, compact_errors
from tools.system.backends import installed_software_rows, service_rows


def _require_windows() -> None:
    if os.name != "nt":
        raise ToolError("windows_only", "This compatibility tool requires Windows.")


def _scheduled_task_status(task_path: str) -> dict[str, Any]:
    _require_windows()
    parts = task_path[1:].split("\\")
    if not task_path.startswith("\\") or any(part in {"", ".", ".."} for part in parts) or any(
        char in '/:*?"<>|' or ord(char) < 32 for char in task_path
    ):
        raise ToolError("invalid_task_path", "Provide one absolute Task Scheduler path, such as \\Folder\\TaskName.")
    try:
        com = import_module("comtypes")
        client = import_module("comtypes.client")
    except ImportError:
        raise ToolError("scheduler_unavailable", "The Windows Task Scheduler COM client is unavailable.") from None
    service = task = None
    com.CoInitialize()
    try:
        service = client.CreateObject("Schedule.Service")
        service.Connect()
        task = service.GetFolder("\\" + "\\".join(parts[:-1])).GetTask(parts[-1])
        return {
            "ok": True,
            "task_path": task_path,
            "state": {1: "disabled", 2: "queued", 3: "ready", 4: "running"}.get(int(task.State), "unknown"),
            "enabled": bool(task.Enabled),
            "last_task_result": int(task.LastTaskResult),
        }
    except com.COMError as exc:
        code = exc.hresult & 0xFFFFFFFF
        if code in {0x80070002, 0x80070003}:
            raise ToolError("scheduled_task_not_found", "Task Scheduler could not find that task.") from None
        if code == 0x80070005:
            raise ToolError("permission_denied", "The runtime cannot inspect that scheduled task.") from None
        raise ToolError("scheduler_unavailable", "The local Task Scheduler query failed.") from None
    finally:
        task = service = None
        com.CoUninitialize()


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("windows_scheduled_task")
    def windows_scheduled_task(
        task_path: Annotated[str, Field(min_length=2, max_length=512)],
    ) -> dict[str, Any]:
        """Read the state and last result of one local Windows scheduled task by its exact path."""

        return _scheduled_task_status(task_path)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("installed_programs")
    def installed_programs(
        name_filter: Annotated[str | None, Field(max_length=200)] = None,
        publisher_filter: Annotated[str | None, Field(max_length=200)] = None,
        offset: Annotated[int, Field(ge=0, le=100_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> dict[str, Any]:
        """Compatibility alias for Windows installed-program registry metadata."""

        _require_windows()
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
            "ok": backend == "windows_registry",
            "backend": backend,
            **page(rows[offset : offset + max_items], total=total, offset=offset, limit=max_items),
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("windows_services")
    def windows_services(
        name_filter: Annotated[str | None, Field(max_length=200)] = None,
        status_filter: Literal["running", "stopped", "paused", "start_pending", "stop_pending"] | None = None,
        offset: Annotated[int, Field(ge=0, le=100_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> dict[str, Any]:
        """Compatibility alias for Windows Service Control Manager data."""

        _require_windows()
        backend, discovered = service_rows()
        needle = name_filter.casefold() if name_filter else None
        rows = []
        for row in discovered:
            searchable = f"{row.get('name', '')} {row.get('display_name', '')}".casefold()
            if needle and needle not in searchable:
                continue
            if status_filter and row.get("status") != status_filter:
                continue
            rows.append(row)
        rows.sort(key=lambda item: str(item.get("name", "")).casefold())
        total = len(rows)
        return {
            "ok": backend == "windows_service_manager",
            "backend": backend,
            **page(rows[offset : offset + max_items], total=total, offset=offset, limit=max_items),
        }
