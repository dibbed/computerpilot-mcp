"""Bounded static HTML/CSS/JS/TS reference evidence (not runtime DOM proof)."""

from __future__ import annotations

import bisect
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal

from core.errors import ToolError

_EXTENSIONS = frozenset({".html", ".htm", ".css", ".scss", ".less", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"})
_EXCLUDED = frozenset({
    ".git", ".worktrees", ".venv", ".agent_state", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".next", ".nuxt", ".svelte-kit", ".turbo", "node_modules",
    "dist", "build", "coverage", "output", "vendor", "__pycache__",
    "graphify-out", ".real_lsp_tools", ".real_lsp_runs",
})
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")
_CSS_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])([.#])([A-Za-z_][A-Za-z0-9_-]*)")
_CSS_RULE = re.compile(r"([^{}]+)\{")
_JS_CALL = re.compile(r"\b(querySelector(?:All)?|getElementById|getElementsByClassName)\s*\(")
_JS_STATIC = re.compile(
    r"\b(querySelector(?:All)?|getElementById|getElementsByClassName)\s*\(\s*(['\"])(.*?)\2\s*\)",
    re.DOTALL,
)
_JS_CLASS = re.compile(
    r"\bclassList\s*\.\s*(?:add|remove|toggle|contains)\s*\(\s*(['\"])(.*?)\1", re.DOTALL,
)
_JSX_TAG = re.compile(r"<[A-Za-z][A-Za-z0-9_.:-]*\b[^<>]{0,2000}>")
_JSX_ATTR = re.compile(r"\b(className|id)\s*=\s*(['\"])(.*?)\2", re.DOTALL)
_MAX_FILE_BYTES = 1024 * 1024
_MAX_TOTAL_BYTES = 16 * 1024 * 1024


def _line_offsets(text: str) -> list[int]:
    return [0, *(match.end() for match in re.finditer("\n", text))]


def _location(start: int, offsets: list[int]) -> tuple[int, int]:
    index = bisect.bisect_right(offsets, start) - 1
    return index + 1, start - offsets[index] + 1


def _masked(text: str, *, javascript: bool) -> str:
    """Blank strings and comments while retaining offsets and newlines."""
    output = list(text)
    index = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if char in ("'", '"') or (javascript and char == chr(96)):
            end = index + 1
            while end < len(text):
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == char:
                    end += 1
                    break
                end += 1
        elif char == "/" and next_char == "*":
            close = text.find("*/", index + 2)
            end = len(text) if close < 0 else close + 2
        elif javascript and char == "/" and next_char == "/":
            end = text.find("\n", index + 2)
            if end == -1:
                end = len(text)
        else:
            index += 1
            continue
        for at in range(index, min(end, len(text))):
            if text[at] != "\n":
                output[at] = " "
        index = end
    return "".join(output)


def _css_locations(text: str, symbol: str, kind: str, *, base: int = 0) -> list[tuple[int, str]]:
    filtered = _masked(text, javascript=False)
    matches: list[tuple[int, str]] = []
    marker = "." if kind == "class" else "#"
    for rule in _CSS_RULE.finditer(filtered):
        selector = rule.group(1)
        if selector.lstrip().startswith("@"):
            continue  # Feature-test arguments are not selector declarations.
        selector = re.sub(r"\[[^\]]*\]", lambda m: " " * len(m.group()), selector)
        for item in _CSS_TOKEN.finditer(selector):
            if item.group(1) == marker and item.group(2) == symbol:
                matches.append((base + rule.start(1) + item.start(), "css_selector"))
    return matches


class _HtmlLocations(HTMLParser):
    def __init__(self, text: str, symbol: str, kind: str) -> None:
        super().__init__(convert_charrefs=True)
        self.symbol = symbol
        self.kind = kind
        self.offsets = _line_offsets(text)
        self.matches: list[tuple[int, str]] = []
        self.style_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        line, column = self.getpos()
        start = self.offsets[line - 1] + column
        for name, value in attrs:
            if not isinstance(value, str):
                continue
            if self.kind == "class" and name.lower() == "class" and self.symbol in value.split():
                self.matches.append((start, "html_attribute"))
            if self.kind == "id" and name.lower() == "id" and self.symbol == value:
                self.matches.append((start, "html_attribute"))
        if tag.lower() == "style":
            self.style_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style" and self.style_depth:
            self.style_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.style_depth:
            line, column = self.getpos()
            self.matches.extend(_css_locations(
                data, self.symbol, self.kind, base=self.offsets[line - 1] + column,
            ))


def _script_locations(text: str, symbol: str, kind: str, suffix: str) -> tuple[list[tuple[int, str]], int]:
    code = _masked(text, javascript=True)
    matches: list[tuple[int, str]] = []
    static_starts: set[int] = set()
    for hit in _JS_STATIC.finditer(text):
        if code[hit.start()] == " ":
            continue
        static_starts.add(hit.start())
        method, selector = hit.group(1), hit.group(3)
        if method.startswith("querySelector"):
            filtered = re.sub(r"\[[^\]]*\]", "", selector)
            marker = "." if kind == "class" else "#"
            found = any(m.group(1) == marker and m.group(2) == symbol for m in _CSS_TOKEN.finditer(filtered))
        elif method == "getElementById":
            found = kind == "id" and selector == symbol
        else:
            found = kind == "class" and symbol in selector.split()
        if found:
            role = "js_selector" if method.startswith("querySelector") else "js_id_lookup" if kind == "id" else "js_class_lookup"
            matches.append((hit.start(), role))
    dynamic = sum(1 for m in _JS_CALL.finditer(text) if code[m.start()] != " " and m.start() not in static_starts)
    if kind == "class":
        for hit in _JS_CLASS.finditer(text):
            if code[hit.start()] != " " and hit.group(2) == symbol:
                matches.append((hit.start(), "js_class_list"))
    if suffix in {".jsx", ".tsx"}:
        for tag in _JSX_TAG.finditer(text):
            if code[tag.start()] == " ":
                continue
            for attr in _JSX_ATTR.finditer(tag.group()):
                key, value = attr.group(1), attr.group(3)
                if (kind == "class" and key == "className" and symbol in value.split()) or (
                    kind == "id" and key == "id" and value == symbol
                ):
                    matches.append((tag.start() + attr.start(), "jsx_attribute"))
            if re.search(r"\b(?:className|id)\s*=\s*\{", tag.group()):
                dynamic += 1
    return matches, dynamic


def _candidates(root: Path, max_files: int) -> tuple[list[Path], bool]:
    files: list[Path] = []
    walk_errors: list[OSError] = []
    for current, directories, names in os.walk(root, followlinks=False, onerror=walk_errors.append):
        directories[:] = sorted(
            name for name in directories
            if name not in _EXCLUDED and not name.startswith(".pytest-tmp-")
            and not (Path(current) / name).is_symlink()
        )
        for name in sorted(names):
            file = Path(current) / name
            if file.suffix.lower() not in _EXTENSIONS or file.is_symlink():
                continue
            if not file.resolve(strict=False).is_relative_to(root):
                continue
            if len(files) == max_files:
                return files, True
            files.append(file)
    return files, bool(walk_errors)


def scan_references(
    root: Path, symbol: str, *, kind: Literal["class", "id"] = "class",
    max_files: int = 2000, offset: int = 0, max_results: int = 100,
) -> dict[str, Any]:
    """Report source-located static syntax, never inferred runtime DOM identity."""
    if not root.is_dir():
        raise ToolError("cross_language_invalid_root", "The source root must be a directory.")
    if not _IDENTIFIER.fullmatch(symbol) or len(symbol) > 128:
        raise ToolError("cross_language_invalid_symbol", "Use an unescaped plain CSS identifier.")
    if kind not in ("class", "id") or not 1 <= max_files <= 10_000 or not 1 <= max_results <= 500 or offset < 0:
        raise ToolError("cross_language_invalid_options", "Invalid bounded search options.")
    root = root.resolve(strict=True)
    files, scan_truncated = _candidates(root, max_files)
    rows: list[dict[str, Any]] = []
    total = dynamic = unreadable = bytes_read = 0
    for file in files:
        try:
            stat = file.stat()
            if stat.st_size > _MAX_FILE_BYTES or bytes_read + stat.st_size > _MAX_TOTAL_BYTES:
                scan_truncated = True
                continue
            text = file.read_text(encoding="utf-8", errors="strict")
            bytes_read += stat.st_size
        except (OSError, UnicodeError, ValueError):
            unreadable += 1
            continue
        suffix = file.suffix.lower()
        if suffix in {".html", ".htm"}:
            parser = _HtmlLocations(text, symbol, kind)
            parser.feed(text)
            evidence = parser.matches
        elif suffix in {".css", ".scss", ".less"}:
            evidence = _css_locations(text, symbol, kind)
        else:
            evidence, dynamic_found = _script_locations(text, symbol, kind, suffix)
            dynamic += dynamic_found
        offsets = _line_offsets(text)
        if suffix in {".html", ".htm"}:
            language = "html"
        elif suffix in {".css", ".scss", ".less"}:
            language = "css"
        elif suffix in {".ts", ".tsx", ".mts", ".cts"}:
            language = "typescript"
        else:
            language = "javascript"
        for start, role in evidence:
            if offset <= total < offset + max_results:
                line, column = _location(start, offsets)
                rows.append({
                    "file": file.relative_to(root).as_posix(), "line": line, "column": column,
                    "role": role,
                    "language": language,
                    "confidence": "static_evidence",
                })
            total += 1
    return {
        "ok": True, "root": str(root), "symbol": symbol, "kind": kind,
        "items": rows, "count": len(rows), "total_count": total, "offset": offset,
        "has_more": offset + len(rows) < total,
        "next_offset": offset + len(rows) if offset + len(rows) < total else None,
        "files_scanned": len(files), "bytes_scanned": bytes_read,
        "dynamic_expression_count": dynamic, "unreadable_files": unreadable,
        "scan_truncated": scan_truncated, "complete": not (dynamic or unreadable or scan_truncated),
        "evidence_scope": "static_syntax_only",
    }
