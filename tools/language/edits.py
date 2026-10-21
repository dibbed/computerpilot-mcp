"""Validated transactional application of LSP workspace edits."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from core.backups import BACKUP_RETENTION
from core.config import SETTINGS
from core.errors import ToolError
from core.recovery import (
    OPERATION_RECOVERY,
    WorkspaceEditEvidence,
    WorkspaceEditFile,
    mark_workspace_edit_rolled_back,
    validate_workspace_edit_evidence,
    workspace_edit_mutation,
)
from core.resource_claims import managed_path_claims
from core.resource_locks import RESOURCE_LOCKS
from tools.filesystem.patches import _write_bytes
from tools.language.lsp import position_to_offset, uri_to_path
from tools.project.context import CONTEXT_CACHE
from tools.project.index import PYTHON_METADATA_CACHE

_MAX_EDIT_FILES = 100
_MAX_EDITS = 1_000
_MAX_REPLACEMENT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class PlannedFile:
    path: Path
    before: bytes
    after: bytes


def _unsupported(message: str) -> ToolError:
    return ToolError("lsp_edit_unsupported", message)


def _budget(message: str) -> ToolError:
    return ToolError("lsp_edit_budget", message)


def _version_path(key: str, root: Path) -> Path:
    candidate = Path(key)
    try:
        path = candidate.resolve(strict=False) if candidate.is_absolute() else None
        scheme = urlsplit(key).scheme if path is None else ""
    except ValueError as exc:
        raise _unsupported("Workspace edit contains an invalid version URI.") from exc
    if path is None:
        path = _uri_path(key, root) if scheme else (root / key).resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ToolError("lsp_path_outside_workspace", "Workspace edit path is outside the workspace.") from exc
    return path


def _uri_path(uri: str, root: Path) -> Path:
    try:
        return uri_to_path(uri, root)
    except (UnicodeError, ValueError) as exc:
        raise _unsupported("Workspace edit contains an invalid URI.") from exc


def _document_versions(root: Path, versions: Mapping[str, int] | None) -> dict[Path, int]:
    if versions is None:
        return {}
    if not isinstance(versions, Mapping):
        raise _unsupported("document_versions must map document URIs to versions.")
    normalized: dict[Path, int] = {}
    for key, value in versions.items():
        if not isinstance(key, str) or type(value) is not int or value < 0:
            raise _unsupported("document_versions must map document URIs to non-negative integers.")
        path = _version_path(key, root)
        previous = normalized.get(path)
        if previous is not None and previous != value:
            raise ToolError("lsp_document_version_conflict", "document_versions contains conflicting aliases.")
        normalized[path] = value
    return normalized


def _validate_edit(edit: object) -> dict[str, Any]:
    if not isinstance(edit, dict):
        raise _unsupported("Workspace edit requires a range and string newText.")
    target_range = edit.get("range")
    if (
        not isinstance(target_range, dict)
        or not isinstance(target_range.get("start"), dict)
        or not isinstance(target_range.get("end"), dict)
        or not isinstance(edit.get("newText"), str)
    ):
        raise _unsupported("Workspace edit requires a range and string newText.")
    return edit


def prepare_workspace_edit(
    root: Path,
    workspace_edit: dict[str, Any],
    *,
    expected_sha256: dict[str, str] | None = None,
    require_expected_sha256: bool = False,
    document_versions: Mapping[str, int] | None = None,
) -> tuple[PlannedFile, ...]:
    if not isinstance(workspace_edit, dict):
        raise _unsupported("Workspace edit must be an object.")
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, Mapping)
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in expected_sha256.items())
    ):
        raise _unsupported("expected_sha256 must map relative paths to SHA-256 strings.")
    root = root.resolve(strict=False)
    changes: dict[str, list[dict[str, Any]]] = {}
    versions: dict[str, int] = {}
    edit_count = 0
    replacement_bytes = 0

    def append_edits(uri: str, raw_edits: object) -> None:
        nonlocal edit_count, replacement_bytes
        if not isinstance(uri, str) or not isinstance(raw_edits, list):
            raise _unsupported("Workspace text edits must be lists keyed by URI.")
        if edit_count + len(raw_edits) > _MAX_EDITS:
            raise _budget(f"Workspace edit exceeds the {_MAX_EDITS} edit limit.")
        parsed = []
        for edit in raw_edits:
            candidate = _validate_edit(edit)
            replacement = candidate["newText"]
            if len(replacement) > _MAX_REPLACEMENT_BYTES:
                raise _budget("Workspace edit replacement text exceeds the 2 MiB limit.")
            try:
                encoded_replacement = replacement.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise _unsupported("Workspace edit newText must be valid UTF-8.") from exc
            replacement_bytes += len(encoded_replacement)
            if replacement_bytes > _MAX_REPLACEMENT_BYTES:
                raise _budget("Workspace edit replacement text exceeds the 2 MiB limit.")
            parsed.append(candidate)
        edit_count += len(parsed)
        changes.setdefault(uri, []).extend(parsed)

    raw_changes = workspace_edit.get("changes")
    if raw_changes is not None:
        if not isinstance(raw_changes, dict):
            raise _unsupported("Workspace edit changes must be an object.")
        if len(raw_changes) > _MAX_EDITS:
            raise _budget("Workspace edit exceeds the URI alias limit.")
        for uri, raw_edits in raw_changes.items():
            append_edits(uri, raw_edits)

    document_changes = workspace_edit.get("documentChanges")
    if document_changes is not None:
        if not isinstance(document_changes, list):
            raise _unsupported("Workspace edit documentChanges must be a list.")
        if len(document_changes) > _MAX_EDITS:
            raise _budget("Workspace edit exceeds the operation limit.")
        for operation in document_changes:
            document = operation.get("textDocument") if isinstance(operation, dict) else None
            raw_document_edits = operation.get("edits") if isinstance(operation, dict) else None
            if (
                not isinstance(document, dict)
                or not isinstance(document.get("uri"), str)
                or not isinstance(raw_document_edits, list)
            ):
                raise _unsupported("Workspace resource operations are not supported.")
            version = document.get("version")
            if version is not None:
                if type(version) is not int or version < 0:
                    raise _unsupported("Workspace document versions must be non-negative integers.")
                previous = versions.get(document["uri"])
                if previous is not None and previous != version:
                    raise ToolError("lsp_document_version_conflict", "Workspace document versions conflict.")
                versions[document["uri"]] = version
            append_edits(document["uri"], raw_document_edits)

    if not changes:
        raise _unsupported("Workspace edit has no supported text edits.")

    files: dict[Path, list[dict[str, Any]]] = {}
    for uri, raw_edits in changes.items():
        path = _uri_path(uri, root)
        files.setdefault(path, []).extend(raw_edits)
    if len(files) > _MAX_EDIT_FILES:
        raise _budget(f"Workspace edit exceeds the {_MAX_EDIT_FILES} file limit.")

    normalized_versions = _document_versions(root, document_versions)
    versioned_paths: dict[Path, int] = {}
    for uri, version in versions.items():
        path = _uri_path(uri, root)
        previous = versioned_paths.get(path)
        if previous is not None and previous != version:
            raise ToolError("lsp_document_version_conflict", "Workspace document versions conflict.")
        versioned_paths[path] = version
    if versioned_paths:
        if document_versions is None:
            raise ToolError("lsp_document_version_conflict", "Versioned workspace edits require document_versions.")
        for path, version in versioned_paths.items():
            if normalized_versions.get(path) != version:
                raise ToolError("lsp_document_version_conflict", "Workspace document version does not match the snapshot.")

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

        seen: set[tuple[int, int, str]] = set()
        parsed_edits: list[tuple[int, int, str]] = []
        for edit in raw_edits:
            start = position_to_offset(text, edit["range"]["start"])
            end = position_to_offset(text, edit["range"]["end"])
            if end < start:
                raise ToolError("lsp_position_invalid", "Workspace edit range ends before it starts.")
            key = (start, end, edit["newText"])
            if key in seen:
                raise ToolError("lsp_edit_duplicate", f"Workspace edit contains a duplicate in {relative}.")
            seen.add(key)
            parsed_edits.append(key)
        # Reverse equal-position inserts too, so the final text preserves input order.
        parsed_edits = list(reversed(sorted(parsed_edits, key=lambda item: (item[0], item[1]))))
        for index in range(len(parsed_edits) - 1):
            if parsed_edits[index + 1][1] > parsed_edits[index][0]:
                raise ToolError("lsp_edit_overlap", f"Workspace edits overlap in {relative}.")
        after = text
        for start, end, replacement in parsed_edits:
            after = after[:start] + replacement + after[end:]
        planned.append(PlannedFile(path, before, after.encode("utf-8")))
    return tuple(planned)


def _invalidate_paths(paths: tuple[Path, ...]) -> None:
    for path in paths:
        PYTHON_METADATA_CACHE.invalidate(path)
        CONTEXT_CACHE.invalidate(path)


def apply_workspace_edit(plan: tuple[PlannedFile, ...]) -> dict[str, Any]:
    paths = tuple(item.path for item in plan)
    if not paths:
        return {"ok": True, "file_count": 0, "files": []}
    with managed_path_claims(*paths, category="native.workspace_edit"), RESOURCE_LOCKS.sync(*paths):
        for item in plan:
            if item.path.read_bytes() != item.before:
                raise ToolError("hash_conflict", f"Workspace edit precondition failed for {item.path}.")
        with workspace_edit_mutation(paths) as handle:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            reserved = tuple(
                SETTINGS.backup_dir / f"{stamp}_{uuid.uuid4().hex}.bak"
                for _ in plan
            )
            evidence = WorkspaceEditEvidence(files=tuple(
                WorkspaceEditFile(
                    path=str(item.path.resolve(strict=False)),
                    before_sha256=hashlib.sha256(item.before).hexdigest(),
                    after_sha256=hashlib.sha256(item.after).hexdigest(),
                    backup_path=str(destination.resolve(strict=False)),
                )
                for item, destination in zip(plan, reserved, strict=True)
            ))
            validate_workspace_edit_evidence(evidence)
            with BACKUP_RETENTION.pin_paths(reserved):
                from tools.filesystem.service import _backup_file

                for item, destination, item_evidence in zip(plan, reserved, evidence.files, strict=True):
                    backup = Path(_backup_file(
                        item.path,
                        item_evidence.before_sha256,
                        destination=destination,
                    ))
                    try:
                        backup_digest = hashlib.sha256(backup.read_bytes()).hexdigest()
                    except OSError as exc:
                        raise ToolError(
                            "backup_verification_failed",
                            f"Workspace edit backup could not be verified for {item.path}.",
                        ) from exc
                    if backup_digest != item_evidence.before_sha256:
                        raise ToolError(
                            "backup_verification_failed",
                            f"Workspace edit backup changed while copying {item.path}.",
                        )
                OPERATION_RECOVERY.checkpoint_workspace_edit(handle, evidence, stage="prepared")
                OPERATION_RECOVERY.checkpoint_workspace_edit(handle, evidence, stage="may_dispatch")
                try:
                    for item in plan:
                        _write_bytes(item.path, item.after)
                    for item, item_evidence in zip(plan, evidence.files, strict=True):
                        try:
                            actual = hashlib.sha256(item.path.read_bytes()).hexdigest()
                        except OSError as exc:
                            raise ToolError(
                                "workspace_edit_verification_failed",
                                f"Workspace edit result could not be verified for {item.path}.",
                            ) from exc
                        if actual != item_evidence.after_sha256:
                            raise ToolError(
                                "workspace_edit_verification_failed",
                                f"Workspace edit result changed during verification for {item.path}.",
                            )
                except BaseException as error:
                    changed: list[PlannedFile] = []
                    restored = True
                    for item in plan:
                        try:
                            current = item.path.read_bytes()
                        except OSError:
                            restored = False
                            break
                        if current == item.after and current != item.before:
                            changed.append(item)
                        elif current != item.before:
                            restored = False
                            break
                    if restored:
                        try:
                            for item in reversed(changed):
                                _write_bytes(item.path, item.before)
                            for item in plan:
                                if item.path.read_bytes() != item.before:
                                    restored = False
                                    break
                        except Exception:
                            restored = False
                    _invalidate_paths(paths)
                    if restored:
                        mark_workspace_edit_rolled_back()
                        try:
                            OPERATION_RECOVERY.finish(handle, known_result="returned_error")
                        except Exception:
                            pass  # A prepared checkpoint still recovers as failed without replay.
                        raise
                    try:
                        OPERATION_RECOVERY.mark_uncertain(handle, "workspace_edit_outcome_uncertain")
                    except Exception:
                        pass  # The durable may_dispatch checkpoint remains for reconciliation.
                    raise ToolError(
                        "workspace_edit_outcome_uncertain",
                        "The workspace edit may have partially applied; do not replay it.",
                        hint=f"Inspect recovery operation {handle.operation_id} and restore from its backups.",
                    ) from error
        _invalidate_paths(paths)
    return {"ok": True, "file_count": len(plan), "files": [str(item.path) for item in plan]}
