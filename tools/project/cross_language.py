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
_JS_CALL = re.compile(r"\b(querySelector(?:All)?|getElementById|getElementsByClassName)\s*\(")
_JS_CLASS_CALL = re.compile(r"\bclassList\s*\.\s*(?:add|remove|toggle|contains)\s*\(")
_JS_LITERAL = re.compile(r"""\s*(["'])([^\\\r\n]*)\1\s*\Z""", re.DOTALL)
_JSX_TAG = re.compile(r"<[A-Za-z][A-Za-z0-9_.:-]*\b[^<>]{0,2000}>")
_JSX_ATTR = re.compile(r"\b(className|id)\s*=\s*(['\"])(.*?)\2", re.DOTALL)
_MAX_FILE_BYTES = 1024 * 1024
_MAX_TOTAL_BYTES = 16 * 1024 * 1024
_MAX_CALL_ARGUMENT_CHARS = 4096


def _line_offsets(text: str) -> list[int]:
    return [0, *(match.end() for match in re.finditer("\n", text))]


def _location(start: int, offsets: list[int]) -> tuple[int, int]:
    index = bisect.bisect_right(offsets, start) - 1
    return index + 1, start - offsets[index] + 1


def _regex_end(text: str, start: int) -> int | None:
    """Find an unescaped slash after a JS regex literal, respecting character classes."""
    in_class = False
    index = start + 1
    while index < len(text) and text[index] not in "\r\n":
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            index += 1
            while index < len(text) and text[index].isalpha():
                index += 1
            return index
        index += 1
    return None


def _looks_like_regex(text: str, start: int) -> bool:
    """Conservative lexical disambiguation for common regex expression starts."""
    index = start - 1
    while index >= 0 and text[index].isspace():
        index -= 1
    if index < 0:
        return True
    if text[index] in "=([{,:;!?&|+-*%^~<>":
        return True
    return bool(re.search(r"\b(?:return|throw|case|yield|await)\Z", text[max(0, index - 16):index + 1]))


def _masked(text: str, *, javascript: bool) -> str:
    """Blank strings, comments and recognizable JS regex literals, preserving offsets."""
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
        elif javascript and char == "/" and next_char != "=" and _looks_like_regex(text, index):
            matched_end = _regex_end(text, index)
            if matched_end is None:
                index += 1
                continue
            end = matched_end
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
    # Walk delimiters once: a broad "anything before {" regexp is quadratic
    # on a long CSS file without a brace.
    start = 0
    for position, char in enumerate(filtered):
        if char not in "{}":
            continue
        if char == "{":
            selector = filtered[start:position]
            if not selector.lstrip().startswith("@"):
                selector = re.sub(r"\[[^\]]*\]", lambda m: " " * len(m.group()), selector)
                for item in _CSS_TOKEN.finditer(selector):
                    if item.group(1) == marker and item.group(2) == symbol:
                        matches.append((base + start + item.start(), "css_selector"))
        start = position + 1
    return matches


class _HtmlLocations(HTMLParser):
    def __init__(self, text: str, symbol: str, kind: str) -> None:
        super().__init__(convert_charrefs=True)
        self.symbol = symbol
        self.kind = kind
        self.offsets = _line_offsets(text)
        self.matches: list[tuple[int, str]] = []
        self.style_depth = 0
        self.script_depth = 0
        self.dynamic_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        line, column = self.getpos()
        start = self.offsets[line - 1] + column
        for name, value in attrs:
            normalized = name.casefold()
            if normalized in {
                ":class", ":id", "v-bind:class", "v-bind:id",
                "x-bind:class", "x-bind:id", "[class]", "[id]",
                "class.bind", "id.bind", "class:list", "ngclass",
            }:
                self.dynamic_count += 1
            if not isinstance(value, str):
                continue
            if normalized in {"class", "id"} and any(
                marker in value for marker in ("{{", "{%", "<%", chr(36) + "{")
            ):
                self.dynamic_count += 1
            if self.kind == "class" and normalized == "class" and self.symbol in value.split():
                self.matches.append((start, "html_attribute"))
            if self.kind == "id" and normalized == "id" and self.symbol == value:
                self.matches.append((start, "html_attribute"))
        if tag.lower() == "style":
            self.style_depth += 1
        if tag.lower() == "script":
            script_type = next((value for name, value in attrs if name.casefold() == "type"), None)
            if script_type is None or script_type.casefold() in {
                "module", "text/javascript", "application/javascript",
            }:
                self.script_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style" and self.style_depth:
            self.style_depth -= 1
        if tag.lower() == "script":
            self.script_depth = 0

    def handle_data(self, data: str) -> None:
        line, column = self.getpos()
        start = self.offsets[line - 1] + column
        if self.style_depth:
            self.matches.extend(_css_locations(data, self.symbol, self.kind, base=start))
        if self.script_depth:
            matches, unresolved = _script_locations(data, self.symbol, self.kind, ".js")
            self.matches.extend((start + offset, role) for offset, role in matches)
            self.dynamic_count += unresolved


def _call_arguments(text: str, masked: str, opened: int) -> tuple[list[str], bool]:
    """Extract bounded literal arguments and flag unsupported expressions."""
    limit = min(len(text), opened + _MAX_CALL_ARGUMENT_CHARS)
    if masked.find(")", opened, limit) < 0:
        return [], True
    pieces: list[str] = []
    index = opened
    nesting = 0
    start = opened
    while index < limit:
        char = masked[index]
        if char in "([{":
            nesting += 1
        elif char in ")]}":
            if not nesting and char == ")":
                pieces.append(text[start:index])
                break
            nesting = max(nesting - 1, 0)
        elif char == "," and not nesting:
            pieces.append(text[start:index])
            start = index + 1
        index += 1
    else:
        return [], True
    literals: list[str] = []
    dynamic = False
    for piece in pieces:
        value = _JS_LITERAL.fullmatch(piece)
        if value is None:
            dynamic = True
        else:
            literals.append(value.group(2))
    return literals, dynamic


def _script_locations(text: str, symbol: str, kind: str, suffix: str) -> tuple[list[tuple[int, str]], int]:
    code = _masked(text, javascript=True)
    matches: list[tuple[int, str]] = []
    dynamic = 0
    # Read at most one bounded argument span per call. A regex with an
    # unclosed quote previously re-scanned the rest of the file per match.
    for hit in _JS_CALL.finditer(code):
        arguments, unresolved = _call_arguments(text, code, hit.end())
        if unresolved or len(arguments) != 1:
            dynamic += 1
            continue
        method = hit.group(1)
        selector = arguments[0]
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
    for hit in _JS_CLASS_CALL.finditer(code):
        arguments, unresolved = _call_arguments(text, code, hit.end())
        if unresolved:
            dynamic += 1
        if kind == "class" and symbol in arguments:
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
            if re.search(r"\{\s*\.\.\.", tag.group()):
                dynamic += 1
    return matches, dynamic


def _indirect_path(path: Path) -> bool:
    """Exclude symlinks and Windows directory junctions without following them."""
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _candidates(root: Path, max_files: int) -> tuple[list[Path], bool]:
    files: list[Path] = []
    walk_errors: list[OSError] = []
    for current, directories, names in os.walk(root, followlinks=False, onerror=walk_errors.append):
        directories[:] = sorted(
            name for name in directories
            if name not in _EXCLUDED and not name.startswith(".pytest-tmp-")
            and not _indirect_path(Path(current) / name)
        )
        for name in sorted(names):
            file = Path(current) / name
            if file.suffix.lower() not in _EXTENSIONS or _indirect_path(file):
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
            dynamic += parser.dynamic_count
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
