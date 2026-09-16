"""Filesystem primitives with optional read limits and atomic edits."""

from __future__ import annotations

import ast
import codecs
import difflib
import fnmatch
import hashlib
import locale
import os
import re
import shutil
import stat
import tempfile
import textwrap
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from core.backups import BACKUP_RETENTION
from core.config import SETTINGS, ensure_runtime_dirs, resolve_path
from core.errors import ToolError
from core.executor import run_bounded
from core.response import page, sha256_bytes

COMMON_EXCLUDES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".agent_state",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "graphify-out",
    "build",
    "dist",
}

_WINDOWS_REPLACE_RETRIES = 8
_WINDOWS_TRANSIENT_REPLACE_ERRORS = frozenset({5, 32, 33})


def _replace_with_retry(source: Path, destination: Path) -> None:
    """Retry bounded transient Windows replace failures, then preserve the original error."""

    for attempt in range(_WINDOWS_REPLACE_RETRIES):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            retryable = os.name == "nt" and (
                isinstance(exc, PermissionError) or winerror in _WINDOWS_TRANSIENT_REPLACE_ERRORS
            )
            if not retryable or attempt + 1 >= _WINDOWS_REPLACE_RETRIES:
                raise
            time.sleep(min(0.01 * (2**attempt), 0.1))


def detect_encoding(path: Path) -> str:
    with path.open("rb") as handle:
        sample = handle.read(65_536)
    if sample.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if sample.startswith(codecs.BOM_UTF32_LE) or sample.startswith(codecs.BOM_UTF32_BE):
        return "utf-32"
    if sample.startswith(codecs.BOM_UTF16_LE) or sample.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    if b"\x00" in sample:
        raise ToolError("binary_file", f"File appears binary: {path}")
    try:
        sample.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(sample).best()
        if best is not None and best.encoding:
            return best.encoding
    except ImportError:
        pass
    return locale.getpreferredencoding(False) or "cp1252"


def chosen_encoding(path: Path, requested: str) -> str:
    if requested.lower() == "auto":
        return detect_encoding(path)
    try:
        codecs.lookup(requested)
    except LookupError as exc:
        raise ToolError("unknown_encoding", f"Unknown text encoding: {requested}") from exc
    return requested


def read_window(
    path_value: str,
    *,
    start_line: int,
    end_line: int | None,
    offset: int,
    max_chars: int | None,
    encoding: str,
) -> dict[str, Any]:
    path = resolve_path(path_value)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    if end_line is not None and end_line < start_line:
        raise ToolError("invalid_line_range", "end_line must be greater than or equal to start_line.")
    used_encoding = chosen_encoding(path, encoding)
    remaining_offset = offset
    pieces: list[str] = []
    captured = 0
    current_line = 1
    last_line: int | None = None
    has_more = False
    chunk_size = 65_536
    pending = ""
    with path.open("r", encoding=used_encoding, errors="strict", newline="") as handle:
        while True:
            if end_line is not None and current_line > end_line:
                break
            fragment = pending + handle.readline(chunk_size - len(pending))
            pending = ""
            if not fragment:
                break
            if fragment.endswith("\r"):
                following = handle.read(1)
                if following == "\n":
                    fragment += following
                else:
                    pending = following
            line_ended = fragment.endswith(("\n", "\r"))
            if current_line >= start_line:
                last_line = current_line
                if remaining_offset:
                    skipped = min(len(fragment), remaining_offset)
                    fragment = fragment[skipped:]
                    remaining_offset -= skipped
                if fragment:
                    if max_chars is None:
                        pieces.append(fragment)
                        captured += len(fragment)
                    else:
                        available = max_chars + 1 - captured
                        if available <= 0:
                            has_more = True
                            break
                        pieces.append(fragment[:available])
                        captured += min(len(fragment), available)
                        if len(fragment) > available or captured > max_chars:
                            has_more = True
                            break
            if line_ended:
                current_line += 1
    content = "".join(pieces)
    if max_chars is not None and len(content) > max_chars:
        content = content[:max_chars]
        has_more = True
    return {
        "ok": True,
        "path": str(path),
        "encoding": used_encoding,
        "content": content,
        "chars": len(content),
        "start_line": start_line,
        "end_line": last_line,
        "offset": offset,
        "truncated": has_more,
        "next_offset": offset + len(content) if has_more else None,
    }


def _validate_python(path: Path, content: str) -> None:
    if path.suffix.lower() != ".py":
        return
    try:
        ast.parse(content, filename=str(path))
    except SyntaxError as exc:
        location = f"line {exc.lineno}, column {exc.offset}" if exc.lineno else "unknown location"
        raise ToolError("python_syntax_error", f"Python syntax invalid at {location}: {exc.msg}") from exc


def _hash_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_file(path: Path, digest: str) -> str:
    ensure_runtime_dirs()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = SETTINGS.backup_dir / f"{stamp}_{digest[:12]}_{path.name}.bak"
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{backup.name}.", suffix=".tmp", dir=str(SETTINGS.backup_dir))
    os.close(descriptor)
    temp = Path(temp_name)
    try:
        shutil.copy2(path, temp)
        with temp.open("rb+") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temp, backup)
    finally:
        temp.unlink(missing_ok=True)
    return str(backup)


def atomic_write(
    path: Path,
    content: str,
    *,
    encoding: str,
    create_parents: bool,
    backup: bool,
    validate_python: bool,
    exclusive_create: bool = False,
) -> dict[str, Any]:
    if len(content) > SETTINGS.max_file_write_chars:
        raise ToolError(
            "content_too_large",
            f"Content has {len(content)} characters; limit is {SETTINGS.max_file_write_chars}.",
            hint="Use surgical editing or write the file in bounded chunks.",
        )
    existed = path.exists()
    if exclusive_create and (existed or path.is_symlink()):
        raise FileExistsError(f"Target already exists: {path}")
    if existed and not path.is_file():
        raise ToolError("not_a_file", f"Target is not a regular file: {path}")
    if create_parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    if not path.parent.is_dir():
        raise FileNotFoundError(f"Parent directory not found: {path.parent}")
    old_size = path.stat().st_size if existed else 0
    in_memory_limit = SETTINGS.max_file_write_chars * 4
    if not existed:
        old_raw: bytes | None = b""
    elif old_size <= in_memory_limit:
        old_raw = path.read_bytes()
    else:
        old_raw = None
    old_hash = sha256_bytes(old_raw) if old_raw is not None else _hash_path(path) if existed else None
    old_encoding = chosen_encoding(path, encoding) if existed else ("utf-8" if encoding == "auto" else encoding)
    try:
        new_raw = content.encode(old_encoding)
    except (LookupError, UnicodeEncodeError) as exc:
        raise ToolError("encoding_error", f"Cannot encode content as {old_encoding}: {exc}") from exc
    new_hash = sha256_bytes(new_raw)
    if existed and old_size == len(new_raw) and old_hash == new_hash:
        return {
            "ok": True,
            "path": str(path),
            "changed": False,
            "bytes_written": 0,
            "sha256": new_hash,
            "backup": None,
        }
    if validate_python:
        _validate_python(path, content)
    backup_path = _backup_file(path, old_hash or "unknown") if existed and backup else None
    original_mode = stat.S_IMODE(path.stat().st_mode) if existed else None
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(new_raw)
            handle.flush()
            os.fsync(handle.fileno())
        if original_mode is not None:
            os.chmod(temp_path, original_mode)
        if exclusive_create:
            os.link(temp_path, path)
        else:
            _replace_with_retry(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
    if backup_path is not None:
        BACKUP_RETENTION.schedule(Path(backup_path))
    return {
        "ok": True,
        "path": str(path),
        "changed": True,
        "created": not existed,
        "bytes_before": old_size,
        "bytes_after": len(new_raw),
        "bytes_changed": _changed_bytes(old_raw, new_raw) if old_raw is not None else None,
        "sha256_before": old_hash,
        "sha256": new_hash,
        "encoding": old_encoding,
        "backup": backup_path,
    }


def _changed_bytes(before: bytes, after: bytes) -> int:
    prefix = 0
    common = min(len(before), len(after))
    while prefix < common and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    while suffix < common - prefix and before[-1 - suffix] == after[-1 - suffix]:
        suffix += 1
    return max(len(before) - prefix - suffix, len(after) - prefix - suffix)


def diff_summary(before: str, after: str, path: Path, max_chars: int | None = None) -> dict[str, Any]:
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    added = deleted = 0
    hunks = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        hunks += 1
        deleted += i2 - i1
        added += j2 - j1
    raw_diff = "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=str(path),
            tofile=str(path),
            n=2,
        )
    )
    truncated = max_chars is not None and len(raw_diff) > max_chars
    return {
        "added_lines": added,
        "deleted_lines": deleted,
        "hunks": hunks,
        "preview": raw_diff if max_chars is None else raw_diff[:max_chars],
        "preview_truncated": truncated,
    }


def load_text(path_value: str, encoding: str = "auto") -> tuple[Path, str, str]:
    path = resolve_path(path_value)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    edit_limit = SETTINGS.max_file_write_chars * 4
    file_size = path.stat().st_size
    if file_size > edit_limit:
        raise ToolError(
            "file_too_large_for_text_edit",
            f"Text editing is limited to {edit_limit} source bytes; file is {file_size} bytes.",
            hint="Use a bounded terminal transformation for unusually large files.",
        )
    used_encoding = chosen_encoding(path, encoding)
    return path, path.read_text(encoding=used_encoding), used_encoding


def exact_replace(source: str, old: str, new: str, expected_count: int) -> tuple[str, int]:
    count = source.count(old)
    if count != expected_count:
        raise ToolError(
            "match_count_mismatch",
            f"Expected {expected_count} exact match(es), found {count}.",
            hint="Read a small surrounding range and provide a more specific old value.",
        )
    return source.replace(old, new, expected_count), count


def anchor_replace(
    source: str,
    start_marker: str,
    end_marker: str,
    replacement: str,
    include_markers: bool,
) -> str:
    start_count = source.count(start_marker)
    end_count = source.count(end_marker)
    if start_count != 1 or end_count != 1:
        raise ToolError(
            "anchor_count_mismatch",
            f"Expected one start and one end marker; found {start_count} and {end_count}.",
        )
    start = source.index(start_marker)
    end = source.index(end_marker, start + len(start_marker))
    if include_markers:
        return source[:start] + replacement + source[end + len(end_marker) :]
    return source[: start + len(start_marker)] + replacement + source[end:]


SymbolNode = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


def _qualified_nodes(tree: ast.AST) -> list[tuple[str, SymbolNode]]:
    found: list[tuple[str, SymbolNode]] = []

    def visit(body: list[ast.stmt], prefix: str = "") -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified = f"{prefix}.{node.name}" if prefix else node.name
                found.append((qualified, node))
                visit(node.body, qualified)

    visit(getattr(tree, "body", []))
    return found


def replace_symbol_body(source: str, qualified_name: str, new_body: str, kind: Literal["function", "class"]) -> str:
    tree = ast.parse(source)
    matches = []
    for name, node in _qualified_nodes(tree):
        is_kind = isinstance(node, ast.ClassDef) if kind == "class" else isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        if name == qualified_name and is_kind:
            matches.append(node)
    if len(matches) != 1:
        raise ToolError("symbol_match_error", f"Expected one {kind} named {qualified_name!r}; found {len(matches)}.")
    node = matches[0]
    body = getattr(node, "body", [])
    if not body:
        raise ToolError("empty_symbol", f"{kind.title()} has no replaceable body.")
    first = body[0]
    last = body[-1]
    if first.lineno == node.lineno:
        raise ToolError("single_line_symbol", "Single-line definitions require replace_exact or safe_refactor.")
    lines = source.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in source else "\n"
    indent = lines[first.lineno - 1][: first.col_offset]
    normalized = textwrap.dedent(new_body).strip("\r\n")
    replacement_lines = [indent + line if line else "" for line in normalized.splitlines()]
    replacement = newline.join(replacement_lines) + newline
    return "".join(lines[: first.lineno - 1]) + replacement + "".join(lines[last.end_lineno :])


def safe_refactor_content(source: str, edits: list[Any]) -> str:
    result = source
    for edit in edits:
        if edit.mode == "exact":
            if edit.old is None or edit.new is None:
                raise ToolError("invalid_edit", "Exact edits require old and new.")
            result, _ = exact_replace(result, edit.old, edit.new, edit.expected_count)
        elif edit.mode == "anchors":
            if edit.start_marker is None or edit.end_marker is None or edit.new is None:
                raise ToolError("invalid_edit", "Anchor edits require start_marker, end_marker, and new.")
            result = anchor_replace(result, edit.start_marker, edit.end_marker, edit.new, edit.include_markers)
        elif edit.mode in {"function", "class"}:
            if edit.symbol is None or edit.new_body is None:
                raise ToolError("invalid_edit", f"{edit.mode.title()} edits require symbol and new_body.")
            result = replace_symbol_body(result, edit.symbol, edit.new_body, edit.mode)
        else:
            raise ToolError("invalid_edit_mode", f"Unsupported edit mode: {edit.mode}")
    return result


def apply_safe_refactor(
    path_value: str,
    edits: list[Any],
    *,
    encoding: str,
    validation_command: list[str] | None,
    validation_cwd: str | None,
    timeout_sec: float,
) -> dict[str, Any]:
    path, before, used_encoding = load_text(path_value, encoding)
    _validate_python(path, before)
    after = safe_refactor_content(before, edits)
    _validate_python(path, after)
    summary = diff_summary(before, after, path)
    write_result = atomic_write(
        path,
        after,
        encoding=used_encoding,
        create_parents=False,
        backup=True,
        validate_python=True,
    )
    if validation_command:
        cwd = resolve_path(validation_cwd) if validation_cwd else path.parent
        check = run_bounded(
            validation_command,
            cwd=cwd,
            timeout_sec=timeout_sec,
            stdout_limit=None,
            stderr_limit=None,
            output_mode="tail",
        )
        if not check["ok"]:
            atomic_write(
                path,
                before,
                encoding=used_encoding,
                create_parents=False,
                backup=False,
                validate_python=True,
            )
            raise ToolError(
                "validation_failed_rolled_back",
                f"Validation exited {check['exit_code']}; edit was rolled back.",
                hint=check["stderr"]["text"] or check["stdout"]["text"],
            )
        write_result["validation"] = {
            "ok": True,
            "exit_code": check["exit_code"],
            "duration_ms": check["duration_ms"],
        }
    write_result["diff"] = summary
    write_result["edit_count"] = len(edits)
    return write_result


def directory_listing(
    path_value: str,
    *,
    offset: int,
    limit: int,
    pattern: str | None,
    include_hidden: bool,
    sort_by: Literal["name", "size", "modified"],
    scan_limit: int,
) -> dict[str, Any]:
    path = resolve_path(path_value)
    if not path.is_dir():
        raise NotADirectoryError(f"Directory not found: {path}")
    entries: list[dict[str, Any]] = []
    scan_truncated = False
    with os.scandir(path) as iterator:
        for entry in iterator:
            if not include_hidden and entry.name.startswith("."):
                continue
            if pattern and not fnmatch.fnmatch(entry.name, pattern):
                continue
            if len(entries) >= scan_limit:
                scan_truncated = True
                break
            try:
                info = entry.stat(follow_symlinks=False)
                item = {
                    "name": entry.name,
                    "type": "directory" if entry.is_dir(follow_symlinks=False) else "file",
                    "size": info.st_size if entry.is_file(follow_symlinks=False) else None,
                    "modified": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                }
            except OSError:
                item = {"name": entry.name, "type": "unknown", "size": None, "modified": None}
            entries.append(item)
    key = {
        "name": lambda item: item["name"].casefold(),
        "size": lambda item: item["size"] or -1,
        "modified": lambda item: item["modified"] or "",
    }[sort_by]
    entries.sort(key=key)
    total = len(entries)
    result = page(entries[offset : offset + limit], total=total, offset=offset, limit=limit)
    result["truncated"] = bool(result["truncated"] or scan_truncated)
    return {"ok": True, "path": str(path), "scan_truncated": scan_truncated, **result}


@dataclass(slots=True)
class _WalkProgress:
    scanned_files: int = 0
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class StreamingNameSearchPage:
    items: tuple[Path, ...]
    has_more: bool
    continuation_available: bool
    scan_truncated: bool
    scanned_files: int


def _iter_files(
    root: Path,
    include_hidden: bool,
    max_scan_files: int,
    exclude_common: bool,
    progress: _WalkProgress,
) -> Iterator[Path]:
    """Yield files incrementally while preserving the existing scan-limit semantics."""

    for current, directories, files in os.walk(root):
        directories[:] = [
            name
            for name in directories
            if (include_hidden or not name.startswith(".")) and (not exclude_common or name not in COMMON_EXCLUDES)
        ]
        for name in files:
            if not include_hidden and name.startswith("."):
                continue
            progress.scanned_files += 1
            if progress.scanned_files >= max_scan_files:
                progress.truncated = True
            yield Path(current) / name
            if progress.truncated:
                return


def _walk_files(root: Path, include_hidden: bool, max_scan_files: int, exclude_common: bool) -> tuple[list[Path], bool]:
    progress = _WalkProgress()
    found = list(_iter_files(root, include_hidden, max_scan_files, exclude_common, progress))
    return found, progress.truncated


def _name_matcher(query: str, *, regex: bool, case_sensitive: bool) -> tuple[re.Pattern[str] | None, str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    return (re.compile(query, flags) if regex else None, query if case_sensitive else query.casefold())


def _name_matches(
    path: Path,
    root: Path,
    *,
    compiled: re.Pattern[str] | None,
    needle: str,
    case_sensitive: bool,
    glob: str | None,
) -> bool:
    relative = str(path.relative_to(root))
    if glob and not fnmatch.fnmatch(relative, glob):
        return False
    candidate = relative if case_sensitive else relative.casefold()
    return bool(compiled.search(relative)) if compiled else needle in candidate


def search_by_name(
    root: Path,
    query: str,
    *,
    regex: bool,
    case_sensitive: bool,
    glob: str | None,
    include_hidden: bool,
    max_scan_files: int,
    exclude_common: bool,
) -> tuple[list[Path], bool]:
    """Run an exact-count name search without materializing every scanned path."""

    progress = _WalkProgress()
    compiled, needle = _name_matcher(query, regex=regex, case_sensitive=case_sensitive)
    matches: list[Path] = []
    for path in _iter_files(root, include_hidden, max_scan_files, exclude_common, progress):
        if _name_matches(
            path,
            root,
            compiled=compiled,
            needle=needle,
            case_sensitive=case_sensitive,
            glob=glob,
        ):
            matches.append(path)
    return matches, progress.truncated


def search_by_name_streaming(
    root: Path,
    query: str,
    *,
    regex: bool,
    case_sensitive: bool,
    glob: str | None,
    include_hidden: bool,
    max_scan_files: int,
    exclude_common: bool,
    offset: int,
    limit: int,
) -> StreamingNameSearchPage:
    """Return one traversal-order page and stop after finding one extra match."""

    progress = _WalkProgress()
    compiled, needle = _name_matcher(query, regex=regex, case_sensitive=case_sensitive)
    skipped = 0
    items: list[Path] = []
    found_extra = False
    for path in _iter_files(root, include_hidden, max_scan_files, exclude_common, progress):
        if not _name_matches(
            path,
            root,
            compiled=compiled,
            needle=needle,
            case_sensitive=case_sensitive,
            glob=glob,
        ):
            continue
        if skipped < offset:
            skipped += 1
            continue
        if len(items) < limit:
            items.append(path)
            continue
        found_extra = True
        break
    has_more = found_extra or progress.truncated
    return StreamingNameSearchPage(
        items=tuple(items),
        has_more=has_more,
        continuation_available=found_extra,
        scan_truncated=progress.truncated,
        scanned_files=progress.scanned_files,
    )


def search_by_content(
    root: Path,
    query: str,
    *,
    regex: bool,
    case_sensitive: bool,
    glob: str | None,
    include_hidden: bool,
    timeout_sec: float,
    exclude_common: bool,
) -> tuple[list[Path], bool]:
    executable = shutil.which("rg")
    if executable:
        command = [executable, "--files-with-matches", "--no-messages", "--color", "never"]
        if not regex:
            command.append("--fixed-strings")
        if not case_sensitive:
            command.append("--ignore-case")
        if include_hidden:
            command.append("--hidden")
        if not exclude_common:
            command.append("--no-ignore")
        if exclude_common:
            for excluded in COMMON_EXCLUDES:
                command.extend(["--glob", f"!{excluded}/**"])
        if glob:
            command.extend(["--glob", glob])
        command.extend(["--", query, str(root)])
        result = run_bounded(
            command,
            cwd=root,
            timeout_sec=timeout_sec,
            stdout_limit=None,
            stderr_limit=None,
            output_mode="head",
            encoding="utf-8",
        )
        if result["exit_code"] not in (0, 1):
            raise ToolError("search_failed", result["stderr"]["text"] or "ripgrep failed.")
        rg_matches = [Path(line) for line in result["stdout"]["text"].splitlines() if line]
        return rg_matches, bool(result["stdout"]["truncated"] or result["timed_out"])
    files, scan_truncated = _walk_files(root, include_hidden, 50_000, exclude_common)
    flags = 0 if case_sensitive else re.IGNORECASE
    compiled = re.compile(query, flags) if regex else None
    needle = query if case_sensitive else query.casefold()
    fallback_matches: list[Path] = []
    for path in files:
        relative = str(path.relative_to(root))
        if glob and not fnmatch.fnmatch(relative, glob):
            continue
        try:
            if path.stat().st_size > 5_000_000:
                continue
            text = path.read_text(encoding=detect_encoding(path), errors="replace")
        except (OSError, ToolError):
            continue
        candidate = text if case_sensitive else text.casefold()
        if compiled.search(text) if compiled else needle in candidate:
            fallback_matches.append(path)
    return fallback_matches, scan_truncated
