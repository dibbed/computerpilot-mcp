from __future__ import annotations

import asyncio
import importlib
import importlib.util
from pathlib import Path

from openpyxl import Workbook

from core.registry import create_server


def _workbook(path: Path) -> None:
    workbook = Workbook()
    active = workbook.active
    active.title = "Summary"
    active["A1"] = "Name"
    active["B1"] = "Value"
    active["A2"] = "Alpha"
    active["B2"] = "=1+1"
    active.merge_cells("D1:E1")
    active["D1"] = "Merged"
    data = workbook.create_sheet("Data")
    data.sheet_state = "hidden"
    data["A1"] = 42
    workbook.save(path)
    workbook.close()


def test_excel_inspect_and_list_sheets_use_real_workbook_metadata(tmp_path: Path) -> None:
    assert importlib.util.find_spec("tools.documents.excel") is not None
    excel = importlib.import_module("tools.documents.excel")
    inspect_workbook = getattr(excel, "inspect_workbook", None)
    list_sheets = getattr(excel, "list_sheets", None)
    assert callable(inspect_workbook)
    assert callable(list_sheets)

    path = tmp_path / "sample.xlsx"
    _workbook(path)

    inspected = inspect_workbook(path, max_sheets=10)
    assert inspected["ok"] is True
    assert inspected["format"] == "xlsx"
    assert inspected["sheet_count"] == 2
    assert inspected["truncated"] is False
    assert inspected["has_macros"] is False
    assert inspected["sheets"][0] == {
        "index": 0,
        "name": "Summary",
        "state": "visible",
        "max_row": 2,
        "max_column": 5,
        "merged_range_count": 1,
        "table_count": 0,
    }
    assert inspected["sheets"][1]["name"] == "Data"
    assert inspected["sheets"][1]["state"] == "hidden"

    listed = list_sheets(path, offset=1, max_items=1)
    assert listed["total_count"] == 2
    assert listed["count"] == 1
    assert listed["items"][0]["name"] == "Data"
    assert listed["has_more"] is False


def test_excel_inspect_and_list_sheets_are_registered_tools() -> None:
    tools = asyncio.run(create_server().list_tools())
    names = {tool.name for tool in tools}
    assert {"excel_inspect", "excel_list_sheets"} <= names
