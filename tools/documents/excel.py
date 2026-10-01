"""Native Excel inspection primitives backed by openpyxl."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.table import Table, TableStyleInfo

from core.config import resolve_path
from core.errors import ToolError
from core.response import page
from tools.documents.common import artifact_descriptor, publish_document

EXCEL_MEDIA_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
}


def excel_path(path: str | Path) -> Path:
    target = resolve_path(path)
    suffix = target.suffix.casefold()
    if suffix not in EXCEL_MEDIA_TYPES:
        raise ToolError(
            "unsupported_excel_format",
            "Excel tools support .xlsx and .xlsm files.",
        )
    if not target.is_file():
        raise FileNotFoundError(f"File not found: {target}")
    return target


def media_type(path: Path) -> str:
    return EXCEL_MEDIA_TYPES[path.suffix.casefold()]


def has_vba_project(path: Path) -> bool:
    try:
        with ZipFile(path, "r") as archive:
            names = {name.casefold() for name in archive.namelist()}
    except (BadZipFile, OSError) as exc:
        raise ToolError("invalid_excel", f"Workbook archive is unreadable: {path}") from exc
    return "xl/vbaproject.bin" in names


def load_excel(path: str | Path, *, data_only: bool = False) -> tuple[Path, Any]:
    target = excel_path(path)
    try:
        workbook = load_workbook(
            filename=target,
            read_only=False,
            data_only=data_only,
            keep_vba=target.suffix.casefold() == ".xlsm",
        )
    except (InvalidFileException, BadZipFile, KeyError, ValueError, OSError) as exc:
        raise ToolError("invalid_excel", f"Workbook could not be opened: {target}") from exc
    return target, workbook


def _sheet_summary(sheet: Any, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "name": sheet.title,
        "state": sheet.sheet_state,
        "max_row": int(sheet.max_row),
        "max_column": int(sheet.max_column),
        "merged_range_count": len(sheet.merged_cells.ranges),
        "table_count": len(sheet.tables),
    }


def inspect_workbook(path: str | Path, *, max_sheets: int = 100) -> dict[str, Any]:
    if not 1 <= max_sheets <= 500:
        raise ToolError("invalid_max_sheets", "max_sheets must be between 1 and 500.")
    target, workbook = load_excel(path)
    try:
        total = len(workbook.worksheets)
        sheets = [
            _sheet_summary(sheet, index)
            for index, sheet in enumerate(workbook.worksheets[:max_sheets])
        ]
        descriptor = artifact_descriptor(target, media_type(target))
        return {
            "ok": True,
            "path": str(target),
            "format": target.suffix.casefold().lstrip("."),
            "bytes": descriptor["bytes"],
            "sha256": descriptor["sha256"],
            "media_type": descriptor["media_type"],
            "sheet_count": total,
            "sheets": sheets,
            "truncated": len(sheets) < total,
            "has_macros": has_vba_project(target),
        }
    finally:
        workbook.close()


def list_sheets(
    path: str | Path,
    *,
    offset: int = 0,
    max_items: int = 50,
) -> dict[str, Any]:
    if offset < 0:
        raise ToolError("invalid_offset", "offset must be zero or greater.")
    if not 1 <= max_items <= 500:
        raise ToolError("invalid_max_items", "max_items must be between 1 and 500.")
    target, workbook = load_excel(path)
    try:
        rows = [
            _sheet_summary(sheet, index)
            for index, sheet in enumerate(workbook.worksheets)
        ]
        total = len(rows)
        return {
            "ok": True,
            "path": str(target),
            **page(
                rows[offset : offset + max_items],
                total=total,
                offset=offset,
                limit=max_items,
            ),
        }
    finally:
        workbook.close()


def _worksheet(workbook: Any, sheet: str) -> Any:
    if sheet not in workbook.sheetnames:
        raise ToolError("sheet_not_found", f"Worksheet not found: {sheet!r}.")
    return workbook[sheet]


def _json_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)


def _bounded_scalar(value: Any, remaining_chars: int) -> tuple[Any, int, bool]:
    normalized = _json_scalar(value)
    if normalized is None:
        return None, 0, False
    rendered = normalized if isinstance(normalized, str) else str(normalized)
    if isinstance(normalized, str) and len(rendered) > max(remaining_chars, 0):
        allowed = max(remaining_chars, 0)
        return normalized[:allowed], allowed, True
    return normalized, min(len(rendered), max(remaining_chars, 0)), False


def read_range(
    path: str | Path,
    *,
    sheet: str,
    cell_range: str,
    max_cells: int = 5_000,
    max_chars: int = 250_000,
    data_only: bool = False,
) -> dict[str, Any]:
    if not 1 <= max_cells <= 50_000:
        raise ToolError("invalid_max_cells", "max_cells must be between 1 and 50000.")
    if not 1 <= max_chars <= 2_000_000:
        raise ToolError("invalid_max_chars", "max_chars must be between 1 and 2000000.")
    try:
        min_col, min_row, max_col, max_row = range_boundaries(cell_range)
    except (TypeError, ValueError) as exc:
        raise ToolError("invalid_cell_range", f"Invalid Excel range: {cell_range!r}.") from exc
    row_count = max_row - min_row + 1
    column_count = max_col - min_col + 1
    cell_count = row_count * column_count
    if cell_count > max_cells:
        raise ToolError(
            "range_too_large",
            f"Requested range contains {cell_count} cells; max_cells is {max_cells}.",
            hint="Read a smaller range or raise max_cells within the tool limit.",
        )

    target, workbook = load_excel(path, data_only=data_only)
    try:
        worksheet = _worksheet(workbook, sheet)
        values: list[list[Any]] = []
        remaining = max_chars
        truncated_cells = 0
        for row in worksheet.iter_rows(
            min_row=min_row,
            max_row=max_row,
            min_col=min_col,
            max_col=max_col,
        ):
            output_row: list[Any] = []
            for cell in row:
                normalized, used, truncated = _bounded_scalar(cell.value, remaining)
                remaining = max(0, remaining - used)
                truncated_cells += int(truncated)
                output_row.append(normalized)
            values.append(output_row)
        return {
            "ok": True,
            "path": str(target),
            "sheet": sheet,
            "range": cell_range,
            "row_count": row_count,
            "column_count": column_count,
            "cell_count": cell_count,
            "values": values,
            "data_only": data_only,
            "truncated": truncated_cells > 0,
            "truncated_cells": truncated_cells,
            "max_chars": max_chars,
        }
    finally:
        workbook.close()


def find_cells(
    path: str | Path,
    *,
    query: str,
    sheet: str | None = None,
    case_sensitive: bool = False,
    max_results: int = 100,
    max_cells_scanned: int = 100_000,
) -> dict[str, Any]:
    if not query:
        raise ToolError("empty_query", "query must not be empty.")
    if not 1 <= max_results <= 500:
        raise ToolError("invalid_max_results", "max_results must be between 1 and 500.")
    if not 1 <= max_cells_scanned <= 1_000_000:
        raise ToolError(
            "invalid_scan_limit",
            "max_cells_scanned must be between 1 and 1000000.",
        )

    target, workbook = load_excel(path)
    try:
        worksheets = [_worksheet(workbook, sheet)] if sheet is not None else list(workbook.worksheets)
        needle = query if case_sensitive else query.casefold()
        items: list[dict[str, Any]] = []
        scanned = 0
        truncated = False
        stop = False
        for worksheet in worksheets:
            for row in worksheet.iter_rows():
                for cell in row:
                    if scanned >= max_cells_scanned:
                        truncated = True
                        stop = True
                        break
                    scanned += 1
                    if cell.value is None:
                        continue
                    rendered = str(_json_scalar(cell.value))
                    haystack = rendered if case_sensitive else rendered.casefold()
                    if needle not in haystack:
                        continue
                    if len(items) >= max_results:
                        truncated = True
                        stop = True
                        break
                    bounded = rendered[:4_000]
                    items.append({
                        "sheet": worksheet.title,
                        "coordinate": cell.coordinate,
                        "value": bounded,
                        "value_truncated": len(bounded) < len(rendered),
                        "is_formula": isinstance(cell.value, str) and cell.value.startswith("="),
                    })
                if stop:
                    break
            if stop:
                break
        return {
            "ok": True,
            "path": str(target),
            "query": query,
            "case_sensitive": case_sensitive,
            "cells_scanned": scanned,
            "count": len(items),
            "items": items,
            "truncated": truncated,
        }
    finally:
        workbook.close()


def get_formula(path: str | Path, *, sheet: str, cell: str) -> dict[str, Any]:
    try:
        coordinate_to_tuple(cell)
    except (TypeError, ValueError) as exc:
        raise ToolError("invalid_cell", f"Invalid Excel cell coordinate: {cell!r}.") from exc
    target, workbook = load_excel(path)
    try:
        worksheet = _worksheet(workbook, sheet)
        resolved = worksheet[cell]
        value = resolved.value
        is_formula = isinstance(value, str) and value.startswith("=")
        return {
            "ok": True,
            "path": str(target),
            "sheet": sheet,
            "cell": resolved.coordinate,
            "is_formula": is_formula,
            "formula": value if is_formula else None,
        }
    finally:
        workbook.close()


def table_info(
    path: str | Path,
    *,
    sheet: str | None = None,
    offset: int = 0,
    max_items: int = 50,
) -> dict[str, Any]:
    if offset < 0:
        raise ToolError("invalid_offset", "offset must be zero or greater.")
    if not 1 <= max_items <= 500:
        raise ToolError("invalid_max_items", "max_items must be between 1 and 500.")
    target, workbook = load_excel(path)
    try:
        worksheets = [_worksheet(workbook, sheet)] if sheet is not None else list(workbook.worksheets)
        tables: list[dict[str, Any]] = []
        for worksheet in worksheets:
            for table in worksheet.tables.values():
                style = table.tableStyleInfo
                tables.append({
                    "sheet": worksheet.title,
                    "name": table.displayName,
                    "range": table.ref,
                    "style": None if style is None else style.name,
                })
        total = len(tables)
        return {
            "ok": True,
            "path": str(target),
            **page(
                tables[offset : offset + max_items],
                total=total,
                offset=offset,
                limit=max_items,
            ),
        }
    finally:
        workbook.close()


ExcelMutator = Callable[[Any], dict[str, Any]]
_INVALID_SHEET_CHARS = re.compile(r"[\\/*?:\[\]]")
_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _validate_mutation_format(target: Path) -> None:
    if target.suffix.casefold() == ".xlsm":
        raise ToolError(
            "macro_preservation_unverified",
            "XLSM mutation is disabled until VBA preservation is verified.",
        )


def _validate_sheet_name(workbook: Any, name: str, *, current: str | None = None) -> None:
    if not name or len(name) > 31 or _INVALID_SHEET_CHARS.search(name):
        raise ToolError(
            "invalid_sheet_name",
            "Worksheet names must be 1-31 characters and exclude \\ / * ? : [ ].",
        )
    wanted = name.casefold()
    for existing in workbook.sheetnames:
        if current is not None and existing == current:
            continue
        if existing.casefold() == wanted:
            raise ToolError("sheet_exists", f"Worksheet already exists: {name!r}.")


def _range_bounds(cell_range: str, *, max_cells: int) -> tuple[int, int, int, int, int]:
    try:
        min_col, min_row, max_col, max_row = range_boundaries(cell_range)
    except (TypeError, ValueError) as exc:
        raise ToolError("invalid_cell_range", f"Invalid Excel range: {cell_range!r}.") from exc
    count = (max_row - min_row + 1) * (max_col - min_col + 1)
    if count > max_cells:
        raise ToolError(
            "range_too_large",
            f"Requested range contains {count} cells; maximum is {max_cells}.",
        )
    return min_col, min_row, max_col, max_row, count


def _reject_merged_subcells(
    worksheet: Any,
    *,
    min_col: int,
    min_row: int,
    max_col: int,
    max_row: int,
) -> None:
    for row in range(min_row, max_row + 1):
        for column in range(min_col, max_col + 1):
            if isinstance(worksheet.cell(row=row, column=column), MergedCell):
                raise ToolError(
                    "merged_cell_target",
                    "Mutation intersects a non-anchor merged cell; unmerge or target only safe cells.",
                )


def _validate_cell_value(value: Any) -> None:
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise ToolError(
            "unsupported_cell_value",
            "Excel writes support null, string, integer, number, and boolean cell values.",
        )
    if isinstance(value, str) and len(value) > 32_767:
        raise ToolError("cell_value_too_long", "Excel cell strings may not exceed 32767 characters.")


def _validate_saved_workbook(staged: Path) -> None:
    _, workbook = load_excel(staged)
    workbook.close()


def _mutate_workbook(
    path: str | Path,
    mutator: ExcelMutator,
    *,
    backup: bool,
    expected_sha256: str | None,
) -> dict[str, Any]:
    target = excel_path(path)
    _validate_mutation_format(target)
    metadata: dict[str, Any] = {}

    def writer(staged: Path) -> None:
        _, workbook = load_excel(target)
        try:
            metadata.update(mutator(workbook))
            workbook.save(staged)
        finally:
            workbook.close()

    result = publish_document(
        target,
        writer=writer,
        validator=_validate_saved_workbook,
        media_type=media_type(target),
        backup=backup,
        expected_sha256=expected_sha256,
    )
    return {**result, **metadata}


def write_range(
    path: str | Path,
    *,
    sheet: str,
    cell_range: str,
    values: list[list[Any]],
    backup: bool = True,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    min_col, min_row, max_col, max_row, count = _range_bounds(cell_range, max_cells=10_000)
    expected_rows = max_row - min_row + 1
    expected_columns = max_col - min_col + 1
    if len(values) != expected_rows or any(len(row) != expected_columns for row in values):
        raise ToolError(
            "range_shape_mismatch",
            f"values must be exactly {expected_rows} rows by {expected_columns} columns.",
        )
    for row in values:
        for value in row:
            _validate_cell_value(value)

    def apply(workbook: Any) -> dict[str, Any]:
        worksheet = _worksheet(workbook, sheet)
        _reject_merged_subcells(
            worksheet,
            min_col=min_col,
            min_row=min_row,
            max_col=max_col,
            max_row=max_row,
        )
        for row_offset, row_values in enumerate(values):
            for column_offset, value in enumerate(row_values):
                worksheet.cell(
                    row=min_row + row_offset,
                    column=min_col + column_offset,
                ).value = value
        return {"sheet": sheet, "range": cell_range, "cells_written": count}

    return _mutate_workbook(
        path,
        apply,
        backup=backup,
        expected_sha256=expected_sha256,
    )


def clear_range(
    path: str | Path,
    *,
    sheet: str,
    cell_range: str,
    backup: bool = True,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    min_col, min_row, max_col, max_row, count = _range_bounds(cell_range, max_cells=50_000)

    def apply(workbook: Any) -> dict[str, Any]:
        worksheet = _worksheet(workbook, sheet)
        _reject_merged_subcells(
            worksheet,
            min_col=min_col,
            min_row=min_row,
            max_col=max_col,
            max_row=max_row,
        )
        for row in range(min_row, max_row + 1):
            for column in range(min_col, max_col + 1):
                worksheet.cell(row=row, column=column).value = None
        return {"sheet": sheet, "range": cell_range, "cells_cleared": count}

    return _mutate_workbook(
        path,
        apply,
        backup=backup,
        expected_sha256=expected_sha256,
    )


def add_sheet(
    path: str | Path,
    *,
    name: str,
    index: int | None = None,
    backup: bool = True,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    def apply(workbook: Any) -> dict[str, Any]:
        _validate_sheet_name(workbook, name)
        if index is not None and not 0 <= index <= len(workbook.worksheets):
            raise ToolError("invalid_sheet_index", "index is outside the valid worksheet insertion range.")
        worksheet = workbook.create_sheet(title=name, index=index)
        return {"sheet": worksheet.title, "index": workbook.index(worksheet)}

    return _mutate_workbook(
        path,
        apply,
        backup=backup,
        expected_sha256=expected_sha256,
    )


def rename_sheet(
    path: str | Path,
    *,
    sheet: str,
    new_name: str,
    backup: bool = True,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    def apply(workbook: Any) -> dict[str, Any]:
        worksheet = _worksheet(workbook, sheet)
        _validate_sheet_name(workbook, new_name, current=sheet)
        worksheet.title = new_name
        return {"previous_sheet": sheet, "sheet": worksheet.title}

    return _mutate_workbook(
        path,
        apply,
        backup=backup,
        expected_sha256=expected_sha256,
    )


def delete_sheet(
    path: str | Path,
    *,
    sheet: str,
    backup: bool = True,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    def apply(workbook: Any) -> dict[str, Any]:
        worksheet = _worksheet(workbook, sheet)
        if len(workbook.worksheets) <= 1:
            raise ToolError("last_sheet", "The last worksheet cannot be deleted.")
        index = workbook.index(worksheet)
        workbook.remove(worksheet)
        return {"deleted_sheet": sheet, "index": index}

    return _mutate_workbook(
        path,
        apply,
        backup=backup,
        expected_sha256=expected_sha256,
    )


def set_formula(
    path: str | Path,
    *,
    sheet: str,
    cell: str,
    formula: str,
    backup: bool = True,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    if not formula.startswith("=") or len(formula) > 32_767:
        raise ToolError(
            "invalid_formula",
            "formula must start with '=' and be no longer than 32767 characters.",
        )
    try:
        coordinate_to_tuple(cell)
    except (TypeError, ValueError) as exc:
        raise ToolError("invalid_cell", f"Invalid Excel cell coordinate: {cell!r}.") from exc

    def apply(workbook: Any) -> dict[str, Any]:
        worksheet = _worksheet(workbook, sheet)
        resolved = worksheet[cell]
        if isinstance(resolved, MergedCell):
            raise ToolError("merged_cell_target", "Cannot set a formula on a non-anchor merged cell.")
        resolved.value = formula
        return {"sheet": sheet, "cell": resolved.coordinate, "formula": formula}

    return _mutate_workbook(
        path,
        apply,
        backup=backup,
        expected_sha256=expected_sha256,
    )


def _ranges_overlap(left: str, right: str) -> bool:
    l_min_col, l_min_row, l_max_col, l_max_row = range_boundaries(left)
    r_min_col, r_min_row, r_max_col, r_max_row = range_boundaries(right)
    return not (
        l_max_col < r_min_col
        or r_max_col < l_min_col
        or l_max_row < r_min_row
        or r_max_row < l_min_row
    )


def create_table(
    path: str | Path,
    *,
    sheet: str,
    cell_range: str,
    table_name: str,
    style_name: str = "TableStyleMedium2",
    backup: bool = True,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    min_col, min_row, max_col, max_row, _ = _range_bounds(cell_range, max_cells=100_000)
    if min_row == max_row:
        raise ToolError("table_requires_data", "Excel tables require a header row and at least one data row.")
    if not _TABLE_NAME.fullmatch(table_name):
        raise ToolError(
            "invalid_table_name",
            "table_name must start with a letter or underscore and contain only letters, numbers, dot, or underscore.",
        )

    def apply(workbook: Any) -> dict[str, Any]:
        worksheet = _worksheet(workbook, sheet)
        _reject_merged_subcells(
            worksheet,
            min_col=min_col,
            min_row=min_row,
            max_col=max_col,
            max_row=max_row,
        )
        existing_names = {
            table.displayName.casefold()
            for item in workbook.worksheets
            for table in item.tables.values()
        }
        if table_name.casefold() in existing_names:
            raise ToolError("table_exists", f"Excel table already exists: {table_name!r}.")
        for table in worksheet.tables.values():
            if _ranges_overlap(cell_range, table.ref):
                raise ToolError(
                    "table_overlap",
                    f"Requested range overlaps existing table {table.displayName!r}.",
                )
        headers = [
            worksheet.cell(row=min_row, column=column).value
            for column in range(min_col, max_col + 1)
        ]
        if (
            any(not isinstance(value, str) or not value.strip() for value in headers)
            or len({str(value).casefold() for value in headers}) != len(headers)
        ):
            raise ToolError(
                "invalid_table_headers",
                "Table header cells must contain unique non-empty strings.",
            )
        table = Table(displayName=table_name, ref=cell_range)
        table.tableStyleInfo = TableStyleInfo(name=style_name, showRowStripes=True)
        worksheet.add_table(table)
        return {
            "sheet": sheet,
            "table": {
                "name": table_name,
                "range": cell_range,
                "style": style_name,
            },
        }

    return _mutate_workbook(
        path,
        apply,
        backup=backup,
        expected_sha256=expected_sha256,
    )
