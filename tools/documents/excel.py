"""Native Excel inspection primitives backed by openpyxl."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from core.config import resolve_path
from core.errors import ToolError
from core.response import page
from tools.documents.common import artifact_descriptor

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
