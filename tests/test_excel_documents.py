from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
from pathlib import Path
from zipfile import ZipFile

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.table import Table, TableStyleInfo

from core.errors import ToolError
from core.registry import create_server
from core.tooling import MUTATING_TOOL_OPERATIONS


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


def test_excel_bounded_reads_find_formula_and_table_info(tmp_path: Path) -> None:
    excel = importlib.import_module("tools.documents.excel")
    read_range = getattr(excel, "read_range", None)
    find_cells = getattr(excel, "find_cells", None)
    get_formula = getattr(excel, "get_formula", None)
    table_info = getattr(excel, "table_info", None)
    assert callable(read_range)
    assert callable(find_cells)
    assert callable(get_formula)
    assert callable(table_info)

    path = tmp_path / "reads.xlsx"
    _workbook(path)
    workbook = load_workbook(path)
    sheet = workbook["Summary"]
    table = Table(displayName="SummaryTable", ref="A1:B2")
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    sheet.add_table(table)
    sheet["C3"] = "x" * 30
    workbook.save(path)
    workbook.close()

    ranged = read_range(
        path,
        sheet="Summary",
        cell_range="A1:C3",
        max_cells=20,
        max_chars=20,
    )
    assert ranged["row_count"] == 3
    assert ranged["column_count"] == 3
    assert ranged["values"][0] == ["Name", "Value", None]
    assert ranged["values"][1][1] == "=1+1"
    assert ranged["truncated"] is True
    assert ranged["truncated_cells"] == 1

    found = find_cells(
        path,
        query="alpha",
        sheet="Summary",
        case_sensitive=False,
        max_results=10,
        max_cells_scanned=100,
    )
    assert found["count"] == 1
    assert found["items"][0]["coordinate"] == "A2"
    assert found["items"][0]["value"] == "Alpha"
    assert found["truncated"] is False

    formula = get_formula(path, sheet="Summary", cell="B2")
    assert formula["is_formula"] is True
    assert formula["formula"] == "=1+1"

    tables = table_info(path, sheet="Summary", offset=0, max_items=10)
    assert tables["total_count"] == 1
    assert tables["items"][0]["name"] == "SummaryTable"
    assert tables["items"][0]["range"] == "A1:B2"


def test_excel_read_tools_are_registered() -> None:
    names = {tool.name for tool in asyncio.run(create_server().list_tools())}
    assert {
        "excel_read_range",
        "excel_find",
        "excel_get_formula",
        "excel_table_info",
    } <= names


def test_excel_guarded_mutations_round_trip_and_preserve_original_on_rejection(tmp_path: Path) -> None:
    excel = importlib.import_module("tools.documents.excel")
    write_range = getattr(excel, "write_range", None)
    clear_range = getattr(excel, "clear_range", None)
    add_sheet = getattr(excel, "add_sheet", None)
    rename_sheet = getattr(excel, "rename_sheet", None)
    delete_sheet = getattr(excel, "delete_sheet", None)
    set_formula = getattr(excel, "set_formula", None)
    create_table = getattr(excel, "create_table", None)
    for operation in (
        write_range,
        clear_range,
        add_sheet,
        rename_sheet,
        delete_sheet,
        set_formula,
        create_table,
    ):
        assert callable(operation)

    path = tmp_path / "mutations.xlsx"
    _workbook(path)

    written = write_range(
        path,
        sheet="Summary",
        cell_range="A3:B3",
        values=[["Beta", 7]],
        backup=True,
    )
    assert written["changed"] is True
    assert written["cells_written"] == 2
    assert Path(written["backup"]).is_file()
    digest = written["sha256"]

    cleared = clear_range(
        path,
        sheet="Summary",
        cell_range="B3:B3",
        backup=True,
        expected_sha256=digest,
    )
    assert cleared["cells_cleared"] == 1

    formula = set_formula(
        path,
        sheet="Summary",
        cell="B2",
        formula="=2+2",
        backup=True,
    )
    assert formula["formula"] == "=2+2"

    added = add_sheet(path, name="Temp", index=1, backup=True)
    assert added["sheet"] == "Temp"
    renamed = rename_sheet(path, sheet="Temp", new_name="Renamed", backup=True)
    assert renamed["sheet"] == "Renamed"
    deleted = delete_sheet(path, sheet="Renamed", backup=True)
    assert deleted["deleted_sheet"] == "Renamed"

    table = create_table(
        path,
        sheet="Summary",
        cell_range="A1:B3",
        table_name="SummaryTable",
        style_name="TableStyleMedium2",
        backup=True,
    )
    assert table["table"]["name"] == "SummaryTable"

    workbook = load_workbook(path, data_only=False)
    try:
        assert workbook["Summary"]["A3"].value == "Beta"
        assert workbook["Summary"]["B3"].value is None
        assert workbook["Summary"]["B2"].value == "=2+2"
        assert workbook.sheetnames == ["Summary", "Data"]
        assert "SummaryTable" in workbook["Summary"].tables
    finally:
        workbook.close()

    before = path.read_bytes()
    with pytest.raises(ToolError) as error:
        write_range(
            path,
            sheet="Summary",
            cell_range="D1:E1",
            values=[["Merged", "unsafe"]],
            backup=True,
        )
    assert error.value.code == "merged_cell_target"
    assert path.read_bytes() == before

    with pytest.raises(ToolError) as stale:
        set_formula(
            path,
            sheet="Summary",
            cell="B2",
            formula="=9+9",
            expected_sha256="0" * 64,
        )
    assert stale.value.code == "stale_file"
    assert path.read_bytes() == before


def test_excel_mutation_tools_are_registered_and_recovery_guarded() -> None:
    expected = {
        "excel_write_range",
        "excel_clear_range",
        "excel_add_sheet",
        "excel_rename_sheet",
        "excel_delete_sheet",
        "excel_set_formula",
        "excel_create_table",
    }
    names = {tool.name for tool in asyncio.run(create_server().list_tools())}
    assert expected <= names
    assert expected <= MUTATING_TOOL_OPERATIONS


def test_xlsm_mutation_preserves_vba_bytes_and_xlsx_macro_mismatch_fails_closed(tmp_path: Path) -> None:
    excel = importlib.import_module("tools.documents.excel")
    payload = b"synthetic-vba-payload"

    macro_path = tmp_path / "macro.xlsm"
    _workbook(macro_path)
    with ZipFile(macro_path, "a") as archive:
        archive.writestr("xl/vbaProject.bin", payload)
    before_digest = hashlib.sha256(payload).hexdigest()

    result = excel.write_range(
        macro_path,
        sheet="Summary",
        cell_range="A3:A3",
        values=[["MacroSafe"]],
        backup=True,
    )
    assert result["macro_preserved"] is True
    assert result["macro_sha256"] == before_digest
    with ZipFile(macro_path, "r") as archive:
        after_digest = hashlib.sha256(archive.read("xl/vbaProject.bin")).hexdigest()
    assert after_digest == before_digest

    mismatch = tmp_path / "mismatch.xlsx"
    _workbook(mismatch)
    with ZipFile(mismatch, "a") as archive:
        archive.writestr("xl/vbaProject.bin", payload)
    original = mismatch.read_bytes()

    with pytest.raises(ToolError) as error:
        excel.set_formula(
            mismatch,
            sheet="Summary",
            cell="B2",
            formula="=7+7",
            backup=True,
        )
    assert error.value.code == "macro_extension_mismatch"
    assert mismatch.read_bytes() == original

