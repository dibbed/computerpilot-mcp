"""Windows compatibility tools for installed programs and services."""

from __future__ import annotations

import os
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


def register(mcp: MCPServer) -> None:
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
