"""MCP registration for native document tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from core.audit import audit_action
from core.config import resolve_path
from core.tooling import MUTATING, READ_ONLY, PathArg, compact_errors
from tools.documents import docx, excel, pdf

ExpectedSha256 = Annotated[str | None, Field(pattern=r"^[0-9a-fA-F]{64}$")]
ExcelRows = Annotated[list[list[Any]], Field(min_length=1, max_length=10_000)]
DocxParagraph = Annotated[str, Field(max_length=100_000)]
DocxParagraphs = Annotated[list[DocxParagraph], Field(max_length=10_000)]


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


    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("docx_inspect")
    def docx_inspect(path: PathArg) -> dict[str, Any]:
        """Inspect DOCX structure and metadata without launching Word."""

        target = resolve_path(path)
        audit_action("docx_inspect", target=target)
        return docx.inspect_document(target)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("docx_read")
    def docx_read(
        path: PathArg,
        offset: Annotated[int, Field(ge=0, le=1_000_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=5_000)] = 100,
        max_chars: Annotated[int, Field(ge=1, le=2_000_000)] = 250_000,
    ) -> dict[str, Any]:
        """Read bounded DOCX paragraphs and table cells without launching Word."""

        target = resolve_path(path)
        audit_action("docx_read", target=target, details={"offset": offset, "max_items": max_items})
        return docx.read_document(target, offset=offset, max_items=max_items, max_chars=max_chars)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("docx_find")
    def docx_find(
        path: PathArg,
        query: Annotated[str, Field(min_length=1, max_length=4_000)],
        case_sensitive: bool = False,
        max_results: Annotated[int, Field(ge=1, le=500)] = 100,
        max_items_scanned: Annotated[int, Field(ge=1, le=1_000_000)] = 100_000,
    ) -> dict[str, Any]:
        """Find bounded text matches in DOCX paragraphs and table cells."""

        target = resolve_path(path)
        audit_action("docx_find", target=target, details={"query_chars": len(query)})
        return docx.find_text(
            target,
            query=query,
            case_sensitive=case_sensitive,
            max_results=max_results,
            max_items_scanned=max_items_scanned,
        )

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("docx_replace_text")
    def docx_replace_text(
        path: PathArg,
        old: Annotated[str, Field(min_length=1, max_length=100_000)],
        new: Annotated[str, Field(max_length=100_000)],
        replace_all: bool = True,
        include_tables: bool = True,
        backup: bool = True,
        expected_sha256: ExpectedSha256 = None,
    ) -> dict[str, Any]:
        """Atomically replace supported DOCX text while preserving unambiguous run formatting."""

        target = resolve_path(path)
        audit_action(
            "docx_replace_text",
            target=target,
            details={
                "old_chars": len(old),
                "new_chars": len(new),
                "replace_all": replace_all,
                "include_tables": include_tables,
            },
        )
        return docx.replace_text(
            target,
            old=old,
            new=new,
            replace_all=replace_all,
            include_tables=include_tables,
            backup=backup,
            expected_sha256=expected_sha256,
        )

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("docx_insert_paragraph")
    def docx_insert_paragraph(
        path: PathArg,
        text: Annotated[str, Field(max_length=100_000)],
        after_index: Annotated[int | None, Field(ge=0, le=1_000_000)] = None,
        style: Annotated[str | None, Field(max_length=255)] = None,
        backup: bool = True,
        expected_sha256: ExpectedSha256 = None,
    ) -> dict[str, Any]:
        """Atomically insert one DOCX paragraph at a deterministic top-level position."""

        target = resolve_path(path)
        audit_action(
            "docx_insert_paragraph",
            target=target,
            details={"text_chars": len(text), "after_index": after_index, "style": style},
        )
        return docx.insert_paragraph(
            target,
            text=text,
            after_index=after_index,
            style=style,
            backup=backup,
            expected_sha256=expected_sha256,
        )

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("docx_replace_table_cell")
    def docx_replace_table_cell(
        path: PathArg,
        table_index: Annotated[int, Field(ge=0, le=100_000)],
        row: Annotated[int, Field(ge=0, le=1_000_000)],
        column: Annotated[int, Field(ge=0, le=100_000)],
        text: Annotated[str, Field(max_length=100_000)],
        backup: bool = True,
        expected_sha256: ExpectedSha256 = None,
    ) -> dict[str, Any]:
        """Atomically replace one simple DOCX table cell while preserving supported run formatting."""

        target = resolve_path(path)
        audit_action(
            "docx_replace_table_cell",
            target=target,
            details={
                "table_index": table_index,
                "row": row,
                "column": column,
                "text_chars": len(text),
            },
        )
        return docx.replace_table_cell(
            target,
            table_index=table_index,
            row=row,
            column=column,
            text=text,
            backup=backup,
            expected_sha256=expected_sha256,
        )

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("docx_create")
    def docx_create(
        path: PathArg,
        paragraphs: DocxParagraphs,
        title: Annotated[str | None, Field(max_length=4_000)] = None,
        overwrite: bool = False,
        backup: bool = True,
        expected_sha256: ExpectedSha256 = None,
        create_parents: bool = False,
    ) -> dict[str, Any]:
        """Atomically create or explicitly overwrite a simple DOCX document."""

        target = resolve_path(path)
        audit_action(
            "docx_create",
            target=target,
            details={
                "paragraph_count": len(paragraphs),
                "title_chars": len(title or ""),
                "overwrite": overwrite,
            },
        )
        return docx.create_document(
            target,
            paragraphs=paragraphs,
            title=title,
            overwrite=overwrite,
            backup=backup,
            expected_sha256=expected_sha256,
            create_parents=create_parents,
        )


    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("pdf_inspect")
    def pdf_inspect(path: PathArg) -> dict[str, Any]:
        """Inspect PDF page count, encryption state, metadata, and artifact identity."""

        target = resolve_path(path)
        audit_action("pdf_inspect", target=target)
        return pdf.inspect_pdf(target)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("pdf_metadata")
    def pdf_metadata(path: PathArg) -> dict[str, Any]:
        """Read bounded PDF document metadata without extracting page text."""

        target = resolve_path(path)
        audit_action("pdf_metadata", target=target)
        return pdf.pdf_metadata(target)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("pdf_page_text")
    def pdf_page_text(
        path: PathArg,
        page_number: Annotated[int, Field(ge=1, le=10_000_000)],
        max_chars: Annotated[int, Field(ge=1, le=2_000_000)] = 250_000,
    ) -> dict[str, Any]:
        """Extract bounded text from one 1-based PDF page."""

        target = resolve_path(path)
        audit_action("pdf_page_text", target=target, details={"page_number": page_number, "max_chars": max_chars})
        return pdf.page_text(target, page_number=page_number, max_chars=max_chars)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("pdf_extract_text")
    def pdf_extract_text(
        path: PathArg,
        start_page: Annotated[int, Field(ge=1, le=10_000_000)] = 1,
        end_page: Annotated[int | None, Field(ge=1, le=10_000_000)] = None,
        max_pages: Annotated[int, Field(ge=1, le=10_000)] = 100,
        max_chars: Annotated[int, Field(ge=1, le=2_000_000)] = 250_000,
        delivery: Literal["inline", "file", "auto"] = "inline",
    ) -> dict[str, Any]:
        """Extract bounded PDF text with page, character, and delivery limits."""

        target = resolve_path(path)
        audit_action(
            "pdf_extract_text",
            target=target,
            details={
                "start_page": start_page,
                "end_page": end_page,
                "max_pages": max_pages,
                "max_chars": max_chars,
                "delivery": delivery,
            },
        )
        return pdf.extract_text(
            target,
            start_page=start_page,
            end_page=end_page,
            max_pages=max_pages,
            max_chars=max_chars,
            delivery=delivery,
        )

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("pdf_create_from_text")
    def pdf_create_from_text(
        path: PathArg,
        text: Annotated[str, Field(max_length=2_000_000)],
        title: Annotated[str | None, Field(max_length=4_000)] = None,
        page_size: Literal["A4", "LETTER"] = "A4",
        overwrite: bool = False,
        backup: bool = True,
        expected_sha256: ExpectedSha256 = None,
        create_parents: bool = False,
    ) -> dict[str, Any]:
        """Atomically create a deterministic text PDF with explicit overwrite semantics."""

        target = resolve_path(path)
        audit_action(
            "pdf_create_from_text",
            target=target,
            details={
                "text_chars": len(text),
                "title_chars": len(title or ""),
                "page_size": page_size,
                "overwrite": overwrite,
            },
        )
        return pdf.create_from_text(
            target,
            text=text,
            title=title,
            page_size=page_size,
            overwrite=overwrite,
            backup=backup,
            expected_sha256=expected_sha256,
            create_parents=create_parents,
        )

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("pdf_create_from_markdown")
    def pdf_create_from_markdown(
        path: PathArg,
        markdown: Annotated[str, Field(max_length=2_000_000)],
        title: Annotated[str | None, Field(max_length=4_000)] = None,
        page_size: Literal["A4", "LETTER"] = "A4",
        overwrite: bool = False,
        backup: bool = True,
        expected_sha256: ExpectedSha256 = None,
        create_parents: bool = False,
    ) -> dict[str, Any]:
        """Atomically create a deterministic PDF from a documented Markdown subset."""

        target = resolve_path(path)
        audit_action(
            "pdf_create_from_markdown",
            target=target,
            details={
                "markdown_chars": len(markdown),
                "title_chars": len(title or ""),
                "page_size": page_size,
                "overwrite": overwrite,
            },
        )
        return pdf.create_from_markdown(
            target,
            markdown=markdown,
            title=title,
            page_size=page_size,
            overwrite=overwrite,
            backup=backup,
            expected_sha256=expected_sha256,
            create_parents=create_parents,
        )
