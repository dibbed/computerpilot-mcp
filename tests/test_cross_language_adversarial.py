"""Adversarial cross-language evidence: never mistake dynamic code for proof."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from tools.project.cross_language import scan_references


def scan(tmp_path: Path, name: str, content: str, *, kind: Literal["class", "id"] = "class", symbol: str = "card") -> dict:
    (tmp_path / name).write_text(content, encoding="utf-8")
    return scan_references(tmp_path, symbol, kind=kind, max_files=100, max_results=50)


@pytest.mark.parametrize(
    "source",
    [
        "element.classList.add(variable);\n",
        "element.classList.remove(variable);\n",
        "element.classList.toggle(variable);\n",
        "element.classList.contains(variable);\n",
    ],
)
def test_dynamic_classlist_never_reports_complete(tmp_path: Path, source: str) -> None:
    result = scan(tmp_path, "component.ts", source)
    assert result["total_count"] == 0
    assert result["dynamic_expression_count"] >= 1
    assert result["complete"] is False


@pytest.mark.parametrize(
    "source",
    [
        'element.classList.add("card", variable);\n',
        'element.classList.toggle("card", variable);\n',
    ],
)
def test_mixed_classlist_keeps_literal_and_marks_dynamic(tmp_path: Path, source: str) -> None:
    result = scan(tmp_path, "component.js", source)
    assert any(item["role"] == "js_class_list" for item in result["items"])
    assert result["dynamic_expression_count"] >= 1
    assert result["complete"] is False


@pytest.mark.parametrize(
    "source",
    [
        'const expression = /querySelector(".card")/;\n',
        'const expression = /classList\\.add\\("card"\\)/;\n',
        'const expression = /querySelector\\(["\\\'].card["\\\']\\)/gi;\n',
        'return /querySelector(".card")/;\n',
    ],
)
def test_js_regex_literal_not_treated_as_executed_call(tmp_path: Path, source: str) -> None:
    result = scan(tmp_path, "component.js", source)
    assert result["total_count"] == 0
    assert result["dynamic_expression_count"] == 0


@pytest.mark.parametrize(
    "html",
    [
        '<div class="{{ dynamic_class }}"></div>',
        '<div id="{{ dynamicId }}"></div>',
        '<div :class="dynamicClass"></div>',
        '<div v-bind:class="dynamicClass"></div>',
        '<div [class]="dynamicClass"></div>',
    ],
)
def test_html_binding_is_explicitly_uncertain(tmp_path: Path, html: str) -> None:
    result = scan(tmp_path, "template.html", html)
    assert result["dynamic_expression_count"] >= 1
    assert result["complete"] is False


def test_jsx_spread_can_contain_unresolved_class_and_id(tmp_path: Path) -> None:
    result = scan(tmp_path, "component.tsx", "const item = <div {...props} />;")
    assert result["dynamic_expression_count"] >= 1
    assert result["complete"] is False


def test_literal_matches_remain_source_located(tmp_path: Path) -> None:
    result = scan(
        tmp_path, "component.tsx",
        'const ignore = /querySelector(".card")/;\n'
        'const real = document.querySelector(".card");\n'
        'const item = <section className="card" id="anchor" />;\n',
    )
    roles = [(x["role"], x["line"]) for x in result["items"]]
    assert ("js_selector", 2) in roles
    assert ("jsx_attribute", 3) in roles
    assert sum(role == "js_selector" for role, _line in roles) == 1


@pytest.mark.parametrize("query", ["card", "cardish", "other"])
def test_css_selector_boundaries_are_exact(tmp_path: Path, query: str) -> None:
    result = scan(tmp_path, "component.css", ".card { color: #aabbcc; }\n.cardish { color: red; }\n", symbol=query)
    assert result["total_count"] == (0 if query == "other" else 1)


def test_pagination_reports_complete_scan_separately_from_page(tmp_path: Path) -> None:
    for number in range(6):
        (tmp_path / f"page-{number}.html").write_text('<div class="card"></div>', encoding="utf-8")
    first = scan_references(tmp_path, "card", max_files=100, max_results=2, offset=0)
    second = scan_references(tmp_path, "card", max_files=100, max_results=2, offset=2)
    assert first["complete"] and second["complete"]
    assert first["has_more"] and first["next_offset"] == 2
    assert first["total_count"] == second["total_count"] == 6
    assert {x["file"] for x in first["items"]}.isdisjoint({x["file"] for x in second["items"]})


def test_generated_roots_excluded_and_scan_truncation_is_honest(tmp_path: Path) -> None:
    for dirname in ("output", "node_modules", "dist", ".agent_state"):
        directory = tmp_path / dirname
        directory.mkdir()
        (directory / "generated.tsx").write_text('<div className="card"/>', encoding="utf-8")
    (tmp_path / "src.tsx").write_text('<div className="card"/>', encoding="utf-8")
    complete = scan_references(tmp_path, "card", max_files=100)
    assert complete["total_count"] == 1 and complete["complete"]



def test_class_list_can_find_second_static_literal_without_false_dynamic(tmp_path: Path) -> None:
    result = scan(tmp_path, "component.ts", 'element.classList.add("other", "card");')
    assert result["total_count"] == 1
    assert result["dynamic_expression_count"] == 0
    assert result["complete"] is True


def test_comma_inside_literal_is_not_an_argument_delimiter(tmp_path: Path) -> None:
    result = scan(tmp_path, "component.ts", 'el.classList.add("a,b", "card");')
    assert result["total_count"] == 1
    assert result["complete"]


def test_html_mixed_static_and_template_class_preserves_static_evidence(tmp_path: Path) -> None:
    result = scan(tmp_path, "component.html", '<div class="card {{ extra }}"></div>')
    assert result["total_count"] == 1
    assert result["dynamic_expression_count"] == 1
    assert result["complete"] is False


def test_html_attribute_entity_decoding_and_css_style_locations(tmp_path: Path) -> None:
    result = scan(
        tmp_path, "component.html",
        '<div class="other&#32;card"></div>\n'
        '<style>\n'
        '.card { color: red; }\n'
        '</style>\n',
    )
    assert {(item["role"], item["line"]) for item in result["items"]} == {
        ("html_attribute", 1), ("css_selector", 3),
    }


def test_case_and_suffix_matching_are_exact(tmp_path: Path) -> None:
    html = '<div class="card cardholder Card"></div>'
    (tmp_path / "page.html").write_text(html, encoding="utf-8")
    result = scan_references(tmp_path, "card", kind="class")
    assert result["total_count"] == 1
    result = scan_references(tmp_path, "Card", kind="class")
    assert result["total_count"] == 1
    result = scan_references(tmp_path, "cardhold", kind="class")
    assert result["total_count"] == 0


def test_seeded_suffix_collision_properties(tmp_path: Path) -> None:
    import random
    import string

    rng = random.Random(20261116)
    for _index in range(60):
        suffix = "".join(rng.choices(string.ascii_lowercase + string.digits, k=8))
        symbol = "card" + suffix
        file = tmp_path / "component.css"
        file.write_text(f".{symbol} {{ color: red; }}\n.{symbol}x {{ color: blue; }}", encoding="utf-8")
        exact = scan_references(tmp_path, symbol, kind="class")
        assert exact["total_count"] == 1
        close = scan_references(tmp_path, symbol + "x", kind="class")
        assert close["total_count"] == 1
