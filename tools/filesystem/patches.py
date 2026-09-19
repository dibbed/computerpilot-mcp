"""Strict parsing and transactional application of text unified diffs."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from core.backups import BACKUP_RETENTION
from core.errors import ToolError
from core.executor import run_bounded
from core.resource_locks import RESOURCE_LOCKS
from tools.filesystem import service
from tools.project.context import CONTEXT_CACHE
from tools.project.index import PYTHON_METADATA_CACHE

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$")
_UNSUPPORTED_PREFIXES = (
    "diff --cc ",
    "diff --combined ",
    "GIT binary patch",
    "Binary files ",
    "old mode ",
    "new mode ",
    "new file mode 120000",
    "deleted file mode 120000",
)


@dataclass(frozen=True, slots=True)
class PatchLimits:
    max_bytes: int = 2_000_000
    max_files: int = 100
    max_hunks: int = 1_000


@dataclass(frozen=True, slots=True)
class PatchHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[str, ...]
    old_no_newline: bool = False
    new_no_newline: bool = False


@dataclass(frozen=True, slots=True)
class FilePatch:
    kind: Literal["create", "modify", "delete", "rename"]
    old_path: str | None
    new_path: str | None
    hunks: tuple[PatchHunk, ...]


@dataclass(frozen=True, slots=True)
class PatchSet:
    files: tuple[FilePatch, ...]
    bytes: int
    hunks: int


@dataclass(frozen=True, slots=True)
class _PreparedFile:
    patch: FilePatch
    source: Path | None
    target: Path | None
    before: bytes | None
    after: bytes | None
    encoding: str
    backup: str | None


def _patch_path(value: str, *, allow_null: bool = False) -> str | None:
    value = value.strip().split("\t", 1)[0]
    if allow_null and value == "/dev/null":
        return None
    if value.startswith(("a/", "b/")):
        value = value[2:]
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    if (
        not value
        or "\x00" in value
        or windows.is_absolute()
        or windows.drive
        or posix.is_absolute()
        or value.startswith(("//", "\\\\"))
        or any(part in {"", ".", ".."} for part in posix.parts)
        or "\\" in value
    ):
        raise ToolError("invalid_patch_path", f"Unsafe patch path: {value!r}")
    return posix.as_posix()


def _parse_hunk(lines: list[str], index: int) -> tuple[PatchHunk, int]:
    match = _HUNK_HEADER.match(lines[index])
    if match is None:
        raise ToolError("invalid_patch", f"Malformed hunk header at patch line {index + 1}.")
    old_start, old_count, new_start, new_count = (
        int(match.group(1)),
        int(match.group(2) or "1"),
        int(match.group(3)),
        int(match.group(4) or "1"),
    )
    index += 1
    body: list[str] = []
    old_seen = new_seen = 0
    old_no_newline = new_no_newline = False
    while index < len(lines) and not lines[index].startswith(("@@ ", "diff --git ")):
        line = lines[index]
        if line == "\\ No newline at end of file":
            if not body:
                raise ToolError("invalid_patch", f"No-newline marker has no preceding hunk line at patch line {index + 1}.")
            old_no_newline = old_no_newline or body[-1][0] in {" ", "-"}
            new_no_newline = new_no_newline or body[-1][0] in {" ", "+"}
            index += 1
            continue
        if not line or line[0] not in {" ", "+", "-"}:
            raise ToolError("invalid_patch", f"Invalid hunk line at patch line {index + 1}.")
        if line[0] in {" ", "-"}:
            old_seen += 1
        if line[0] in {" ", "+"}:
            new_seen += 1
        body.append(line)
        index += 1
    if old_seen != old_count or new_seen != new_count:
        raise ToolError(
            "invalid_patch",
            f"Hunk line counts do not match header: expected -{old_count}/+{new_count}, got -{old_seen}/+{new_seen}.",
        )
    return PatchHunk(old_start, old_count, new_start, new_count, tuple(body), old_no_newline, new_no_newline), index


def parse_unified_patch(text: str, limits: PatchLimits | None = None) -> PatchSet:
    limits = limits or PatchLimits()
    encoded_size = len(text.encode("utf-8"))
    if encoded_size > limits.max_bytes:
        raise ToolError("patch_too_large", f"Patch is {encoded_size} bytes; limit is {limits.max_bytes}.")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines()
    if not lines:
        raise ToolError("invalid_patch", "Patch is empty.")
    if any(line.startswith(_UNSUPPORTED_PREFIXES) for line in lines):
        raise ToolError("unsupported_patch", "Patch contains an unsupported binary, combined, mode-only, or symlink change.")

    files: list[FilePatch] = []
    total_hunks = 0
    targets: set[str] = set()
    index = 0
    while index < len(lines):
        if not lines[index].startswith("diff --git "):
            raise ToolError("invalid_patch", f"Expected a Git diff header at patch line {index + 1}.")
        parts = lines[index].split()
        if len(parts) != 4:
            raise ToolError("invalid_patch", f"Malformed Git diff header at patch line {index + 1}.")
        header_old = _patch_path(parts[2])
        header_new = _patch_path(parts[3])
        index += 1
        rename_old: str | None = None
        rename_new: str | None = None
        while index < len(lines) and not lines[index].startswith(("--- ", "diff --git ")):
            line = lines[index]
            if line.startswith("rename from "):
                rename_old = _patch_path(line.removeprefix("rename from "))
            elif line.startswith("rename to "):
                rename_new = _patch_path(line.removeprefix("rename to "))
            elif line.startswith(("index ", "new file mode 100", "deleted file mode 100", "similarity index ")):
                pass
            elif line.startswith(_UNSUPPORTED_PREFIXES):
                raise ToolError("unsupported_patch", f"Unsupported patch metadata: {line.split()[0]}.")
            else:
                raise ToolError("invalid_patch", f"Unexpected patch metadata at line {index + 1}.")
            index += 1

        old_path = header_old
        new_path = header_new
        hunks: list[PatchHunk] = []
        if index < len(lines) and lines[index].startswith("--- "):
            old_path = _patch_path(lines[index][4:], allow_null=True)
            index += 1
            if index >= len(lines) or not lines[index].startswith("+++ "):
                raise ToolError("invalid_patch", "Missing new-file header after old-file header.")
            new_path = _patch_path(lines[index][4:], allow_null=True)
            index += 1
            while index < len(lines) and lines[index].startswith("@@ "):
                hunk, index = _parse_hunk(lines, index)
                hunks.append(hunk)
                total_hunks += 1
                if total_hunks > limits.max_hunks:
                    raise ToolError("patch_too_large", f"Patch exceeds the {limits.max_hunks}-hunk limit.")

        kind: Literal["create", "modify", "delete", "rename"]
        if rename_old is not None or rename_new is not None:
            if rename_old is None or rename_new is None:
                raise ToolError("invalid_patch", "Rename patches require both rename-from and rename-to paths.")
            old_path, new_path, kind = rename_old, rename_new, "rename"
        elif old_path is None:
            kind = "create"
        elif new_path is None:
            kind = "delete"
        else:
            kind = "modify"
        if kind != "rename" and not hunks:
            raise ToolError("invalid_patch", "Text file changes require at least one hunk.")
        operation_target = new_path or old_path
        if operation_target is None or operation_target in targets:
            raise ToolError("invalid_patch", "Patch contains a missing or duplicate target path.")
        targets.add(operation_target)
        files.append(FilePatch(kind=kind, old_path=old_path, new_path=new_path, hunks=tuple(hunks)))
        if len(files) > limits.max_files:
            raise ToolError("patch_too_large", f"Patch exceeds the {limits.max_files}-file limit.")

    return PatchSet(files=tuple(files), bytes=encoded_size, hunks=total_hunks)


def _newline(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def _apply_hunks(path: str, before: str, hunks: tuple[PatchHunk, ...]) -> str:
    newline = _newline(before)
    had_final_newline = before.endswith(("\n", "\r"))
    source = before.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    output: list[str] = []
    cursor = 0
    previous_end = 0
    for number, hunk in enumerate(hunks, start=1):
        start = hunk.old_start - 1 if hunk.old_start else 0
        if start < previous_end or start < cursor or start > len(source):
            raise ToolError("patch_context_mismatch", f"Patch hunk {number} has an invalid or overlapping position in {path}.")
        output.extend(source[cursor:start])
        cursor = start
        for line in hunk.lines:
            prefix, content = line[0], line[1:]
            if prefix in {" ", "-"}:
                if cursor >= len(source) or source[cursor] != content:
                    raise ToolError("patch_context_mismatch", f"Patch context mismatch in {path}, hunk {number}.")
                if prefix == " ":
                    output.append(source[cursor])
                cursor += 1
            else:
                output.append(content)
        previous_end = cursor
    output.extend(source[cursor:])
    result = newline.join(output)
    new_no_newline = bool(hunks and hunks[-1].new_no_newline)
    if (had_final_newline or (not before and hunks)) and not new_no_newline:
        result += newline
    return result


def _resolve_under(root: Path, relative: str | None) -> Path | None:
    if relative is None:
        return None
    target = (root / Path(*PurePosixPath(relative).parts)).resolve(strict=False)
    if not target.is_relative_to(root):
        raise ToolError("invalid_patch_path", f"Patch path escapes repository root: {relative}")
    return target


def _hash_bytes(value: bytes | None) -> str | None:
    return None if value is None else hashlib.sha256(value).hexdigest()


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".patch", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        service._replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _prepare(root: Path, patch: FilePatch, *, encoding: str) -> _PreparedFile:
    source = _resolve_under(root, patch.old_path)
    target = _resolve_under(root, patch.new_path)
    read_path = source
    if patch.kind == "create":
        if target is None or target.exists() or target.is_symlink():
            raise FileExistsError(f"Patch create target already exists: {target}")
        before = None
        used_encoding = "utf-8" if encoding == "auto" else encoding
        before_text = ""
    else:
        if read_path is None or not read_path.is_file() or read_path.is_symlink():
            raise FileNotFoundError(f"Patch source file not found: {read_path}")
        before = read_path.read_bytes()
        used_encoding = service.chosen_encoding(read_path, encoding)
        try:
            before_text = before.decode(used_encoding)
        except UnicodeDecodeError as exc:
            raise ToolError("encoding_error", f"Cannot decode patch target {read_path} as {used_encoding}.") from exc
    if patch.kind == "rename" and target is not None and target.exists() and target != source:
        raise FileExistsError(f"Patch rename target already exists: {target}")
    after_text = _apply_hunks(patch.new_path or patch.old_path or "unknown", before_text, patch.hunks)
    if patch.kind == "delete":
        after = None
    elif patch.kind == "rename" and not patch.hunks:
        after = before
    else:
        try:
            after = after_text.encode(used_encoding)
        except (LookupError, UnicodeEncodeError) as exc:
            raise ToolError("encoding_error", f"Cannot encode patched file as {used_encoding}.") from exc
        if target is not None:
            service._validate_python(target, after_text)
    return _PreparedFile(patch, source, target, before, after, used_encoding, None)


def _restore(prepared: tuple[_PreparedFile, ...]) -> None:
    for item in reversed(prepared):
        touched = {path for path in (item.source, item.target) if path is not None}
        for path in touched:
            path.unlink(missing_ok=True)
        if item.source is not None and item.before is not None:
            _write_bytes(item.source, item.before)
        PYTHON_METADATA_CACHE.invalidate(item.source or item.target or "")
    CONTEXT_CACHE.clear()


def apply_patch_transaction(
    root: Path,
    patch_text: str,
    *,
    dry_run: bool = False,
    expected_sha256: dict[str, str] | None = None,
    backup: bool = True,
    validation_command: list[str] | None = None,
    validation_cwd: Path | None = None,
    timeout_sec: float = 60,
    encoding: str = "auto",
    limits: PatchLimits | None = None,
) -> dict[str, Any]:
    resolved_root = root.resolve(strict=False)
    if not resolved_root.is_dir():
        raise NotADirectoryError(f"Patch root not found: {resolved_root}")
    patch_set = parse_unified_patch(patch_text, limits)
    all_paths = [
        path
        for file_patch in patch_set.files
        for path in (_resolve_under(resolved_root, file_patch.old_path), _resolve_under(resolved_root, file_patch.new_path))
        if path is not None
    ]
    with RESOURCE_LOCKS.sync(*all_paths):
        prepared = tuple(_prepare(resolved_root, item, encoding=encoding) for item in patch_set.files)
        expected = expected_sha256 or {}
        for item in prepared:
            relative = item.patch.old_path or item.patch.new_path
            wanted = expected.get(relative or "")
            if wanted and wanted.casefold() != (_hash_bytes(item.before) or "").casefold():
                raise ToolError("hash_conflict", f"Patch precondition failed for {relative}.")
        if backup and not dry_run:
            prepared = tuple(
                replace(item, backup=service._backup_file(item.source, _hash_bytes(item.before) or "unknown"))
                if item.source is not None and item.before is not None
                else item
                for item in prepared
            )
            for item in prepared:
                if item.backup:
                    BACKUP_RETENTION.schedule(Path(item.backup))

        rows = [
            {
                "operation": item.patch.kind,
                "old_path": item.patch.old_path,
                "new_path": item.patch.new_path,
                "hunks": len(item.patch.hunks),
                "sha256_before": _hash_bytes(item.before),
                "sha256_after": _hash_bytes(item.after),
                "backup": item.backup,
            }
            for item in prepared
        ]
        if dry_run:
            return {
                "ok": True,
                "root": str(resolved_root),
                "dry_run": True,
                "files": rows,
                "file_count": len(rows),
                "hunk_count": patch_set.hunks,
                "rolled_back": False,
            }

        try:
            for item in prepared:
                if item.patch.kind == "delete":
                    assert item.source is not None
                    item.source.unlink()
                elif item.patch.kind == "rename":
                    assert item.source is not None and item.target is not None
                    if item.after == item.before:
                        item.target.parent.mkdir(parents=True, exist_ok=True)
                        service._replace_with_retry(item.source, item.target)
                    else:
                        assert item.after is not None
                        _write_bytes(item.target, item.after)
                        item.source.unlink()
                else:
                    assert item.target is not None and item.after is not None
                    _write_bytes(item.target, item.after)
            if validation_command:
                result = run_bounded(
                    validation_command,
                    cwd=validation_cwd or resolved_root,
                    timeout_sec=timeout_sec,
                    stdout_limit=20_000,
                    stderr_limit=20_000,
                    output_mode="tail",
                    encoding="utf-8",
                )
                if not result["ok"]:
                    raise ToolError(
                        "patch_validation_failed",
                        f"Patch validation failed with exit code {result['exit_code']}.",
                        hint="Inspect the validation diagnostics and adjust the patch.",
                    )
        except BaseException:
            _restore(prepared)
            raise

        for item in prepared:
            if item.source is not None:
                PYTHON_METADATA_CACHE.invalidate(item.source)
            if item.target is not None:
                PYTHON_METADATA_CACHE.invalidate(item.target)
        CONTEXT_CACHE.clear()
        return {
            "ok": True,
            "root": str(resolved_root),
            "dry_run": False,
            "files": rows,
            "file_count": len(rows),
            "hunk_count": patch_set.hunks,
            "rolled_back": False,
        }
