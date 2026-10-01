"""MCP registration for native document tools."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server import MCPServer
from pydantic import Field

from core.audit import audit_action
from core.config import resolve_path
from core.tooling import MUTATING, READ_ONLY, PathArg, compact_errors
from tools.documents import excel

ExpectedSha256 = Annotated[str | None, Field(pattern=r"^[0-9a-fA-F]{64}$")]
ExcelRows = Annotated[list[list[Any]], Field(min_length=1, max_length=10_000)]


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

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("excel_write_range")
    def excel_write_range(
        path: PathArg,
        sheet: Annotated[str, Field(min_length=1, max_length=255)],
        cell_range: Annotated[str, Field(min_length=1, max_length=200)],
        values: ExcelRows,
        backup: bool = True,
        expected_sha256: ExpectedSha256 = None,
    ) -> dict[str, Any]:
        """Atomically write one exact-shaped Excel range with backup and hash guard."""

        target = resolve_path(path)
        audit_action(
            "excel_write_range",
            target=target,
            details={"sheet": sheet, "range": cell_range, "rows": len(values), "backup": backup},
        )
        return excel.write_range(
            target,
            sheet=sheet,
            cell_range=cell_range,
            values=values,
            backup=backup,
            expected_sha256=expected_sha256,
        )

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("excel_clear_range")
    def excel_clear_range(
        path: PathArg,
        sheet: Annotated[str, Field(min_length=1, max_length=255)],
        cell_range: Annotated[str, Field(min_length=1, max_length=200)],
        backup: bool = True,
        expected_sha256: ExpectedSha256 = None,
    ) -> dict[str, Any]:
        """Atomically clear one bounded Excel range with backup and hash guard."""

        target = resolve_path(path)
        audit_action("excel_clear_range", target=target, details={"sheet": sheet, "range": cell_range})
        return excel.clear_range(
            target,
            sheet=sheet,
            cell_range=cell_range,
            backup=backup,
            expected_sha256=expected_sha256,
        )

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("excel_add_sheet")
    def excel_add_sheet(
        path: PathArg,
        name: Annotated[str, Field(min_length=1, max_length=31)],
        index: Annotated[int | None, Field(ge=0, le=100_000)] = None,
        backup: bool = True,
        expected_sha256: ExpectedSha256 = None,
    ) -> dict[str, Any]:
        """Atomically add an Excel worksheet with backup and optional hash guard."""

        target = resolve_path(path)
        audit_action("excel_add_sheet", target=target, details={"name": name, "index": index})
        return excel.add_sheet(
            target,
            name=name,
            index=index,
            backup=backup,
            expected_sha256=expected_sha256,
        )

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("excel_rename_sheet")
    def excel_rename_sheet(
        path: PathArg,
        sheet: Annotated[str, Field(min_length=1, max_length=31)],
        new_name: Annotated[str, Field(min_length=1, max_length=31)],
        backup: bool = True,
        expected_sha256: ExpectedSha256 = None,
    ) -> dict[str, Any]:
        """Atomically rename an Excel worksheet with backup and optional hash guard."""

        target = resolve_path(path)
        audit_action("excel_rename_sheet", target=target, details={"sheet": sheet, "new_name": new_name})
        return excel.rename_sheet(
            target,
            sheet=sheet,
            new_name=new_name,
            backup=backup,
            expected_sha256=expected_sha256,
        )

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("excel_delete_sheet")
    def excel_delete_sheet(
        path: PathArg,
        sheet: Annotated[str, Field(min_length=1, max_length=31)],
        backup: bool = True,
        expected_sha256: ExpectedSha256 = None,
    ) -> dict[str, Any]:
        """Atomically delete an Excel worksheet while retaining at least one sheet."""

        target = resolve_path(path)
        audit_action("excel_delete_sheet", target=target, details={"sheet": sheet})
        return excel.delete_sheet(
            target,
            sheet=sheet,
            backup=backup,
            expected_sha256=expected_sha256,
        )

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("excel_set_formula")
    def excel_set_formula(
        path: PathArg,
        sheet: Annotated[str, Field(min_length=1, max_length=255)],
        cell: Annotated[str, Field(min_length=1, max_length=100)],
        formula: Annotated[str, Field(min_length=2, max_length=32_767)],
        backup: bool = True,
        expected_sha256: ExpectedSha256 = None,
    ) -> dict[str, Any]:
        """Atomically set one Excel formula with backup and optional hash guard."""

        target = resolve_path(path)
        audit_action(
            "excel_set_formula",
            target=target,
            details={"sheet": sheet, "cell": cell, "formula_chars": len(formula)},
        )
        return excel.set_formula(
            target,
            sheet=sheet,
            cell=cell,
            formula=formula,
            backup=backup,
            expected_sha256=expected_sha256,
        )

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("excel_create_table")
    def excel_create_table(
        path: PathArg,
        sheet: Annotated[str, Field(min_length=1, max_length=255)],
        cell_range: Annotated[str, Field(min_length=1, max_length=200)],
        table_name: Annotated[str, Field(min_length=1, max_length=255)],
        style_name: Annotated[str, Field(min_length=1, max_length=255)] = "TableStyleMedium2",
        backup: bool = True,
        expected_sha256: ExpectedSha256 = None,
    ) -> dict[str, Any]:
        """Atomically create a non-overlapping Excel table with validated headers."""

        target = resolve_path(path)
        audit_action(
            "excel_create_table",
            target=target,
            details={"sheet": sheet, "range": cell_range, "table_name": table_name},
        )
        return excel.create_table(
            target,
            sheet=sheet,
            cell_range=cell_range,
            table_name=table_name,
            style_name=style_name,
            backup=backup,
            expected_sha256=expected_sha256,
        )
