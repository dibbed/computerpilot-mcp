from __future__ import annotations

import ast
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from tools.project.context import ContextIndex
from tools.project.index import build_python_metadata


def _contains_ast(value: object) -> bool:
    if isinstance(value, ast.AST):
        return True
    if is_dataclass(value) and not isinstance(value, type):
        return any(_contains_ast(getattr(value, field.name)) for field in fields(value))
    if isinstance(value, (tuple, list, set, frozenset)):
        return any(_contains_ast(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_ast(key) or _contains_ast(item) for key, item in value.items())
    return False


def test_python_metadata_records_calls_and_references_without_retaining_ast(tmp_path: Path) -> None:
    source = tmp_path / "consumer.py"
    source.write_text(
        "from app import service as svc\n\n"
        "async def run(value: str) -> None:\n"
        "    await svc.create(value)\n"
        "    local = value\n",
        encoding="utf-8",
    )

    metadata = build_python_metadata(source)

    assert ("svc.create", 4) in {(item.name, item.line) for item in metadata.calls}
    assert ("value", 4, "load") in {(item.name, item.line, item.context) for item in metadata.references}
    assert ("local", 5, "store") in {(item.name, item.line, item.context) for item in metadata.references}
    assert not _contains_ast(metadata)


def _write_project(root: Path) -> None:
    (root / "app").mkdir()
    (root / "tests").mkdir()
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "service.py").write_text(
        "def helper(value: str) -> str:\n"
        "    return value.upper()\n\n"
        "def create(value: str) -> str:\n"
        "    return helper(value)\n",
        encoding="utf-8",
    )
    (root / "consumer.py").write_text(
        "from app import service as svc\n\n"
        "def execute() -> str:\n"
        "    return svc.create('x')\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_service.py").write_text(
        "from app.service import create\n\n"
        "def test_create() -> None:\n"
        "    assert create('x') == 'X'\n",
        encoding="utf-8",
    )


def test_lookup_code_context_resolves_import_bound_callers_callees_and_tests(tmp_path: Path) -> None:
    from tools.project.context import lookup_code_context

    _write_project(tmp_path)

    result = lookup_code_context(tmp_path, "app.service.create")

    assert result["ok"] is True
    assert result["ambiguous"] is False
    assert result["definitions"][0]["qualified_name"] == "app.service.create"
    assert any(item["file"] == "consumer.py" and item["evidence"] == "import_bound" for item in result["callers"])
    assert any(item["qualified_name"] == "app.service.helper" for item in result["callees"])
    assert any(item["file"] == "tests/test_service.py" for item in result["tests"])
    assert result["complete"] is True


def test_lookup_code_context_reports_ambiguous_short_names_and_scan_limits(tmp_path: Path) -> None:
    from tools.project.context import lookup_code_context

    (tmp_path / "one.py").write_text("def save() -> None:\n    pass\n", encoding="utf-8")
    (tmp_path / "two.py").write_text("def save() -> None:\n    pass\n", encoding="utf-8")

    ambiguous = lookup_code_context(tmp_path, "save")
    limited = lookup_code_context(tmp_path, "save", max_files=1)

    assert ambiguous["ambiguous"] is True
    assert {item["qualified_name"] for item in ambiguous["definitions"]} == {"one.save", "two.save"}
    assert {item["evidence"] for item in ambiguous["definitions"]} == {"name_only"}
    assert limited["scan_truncated"] is True
    assert limited["complete"] is False


def test_lookup_code_context_paginates_relationships_across_categories(tmp_path: Path) -> None:
    from tools.project.context import lookup_code_context

    _write_project(tmp_path)

    first = lookup_code_context(tmp_path, "app.service.create", max_relationships=1, offset=0)
    second = lookup_code_context(tmp_path, "app.service.create", max_relationships=1, offset=1)

    def identities(result: dict[str, Any]) -> set[tuple[str, str]]:
        rows: set[tuple[str, str]] = set()
        for category in ("callers", "callees", "references", "imports", "tests"):
            for item in cast(list[dict[str, Any]], result[category]):
                rows.add((category, str(item)))
        return rows

    assert first["relationships_truncated"] is True
    assert first["total"] >= 2
    assert len(identities(first)) == 1
    assert len(identities(second)) == 1
    assert identities(first).isdisjoint(identities(second))


def test_context_index_refreshes_after_source_changes_and_singleflights_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.project import context

    _write_project(tmp_path)
    context.CONTEXT_CACHE.clear()
    calls = 0
    original = context._build_context_index

    def observed(root: Path, files: tuple[Path, ...], scan_truncated: bool) -> ContextIndex:
        nonlocal calls
        calls += 1
        time.sleep(0.02)
        return original(root, files, scan_truncated)

    monkeypatch.setattr(context, "_build_context_index", observed)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _item: context.lookup_code_context(tmp_path, "app.service.create"), range(12)))

    assert all(result["definitions"] for result in results)
    assert calls == 1

    service = tmp_path / "app" / "service.py"
    service.write_text(service.read_text(encoding="utf-8") + "\ndef remove() -> None:\n    pass\n", encoding="utf-8")
    refreshed = context.lookup_code_context(tmp_path, "app.service.remove")
    assert refreshed["definitions"][0]["qualified_name"] == "app.service.remove"
    assert calls == 2
