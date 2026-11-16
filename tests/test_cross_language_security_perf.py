"""Resource, inline-document and path-trust regressions for frontend evidence."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tools.project.cross_language import scan_references


def test_large_unclosed_css_rule_scan_is_linear(tmp_path: Path) -> None:
    (tmp_path / "nobrace.css").write_text("a" * 200_000, encoding="utf-8")
    start = time.perf_counter()
    result = scan_references(tmp_path, "card", max_files=3)
    assert time.perf_counter() - start < 4.0
    assert result["total_count"] == 0


def test_large_unclosed_js_calls_do_not_rescan_each_suffix(tmp_path: Path) -> None:
    (tmp_path / "unclosed.js").write_text('querySelector("x"' * 30_000, encoding="utf-8")
    start = time.perf_counter()
    result = scan_references(tmp_path, "card", max_files=3)
    assert time.perf_counter() - start < 4.0
    assert result["total_count"] == 0
    assert result["dynamic_expression_count"] > 0
    assert result["complete"] is False


def test_oversized_source_rejected_as_incomplete(tmp_path: Path) -> None:
    (tmp_path / "big.css").write_text(".card {color: red;}\n" + ("x" * (1024 * 1024)), encoding="utf-8")
    result = scan_references(tmp_path, "card")
    assert result["scan_truncated"]
    assert result["total_count"] == 0
    assert not result["complete"]


def test_invalid_utf8_file_returns_bounded_error_count(tmp_path: Path) -> None:
    (tmp_path / "file.css").write_bytes(b"\xff.card { color: red; }")
    result = scan_references(tmp_path, "card")
    assert result["unreadable_files"] == 1
    assert result["total_count"] == 0
    assert result["complete"] is False


def test_symlinked_external_sources_are_never_scanned(tmp_path: Path) -> None:
    project = tmp_path / "workspace"
    external = tmp_path / "private"
    project.mkdir()
    external.mkdir()
    (external / "source.html").write_text('<div class="card"></div>', encoding="utf-8")
    try:
        (project / "outside.html").symlink_to(external / "source.html")
        (project / "outside-dir").symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Filesystem does not permit creating test symlinks")
    result = scan_references(project, "card")
    assert result["total_count"] == 0
    assert result["files_scanned"] == 0


def test_html_inline_javascript_is_located_and_dynamic_calls_marked(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<div class="card"></div>\n'
        '<script>\n'
        'document.querySelector(".card");\n'
        'document.querySelector(dynamicVariable);\n'
        '</script>\n',
        encoding="utf-8",
    )
    result = scan_references(tmp_path, "card")
    assert ("js_selector", 3) in {(x["role"], x["line"]) for x in result["items"]}
    assert result["dynamic_expression_count"] == 1
    assert not result["complete"]


def test_nonjavascript_script_tags_are_not_executable_reference_evidence(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<script type="application/ld+json">{"query":"document.querySelector(\\".card\\")"}</script>\n',
        encoding="utf-8",
    )
    result = scan_references(tmp_path, "card")
    assert result["total_count"] == 0
    assert result["dynamic_expression_count"] == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions only")
def test_junctioned_external_directory_is_not_scanned(tmp_path: Path) -> None:
    import subprocess

    project = tmp_path / "workspace"
    external = tmp_path / "external"
    project.mkdir()
    external.mkdir()
    (external / "source.css").write_text(".card { color: red }", encoding="utf-8")
    junction = project / "junction"
    created = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(external)],
        text=True, capture_output=True, timeout=15,
    )
    if created.returncode:
        pytest.skip("Windows junction creation unavailable")
    try:
        report = scan_references(project, "card")
        assert report["total_count"] == 0
        assert report["files_scanned"] == 0
    finally:
        junction.rmdir()
