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
    root: Path,
    workspace_edit: dict[str, Any],
    *,
    expected_sha256: dict[str, str] | None = None,
    require_expected_sha256: bool = False,
) -> tuple[PlannedFile, ...]:
    root = root.resolve(strict=False)
    changes: dict[str, list[dict[str, Any]]] = {}
    raw_changes = workspace_edit.get("changes")
    if raw_changes is not None:
        if not isinstance(raw_changes, dict):
            raise ToolError("lsp_edit_unsupported", "Workspace edit changes must be an object.")
        for uri, raw_edits in raw_changes.items():
            if not isinstance(uri, str) or not isinstance(raw_edits, list):
                raise ToolError("lsp_edit_unsupported", "Workspace text edits must be lists keyed by URI.")
            changes[uri] = list(raw_edits)
    document_changes = workspace_edit.get("documentChanges")
    if document_changes is not None:
        if not isinstance(document_changes, list):
            raise ToolError("lsp_edit_unsupported", "Workspace edit documentChanges must be a list.")
        for operation in document_changes:
            document = operation.get("textDocument") if isinstance(operation, dict) else None
            edits = operation.get("edits") if isinstance(operation, dict) else None
            if not isinstance(document, dict) or not isinstance(document.get("uri"), str) or not isinstance(edits, list):
                raise ToolError("lsp_edit_unsupported", "Workspace resource operations are not supported.")
            changes.setdefault(document["uri"], []).extend(edits)
    if not changes:
        raise ToolError("lsp_edit_unsupported", "Workspace edit has no supported text edits.")
    files: dict[Path, list[dict[str, Any]]] = {}
    for uri, raw_edits in changes.items():
        path = uri_to_path(uri, root)
        files.setdefault(path, []).extend(raw_edits)
    planned = []
    for path, raw_edits in files.items():
        before = path.read_bytes()
        text = before.decode("utf-8")
        relative = path.relative_to(root).as_posix()
        wanted = (expected_sha256 or {}).get(relative)
        if require_expected_sha256 and not wanted:
            raise ToolError(
                "lsp_edit_precondition_required",
                f"Workspace edit requires expected_sha256 for {relative}.",
            )
        if wanted and hashlib.sha256(before).hexdigest().casefold() != wanted.casefold():
            raise ToolError("hash_conflict", f"Workspace edit precondition failed for {relative}.")
        edits = []
        for edit in raw_edits:
            if (
                not isinstance(edit, dict)
                or not isinstance(edit.get("range"), dict)
                or not isinstance(edit.get("newText"), str)
            ):
                raise ToolError("lsp_edit_unsupported", "Workspace edit requires a range and string newText.")
            start = position_to_offset(text, edit["range"].get("start"))
            end = position_to_offset(text, edit["range"].get("end"))
            if end < start:
                raise ToolError("lsp_position_invalid", "Workspace edit range ends before it starts.")
            edits.append((start, end, edit["newText"]))
        # Reverse equal-position inserts too, so the final text preserves input order.
        edits = list(reversed(sorted(edits, key=lambda item: (item[0], item[1]))))
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
