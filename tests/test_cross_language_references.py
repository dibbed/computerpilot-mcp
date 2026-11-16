"""Real static HTML/CSS/JS/TS cross-language reference evidence."""

from __future__ import annotations

import asyncio
from pathlib import Path

from mcp import Client

from core.registry import create_server
from tools.project.cross_language import scan_references


def _fixture(tmp_path: Path) -> Path:
    (tmp_path / "page.html").write_text(
        '<!-- <div class="profile-card"> -->\n'
        '<div id="user" class="profile-card extra"></div>\n'
        '<style>.profile-card { color: red; }</style>\n',
        encoding="utf-8",
    )
    (tmp_path / "style.css").write_text(
        "/* .profile-card ignored */\n"
        ".profile-card:hover, #user { color: #aabbcc; }\n"
        ".other { color: red; }\n", encoding="utf-8",
    )
    (tmp_path / "app.ts").write_text(
        'document.querySelector(".profile-card");\n'
        'document.getElementById("user");\n'
        'element.classList.add("profile-card");\n'
        '// document.querySelector(".profile-card");\n'
        'document.querySelector(dynamicSelector);\n'
        'const misleading = "document.querySelector(\\".profile-card\\")";\n',
        encoding="utf-8",
    )
    (tmp_path / "view.tsx").write_text(
        'export const Card = () => <div className="profile-card" id="user" />;\n',
        encoding="utf-8",
    )
    return tmp_path


def test_class_sources_and_static_usage_have_evidence(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    report = scan_references(root, "profile-card", kind="class", max_files=50, offset=0, max_results=40)
    records = report["items"]
    by_role = {(row["file"], row["role"]) for row in records}
    assert ("page.html", "html_attribute") in by_role
    assert ("style.css", "css_selector") in by_role
    assert ("app.ts", "js_selector") in by_role
    assert ("app.ts", "js_class_list") in by_role
    assert ("view.tsx", "jsx_attribute") in by_role
    assert all(row["confidence"] == "static_evidence" for row in records)
    assert report["dynamic_expression_count"] >= 1
    assert report["complete"] is False
    assert not any("comment" in row["role"] for row in records)
    # A misleading JavaScript string literal is not an executable selector call.
    assert sum(row["role"] == "js_selector" for row in records) == 1


def test_id_does_not_match_css_hex_color(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    report = scan_references(root, "user", kind="id", max_files=50, offset=0, max_results=30)
    assert {(row["file"], row["role"]) for row in report["items"]} >= {
        ("page.html", "html_attribute"), ("style.css", "css_selector"),
        ("app.ts", "js_id_lookup"), ("view.tsx", "jsx_attribute"),
    }
    no_color = scan_references(root, "aabbcc", kind="id", max_files=50, offset=0, max_results=30)
    assert no_color["total_count"] == 0


def test_bounded_scan_skips_generated_files_and_reports_partial(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    output = root / "output"
    output.mkdir()
    (output / "generated.html").write_text('<i class="profile-card"></i>', encoding="utf-8")
    partial = scan_references(root, "profile-card", kind="class", max_files=1, offset=0, max_results=1)
    assert partial["scan_truncated"] is True
    assert partial["complete"] is False
    full = scan_references(root, "profile-card", kind="class", max_files=50, offset=1, max_results=2)
    assert full["count"] == 2
    assert all(row["file"] != "output/generated.html" for row in full["items"])


def test_dynamic_only_is_never_presented_as_a_definite_reference(tmp_path: Path) -> None:
    (tmp_path / "dynamic.js").write_text(
        'const selector = ".profile-card";\n'
        'document.querySelector(selector);\n', encoding="utf-8",
    )
    result = scan_references(tmp_path, "profile-card", kind="class", max_files=10, offset=0, max_results=10)
    assert result["items"] == []
    assert result["dynamic_expression_count"] == 1
    assert result["complete"] is False


def test_cross_language_tool_registered_and_callable(tmp_path: Path) -> None:
    root = _fixture(tmp_path)

    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert "cross_language_references" in names
            value = await client.call_tool("cross_language_references", {
                "path": str(root), "symbol": "profile-card", "kind": "class",
                "max_files": 100, "max_results": 20,
            })
            assert not value.is_error
            payload = value.structured_content
            assert payload and payload["ok"] is True
            assert payload["total_count"] >= 4

    asyncio.run(scenario())
