"""Validated transactional application of LSP workspace edits."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.errors import ToolError
from core.resource_locks import RESOURCE_LOCKS
from tools.filesystem.patches import _write_bytes
from tools.language.lsp import position_to_offset, uri_to_path


@dataclass(frozen=True)
class PlannedFile:
    path: Path
    before: bytes
    after: bytes


def prepare_workspace_edit(
    root: Path, workspace_edit: dict[str, Any], *, expected_sha256: dict[str, str] | None = None
) -> tuple[PlannedFile, ...]:
    root = root.resolve(strict=False)
    changes = workspace_edit.get("changes")
    if not isinstance(changes, dict):
        raise ToolError("lsp_edit_unsupported", "Workspace edit has no supported changes map.")
    planned = []
    for uri, raw_edits in changes.items():
        path = uri_to_path(uri, root)
        before = path.read_bytes()
        text = before.decode("utf-8")
        relative = path.relative_to(root).as_posix()
        wanted = (expected_sha256 or {}).get(relative)
        if wanted and hashlib.sha256(before).hexdigest().casefold() != wanted.casefold():
            raise ToolError("hash_conflict", f"Workspace edit precondition failed for {relative}.")
        edits = []
        for edit in raw_edits:
            start = position_to_offset(text, edit["range"]["start"])
            end = position_to_offset(text, edit["range"]["end"])
            edits.append((start, end, str(edit["newText"])))
        edits.sort(reverse=True)
        for index in range(len(edits) - 1):
            if edits[index + 1][1] > edits[index][0]:
                raise ToolError("lsp_edit_overlap", f"Workspace edits overlap in {relative}.")
        after = text
        for start, end, replacement in edits:
            after = after[:start] + replacement + after[end:]
        planned.append(PlannedFile(path, before, after.encode("utf-8")))
    return tuple(planned)


def apply_workspace_edit(plan: tuple[PlannedFile, ...]) -> dict[str, Any]:
    with RESOURCE_LOCKS.sync(*(item.path for item in plan)):
        written = []
        try:
            for item in plan:
                if item.path.read_bytes() != item.before:
                    raise ToolError("hash_conflict", f"Workspace edit precondition failed for {item.path}.")
            for item in plan:
                _write_bytes(item.path, item.after)
                written.append(item)
        except BaseException:
            for item in reversed(written):
                _write_bytes(item.path, item.before)
            raise
    return {"ok": True, "file_count": len(plan), "files": [str(item.path) for item in plan]}
