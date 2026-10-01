"""MCP registration for native document tools."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server import MCPServer
from pydantic import Field

from core.audit import audit_action
from core.config import resolve_path
from core.tooling import READ_ONLY, PathArg, compact_errors
from tools.documents import excel


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("excel_inspect")
    def excel_inspect(
        path: PathArg,
        max_sheets: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        """Inspect bounded workbook metadata without launching Excel."""

        target = resolve_path(path)
        audit_action("excel_inspect", target=target)
        return excel.inspect_workbook(target, max_sheets=max_sheets)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("excel_list_sheets")
    def excel_list_sheets(
        path: PathArg,
        offset: Annotated[int, Field(ge=0, le=1_000_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> dict[str, Any]:
        """List workbook sheets with bounded pagination."""

        target = resolve_path(path)
        audit_action("excel_list_sheets", target=target)
        return excel.list_sheets(target, offset=offset, max_items=max_items)
