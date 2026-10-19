from __future__ import annotations

import ast
import asyncio
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


_RELATIONSHIP_CATEGORIES = ("callers", "callees", "references", "imports", "tests")


def _flatten_relationships(result: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (category, str(item))
        for category in _RELATIONSHIP_CATEGORIES
        for item in cast(list[dict[str, Any]], result[category])
    ]


def test_lookup_code_context_paginates_relationships_across_categories(tmp_path: Path) -> None:
    from tools.project.context import lookup_code_context

    _write_project(tmp_path)

    baseline = _flatten_relationships(lookup_code_context(tmp_path, "app.service.create", max_relationships=1000))
    first = lookup_code_context(tmp_path, "app.service.create", max_relationships=1, offset=0)
    second = lookup_code_context(tmp_path, "app.service.create", max_relationships=1, offset=1)

    def identities(result: dict[str, Any]) -> set[tuple[str, str]]:
        rows: set[tuple[str, str]] = set()
        for category in _RELATIONSHIP_CATEGORIES:
            for item in cast(list[dict[str, Any]], result[category]):
                rows.add((category, str(item)))
        return rows

    assert _flatten_relationships(first) == baseline[:1]
    assert _flatten_relationships(second) == baseline[1:2]
    assert first["total"] == second["total"] == len(baseline)
    assert first["relationships_truncated"] is True
    assert first["total"] >= 2
    assert len(identities(first)) == 1
    assert len(identities(second)) == 1
    assert identities(first).isdisjoint(identities(second))


def test_lookup_code_context_paginates_every_category(tmp_path: Path) -> None:
    from tools.project.context import lookup_code_context

    _write_project(tmp_path)
    baseline = _flatten_relationships(
        lookup_code_context(tmp_path, "app.service.create", max_relationships=1000, include_source=False)
    )
    total = len(baseline)
    assert total >= 5

    for limit in (1, 2, 3):
        for offset in range(total + 2):
            page = lookup_code_context(
                tmp_path,
                "app.service.create",
                max_relationships=limit,
                offset=offset,
                include_source=False,
            )
            assert _flatten_relationships(page) == baseline[offset : offset + limit]
            assert page["count"] == sum(
                len(cast(list[dict[str, Any]], page[category])) for category in _RELATIONSHIP_CATEGORIES
            )
            assert page["total"] == total
            assert page["offset"] == offset
            assert page["limit"] == limit
            assert page["relationships_truncated"] is (offset + limit < total)
            if offset >= total:
                assert all(cast(list[dict[str, Any]], page[category]) == [] for category in _RELATIONSHIP_CATEGORIES)


def test_lookup_code_context_pagination_has_no_duplicate_or_missing_rows(tmp_path: Path) -> None:
    from tools.project.context import lookup_code_context

    _write_project(tmp_path)
    baseline = _flatten_relationships(
        lookup_code_context(tmp_path, "app.service.create", max_relationships=1000, include_source=False)
    )
    pages = [
        _flatten_relationships(
            lookup_code_context(
                tmp_path,
                "app.service.create",
                max_relationships=1,
                offset=offset,
                include_source=False,
            )
        )
        for offset in range(len(baseline) + 1)
    ]
    concatenated = [row for page in pages for row in page]

    assert concatenated == baseline
    assert len(set(concatenated)) == len(baseline)


def test_context_parse_errors_prevent_complete(tmp_path: Path) -> None:
    from tools.project.context import lookup_code_context

    (tmp_path / "valid.py").write_text("def save() -> None:\n    pass\n", encoding="utf-8")
    (tmp_path / "broken.py").write_text("def save(:\n    pass\n", encoding="utf-8")

    result = lookup_code_context(tmp_path, "valid.save")

    assert result["parse_errors"]
    assert result["complete"] is False
    assert "parse_errors" in result["incomplete_reasons"]


def test_context_name_only_is_inferred(tmp_path: Path) -> None:
    from tools.project.context import lookup_code_context

    (tmp_path / "a.py").write_text("def foo() -> None:\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def caller() -> None:\n    foo()\n", encoding="utf-8")

    result = lookup_code_context(tmp_path, "a.foo")

    callers = cast(list[dict[str, Any]], result["callers"])
    assert callers and callers[0]["evidence"] == "name_only"
    assert callers[0]["confidence"] == "inferred"
    assert callers[0]["source_kind"] == "python_ast"
    assert result["semantic_complete"] is False
    page_rows = [
        row
        for category in _RELATIONSHIP_CATEGORIES
        for row in cast(list[dict[str, Any]], result[category])
    ]
    assert result["confidence_summary"]["inferred"] == sum(
        1 for row in page_rows if row.get("confidence") == "inferred"
    )
    assert sum(result["confidence_summary"].values()) == len(page_rows)


def test_context_tests_do_not_depend_on_caller_visibility(tmp_path: Path) -> None:
    from tools.project.context import lookup_code_context

    _write_project(tmp_path)

    hidden = lookup_code_context(tmp_path, "app.service.create", include_callers=False, include_tests=True)

    assert hidden["callers"] == []
    assert any(item["file"] == "tests/test_service.py" for item in cast(list[dict[str, Any]], hidden["tests"]))


def test_context_source_limit_reports_incomplete(tmp_path: Path) -> None:
    from tools.project.context import lookup_code_context

    _write_project(tmp_path)

    result = lookup_code_context(tmp_path, "app.service.create", include_source=True, source_max_chars=0)

    assert result["complete"] is False
    assert "source_truncated" in result["incomplete_reasons"]
    assert all(item["source_truncated"] is True for item in cast(list[dict[str, Any]], result["definitions"]))


def test_context_imports_row_reports_binding_confidence(tmp_path: Path) -> None:
    from tools.project.context import lookup_code_context

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "deps.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "app" / "service.py").write_text(
        "from app import deps as d\n\ndef create() -> int:\n    return d.VALUE\n",
        encoding="utf-8",
    )

    result = lookup_code_context(tmp_path, "app.service.create")
    imports = cast(list[dict[str, Any]], result["imports"])

    assert imports
    assert {item["evidence"] for item in imports} == {"import_bound"}
    assert {item["confidence"] for item in imports} == {"static_resolved"}
    assert {item["source_kind"] for item in imports} == {"python_ast"}
    assert result["semantic_complete"] is False
    assert result["confidence_summary"]["static_resolved"] >= len(imports)


def _write_depth_chain(root: Path) -> None:
    (root / "a.py").write_text("from b import mid\n\ndef start():\n    return mid()\n", encoding="utf-8")
    (root / "b.py").write_text("from c import leaf\n\ndef mid():\n    return leaf()\n", encoding="utf-8")
    (root / "c.py").write_text("def leaf():\n    return 1\n", encoding="utf-8")


def test_context_depth_zero_one_two(tmp_path: Path) -> None:
    from tools.project.context import lookup_code_context

    _write_depth_chain(tmp_path)

    depth0 = lookup_code_context(tmp_path, "a.start", max_depth=0)
    assert depth0["query_depth"] == 0
    assert depth0["total"] == 0
    assert depth0["relationships_truncated"] is False
    assert depth0["complete"] is True
    assert all(depth0[category] == [] for category in _RELATIONSHIP_CATEGORIES)

    depth1 = lookup_code_context(tmp_path, "a.start", max_depth=1)
    assert [item["qualified_name"] for item in cast(list[dict[str, Any]], depth1["callees"])] == ["b.mid"]
    assert depth1["query_depth"] == 1
    assert all(item["depth"] == 1 for item in cast(list[dict[str, Any]], depth1["callees"]))

    depth2 = lookup_code_context(tmp_path, "a.start", max_depth=2)
    callees = cast(list[dict[str, Any]], depth2["callees"])
    assert [item["qualified_name"] for item in callees] == ["b.mid", "c.leaf"]
    assert [item["depth"] for item in callees] == [1, 2]
    assert callees[1]["via_qualified_name"] == "b.mid"

    incoming1 = lookup_code_context(tmp_path, "c.leaf", max_depth=1)
    assert [item["qualified_name"] for item in cast(list[dict[str, Any]], incoming1["callers"])] == ["b.mid"]

    incoming2 = lookup_code_context(tmp_path, "c.leaf", max_depth=2)
    callers = cast(list[dict[str, Any]], incoming2["callers"])
    assert [item["qualified_name"] for item in callers] == ["b.mid", "a.start"]
    assert [item["depth"] for item in callers] == [1, 2]
    assert callers[1]["via_qualified_name"] == "b.mid"


def test_context_depth_cycle_is_finite(tmp_path: Path) -> None:
    from tools.project.context import lookup_code_context

    (tmp_path / "a.py").write_text("from b import g\n\ndef f():\n    return g()\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("from a import f\n\ndef g():\n    return f()\n", encoding="utf-8")

    result = lookup_code_context(tmp_path, "a.f", max_depth=2)
    callees = cast(list[dict[str, Any]], result["callees"])
    names = [item["qualified_name"] for item in callees]

    assert "b.g" in names
    assert len(names) == len(set(names))
    assert len(names) <= 2
    assert result["traversal_truncated"] is False


def test_context_inferred_edge_is_not_transitively_expanded(tmp_path: Path) -> None:
    from tools.project.context import lookup_code_context

    (tmp_path / "target.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def caller():\n    return target()\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("from b import caller\n\ndef outer():\n    return caller()\n", encoding="utf-8")

    result = lookup_code_context(tmp_path, "target.target", max_depth=2)
    callers = cast(list[dict[str, Any]], result["callers"])
    names = [item["qualified_name"] for item in callers]

    assert names == ["b.caller"]
    assert callers[0]["evidence"] == "name_only"
    assert callers[0]["confidence"] == "inferred"
    assert "c.outer" not in names


def test_context_traversal_budget_reports_lower_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.project import context

    (tmp_path / "target.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    calls = "".join("    target()\n" for _ in range(10_001))
    (tmp_path / "caller.py").write_text("def caller():\n" + calls, encoding="utf-8")
    monkeypatch.setattr(context.time, "monotonic", lambda: 0.0)

    result = context.lookup_code_context(
        tmp_path, "target.target", max_depth=1, max_relationships=10, include_source=False
    )

    assert result["traversal_truncated"] is True
    assert result["total_exact"] is False
    assert result["complete"] is False
    assert "edge_budget" in result["incomplete_reasons"]
    assert result["total"] >= context.MAX_TRAVERSAL_EDGES
    assert len(cast(list[dict[str, Any]], result["callers"])) == 10


def test_context_depth_pagination_is_deterministic(tmp_path: Path) -> None:
    from tools.project.context import lookup_code_context

    _write_depth_chain(tmp_path)
    baseline = _flatten_relationships(
        lookup_code_context(tmp_path, "a.start", max_depth=2, max_relationships=1000, include_source=False)
    )
    assert len(baseline) >= 2

    for offset in range(len(baseline)):
        page = lookup_code_context(
            tmp_path,
            "a.start",
            max_depth=2,
            max_relationships=1,
            offset=offset,
            include_source=False,
        )
        assert _flatten_relationships(page) == baseline[offset : offset + 1]
        assert page["total"] == len(baseline)


def test_context_depth_helper_rejects_out_of_range_and_bool(tmp_path: Path) -> None:
    from tools.project.context import lookup_code_context

    _write_depth_chain(tmp_path)

    for invalid in (-1, 3, True):
        with pytest.raises(ValueError):
            lookup_code_context(tmp_path, "a.start", max_depth=cast(Any, invalid))


def test_code_context_schema_depth_bounds_and_bool_rejection(tmp_path: Path) -> None:
    from mcp import Client

    from core.registry import create_server

    _write_depth_chain(tmp_path)

    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            valid = await client.call_tool(
                "code_context",
                {"path": str(tmp_path), "symbol": "a.start", "max_depth": 0},
            )
            assert valid.is_error is not True
            assert cast(dict[str, Any], valid.structured_content)["query_depth"] == 0
            for invalid in (3, True):
                result = await client.call_tool(
                    "code_context",
                    {"path": str(tmp_path), "symbol": "a.start", "max_depth": invalid},
                )
                assert result.is_error is True

    asyncio.run(scenario())


def test_context_index_refreshes_after_source_changes_and_singleflights_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.project import context

    _write_project(tmp_path)
    context.CONTEXT_CACHE.clear()
    calls = 0
    original = context._build_context_index

    def observed(root: Path, files: tuple[Path, ...], scan_truncated: bool, *, deadline: float | None = None) -> ContextIndex:
        nonlocal calls
        calls += 1
        time.sleep(0.02)
        return original(root, files, scan_truncated, deadline=deadline)

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
