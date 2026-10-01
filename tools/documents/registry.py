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

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("excel_read_range")
    def excel_read_range(
        path: PathArg,
        sheet: Annotated[str, Field(min_length=1, max_length=255)],
        cell_range: Annotated[str, Field(min_length=1, max_length=200)],
        max_cells: Annotated[int, Field(ge=1, le=50_000)] = 5_000,
        max_chars: Annotated[int, Field(ge=1, le=2_000_000)] = 250_000,
        data_only: bool = False,
    ) -> dict[str, Any]:
        """Read one bounded rectangular Excel range without launching Excel."""

        target = resolve_path(path)
        audit_action("excel_read_range", target=target, details={"sheet": sheet, "range": cell_range})
        return excel.read_range(
            target,
            sheet=sheet,
            cell_range=cell_range,
            max_cells=max_cells,
            max_chars=max_chars,
            data_only=data_only,
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("excel_find")
    def excel_find(
        path: PathArg,
        query: Annotated[str, Field(min_length=1, max_length=4_000)],
        sheet: Annotated[str | None, Field(max_length=255)] = None,
        case_sensitive: bool = False,
        max_results: Annotated[int, Field(ge=1, le=500)] = 100,
        max_cells_scanned: Annotated[int, Field(ge=1, le=1_000_000)] = 100_000,
    ) -> dict[str, Any]:
        """Find bounded cell matches across one sheet or workbook."""

        target = resolve_path(path)
        audit_action("excel_find", target=target, details={"sheet": sheet, "query_chars": len(query)})
        return excel.find_cells(
            target,
            query=query,
            sheet=sheet,
            case_sensitive=case_sensitive,
            max_results=max_results,
            max_cells_scanned=max_cells_scanned,
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("excel_get_formula")
    def excel_get_formula(
        path: PathArg,
        sheet: Annotated[str, Field(min_length=1, max_length=255)],
        cell: Annotated[str, Field(min_length=1, max_length=100)],
    ) -> dict[str, Any]:
        """Read a formula from one Excel cell without recalculating the workbook."""

        target = resolve_path(path)
        audit_action("excel_get_formula", target=target, details={"sheet": sheet, "cell": cell})
        return excel.get_formula(target, sheet=sheet, cell=cell)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("excel_table_info")
    def excel_table_info(
        path: PathArg,
        sheet: Annotated[str | None, Field(max_length=255)] = None,
        offset: Annotated[int, Field(ge=0, le=1_000_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> dict[str, Any]:
        """List bounded Excel table metadata without reading entire table bodies."""

        target = resolve_path(path)
        audit_action("excel_table_info", target=target, details={"sheet": sheet})
        return excel.table_info(target, sheet=sheet, offset=offset, max_items=max_items)
