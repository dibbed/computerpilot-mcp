"""MCP registration for filesystem and surgical editing tools."""

from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import BaseModel, ConfigDict, Field

from core.artifacts import deliver_text
from core.audit import audit_action
from core.config import resolve_path
from core.errors import ToolError
from core.media import image_tool_result, inspect_image
from core.resource_locks import RESOURCE_LOCKS
from core.response import ok, page
from core.tooling import DESTRUCTIVE, MUTATING, READ_ONLY, PathArg, compact_errors
from tools.filesystem import service
from tools.filesystem.search_snapshots import SEARCH_SNAPSHOTS, search_fingerprint
from tools.project.index import PYTHON_METADATA_CACHE

EncodingArg = Annotated[str, Field(min_length=1, max_length=40)]
MaxCharsArg = Annotated[int | None, Field(ge=1)]


class RefactorEdit(BaseModel):
    """One surgical edit used by safe_refactor."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    mode: Literal["exact", "anchors", "function", "class"]
    old: str | None = Field(default=None, max_length=100_000)
    new: str | None = Field(default=None, max_length=200_000)
    expected_count: int = Field(default=1, ge=1, le=100)
    start_marker: str | None = Field(default=None, max_length=10_000)
    end_marker: str | None = Field(default=None, max_length=10_000)
    include_markers: bool = False
    symbol: str | None = Field(default=None, max_length=500)
    new_body: str | None = Field(default=None, max_length=200_000)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _edit_result(
    path: Path,
    before: str,
    after: str,
    encoding: str,
    *,
    backup: bool,
    validate_python: bool,
) -> dict[str, Any]:
    summary = service.diff_summary(before, after, path)
    result = service.atomic_write(
        path,
        after,
        encoding=encoding,
        create_parents=False,
        backup=backup,
        validate_python=validate_python,
    )
    result["diff"] = summary
    return result


def _invalidate_if_changed(path: Path, result: dict[str, Any]) -> None:
    if bool(result.get("changed")):
        PYTHON_METADATA_CACHE.invalidate(path)


_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


def _model_image_result(target: Path) -> dict[str, Any]:
    info = inspect_image(target)
    metadata = {
        "ok": True,
        "path": str(target),
        "bytes": info["bytes"],
        "width": info["width"],
        "height": info["height"],
        "mime_type": info["mime_type"],
    }
    return image_tool_result(metadata, target, delivery="auto")


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("view_image")
    def view_image(path: PathArg) -> dict[str, Any]:
        """Validate and expose a local PNG, JPEG, or WebP as model-visible MCP image content."""

        return _model_image_result(resolve_path(path))

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("read_file")
    def read_file(
        path: PathArg,
        start_line: Annotated[int, Field(ge=1, le=10_000_000)] = 1,
        end_line: Annotated[int | None, Field(ge=1, le=10_000_000)] = None,
        offset: Annotated[int, Field(ge=0, le=100_000_000)] = 0,
        max_chars: MaxCharsArg = None,
        encoding: EncodingArg = "auto",
        delivery: Literal["inline", "file", "auto"] = "inline",
    ) -> dict[str, Any]:
        """Read text, or with delivery=auto expose a PNG/JPEG/WebP as model-visible image content."""

        target = resolve_path(path)
        if delivery == "auto" and target.suffix.casefold() in _IMAGE_SUFFIXES:
            return _model_image_result(target)

        result = service.read_window(
            target,
            start_line=start_line,
            end_line=end_line,
            offset=offset,
            max_chars=max_chars,
            encoding=encoding,
        )
        if delivery != "inline":
            output = deliver_text(result["content"], delivery)
            if output["delivery"] == "file":
                result.pop("content")
                result["artifact"] = output
        return result

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("write_file")
    def write_file(
        path: PathArg,
        content: Annotated[str, Field(max_length=2_000_000)],
        encoding: EncodingArg = "auto",
        create_if_missing: bool = False,
        create_parents: bool = False,
        backup: bool = True,
        validate_python: bool = True,
        expected_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-fA-F]{64}$")] = None,
    ) -> dict[str, Any]:
        """Atomically replace an entire file; prefer surgical edit tools for small changes."""
        with RESOURCE_LOCKS.sync(path):
            target = resolve_path(path)
            if not target.exists() and not create_if_missing:
                raise FileNotFoundError(f"File not found: {target}")
            if target.exists() and expected_sha256 and _hash_file(target).lower() != expected_sha256.lower():
                raise ToolError("stale_file", "File hash changed; refusing to overwrite stale content.")
            audit_action("write_file", target=target, details={"content_chars": len(content)})
            result = service.atomic_write(
                target,
                content,
                encoding=encoding,
                create_parents=create_parents,
                backup=backup,
                validate_python=validate_python,
            )
            _invalidate_if_changed(target, result)
            audit_action("write_file", target=target, outcome="succeeded", details={"changed": result["changed"]})
            return result

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("create_file")
    def create_file(
        path: PathArg,
        content: Annotated[str, Field(max_length=2_000_000)] = "",
        encoding: EncodingArg = "utf-8",
        create_parents: bool = False,
        validate_python: bool = True,
    ) -> dict[str, Any]:
        """Create one new file and fail if it already exists."""
        with RESOURCE_LOCKS.sync(path):
            target = resolve_path(path)
            if target.exists():
                raise FileExistsError(f"Target already exists: {target}")
            audit_action("create_file", target=target, details={"content_chars": len(content)})
            result = service.atomic_write(
                target,
                content,
                encoding=encoding,
                create_parents=create_parents,
                backup=False,
                validate_python=validate_python,
                exclusive_create=True,
            )
            _invalidate_if_changed(target, result)
            audit_action("create_file", target=target, outcome="succeeded")
            return result

    @mcp.tool(annotations=DESTRUCTIVE, structured_output=True)
    @compact_errors("delete_file")
    def delete_file(
        path: PathArg,
        recursive: bool = False,
        missing_ok: bool = False,
    ) -> dict[str, Any]:
        """Permanently delete a file or, with recursive=true, a directory tree."""
        with RESOURCE_LOCKS.sync(path):
            target = resolve_path(path)
            if not target.exists() and not target.is_symlink():
                if missing_ok:
                    return ok(path=str(target), deleted=False, reason="missing")
                raise FileNotFoundError(f"Target not found: {target}")
            target_type = "directory" if target.is_dir() and not target.is_symlink() else "file"
            size = target.stat().st_size if target_type == "file" else None
            audit_action("delete_file", target=target, details={"recursive": recursive, "type": target_type}, durable=True)
            if target_type == "directory":
                if not recursive:
                    target.rmdir()
                else:
                    shutil.rmtree(target)
            else:
                target.unlink()
            PYTHON_METADATA_CACHE.invalidate(target, recursive=target_type == "directory")
            audit_action("delete_file", target=target, outcome="succeeded", details={"type": target_type}, durable=True)
            return ok(path=str(target), deleted=True, type=target_type, bytes_removed=size, recoverable=False)

    @mcp.tool(annotations=DESTRUCTIVE, structured_output=True)
    @compact_errors("move_file")
    def move_file(
        source: PathArg,
        destination: PathArg,
        overwrite: bool = False,
        create_parents: bool = False,
    ) -> dict[str, Any]:
        """Move or rename one file or directory with explicit overwrite semantics."""
        with RESOURCE_LOCKS.sync(source, destination):
            src = resolve_path(source)
            dst = resolve_path(destination)
            if not src.exists() and not src.is_symlink():
                raise FileNotFoundError(f"Source not found: {src}")
            if src == dst:
                raise ToolError("same_path", "Source and destination resolve to the same path.")
            src_is_dir = src.is_dir() and not src.is_symlink()
            dst_was_dir = dst.is_dir() and not dst.is_symlink()
            if src_is_dir and dst.is_relative_to(src):
                raise ToolError("destination_inside_source", "A directory cannot be moved inside itself.")
            if dst.exists() or dst.is_symlink():
                if not overwrite:
                    raise FileExistsError(f"Destination exists: {dst}")
                audit_action("move_file_overwrite", target=dst, details={"source": str(src)}, durable=True)
                shutil.rmtree(dst) if dst.is_dir() and not dst.is_symlink() else dst.unlink()
            if create_parents:
                dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.parent.is_dir():
                raise FileNotFoundError(f"Destination parent not found: {dst.parent}")
            audit_action("move_file", target=src, details={"destination": str(dst), "overwrite": overwrite}, durable=True)
            shutil.move(str(src), str(dst))
            recursive_invalidation = src_is_dir or dst_was_dir
            PYTHON_METADATA_CACHE.invalidate(src, recursive=recursive_invalidation)
            PYTHON_METADATA_CACHE.invalidate(dst, recursive=recursive_invalidation)
            audit_action("move_file", target=src, outcome="succeeded", details={"destination": str(dst)}, durable=True)
            return ok(source=str(src), destination=str(dst), moved=True)

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("copy_file")
    def copy_file(
        source: PathArg,
        destination: PathArg,
        overwrite: bool = False,
        create_parents: bool = False,
        preserve_metadata: bool = True,
    ) -> dict[str, Any]:
        """Copy one regular file and return structured metadata."""
        with RESOURCE_LOCKS.sync(source, destination):
            src = resolve_path(source)
            dst = resolve_path(destination)
            if not src.is_file():
                raise FileNotFoundError(f"Source file not found: {src}")
            if dst.is_dir():
                raise ToolError("destination_is_directory", f"Destination must be a file path: {dst}")
            if dst.exists() and not overwrite:
                raise FileExistsError(f"Destination exists: {dst}")
            if create_parents:
                dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.parent.is_dir():
                raise FileNotFoundError(f"Destination parent not found: {dst.parent}")
            audit_action("copy_file", target=src, details={"destination": str(dst), "overwrite": overwrite})
            (shutil.copy2 if preserve_metadata else shutil.copyfile)(src, dst)
            PYTHON_METADATA_CACHE.invalidate(dst)
            audit_action("copy_file", target=src, outcome="succeeded", details={"destination": str(dst)})
            return ok(source=str(src), destination=str(dst), copied=True, bytes=dst.stat().st_size, sha256=_hash_file(dst))

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("search_files")
    def search_files(
        path: PathArg,
        query: Annotated[str, Field(min_length=1, max_length=5_000)],
        search_type: Literal["name", "content", "both"] = "content",
        regex: bool = False,
        case_sensitive: bool = False,
        glob: Annotated[str | None, Field(max_length=500)] = None,
        include_hidden: bool = False,
        exclude_common: bool = True,
        count_mode: Literal["exact", "none"] = "exact",
        snapshot: bool = False,
        cursor: Annotated[str | None, Field(max_length=64)] = None,
        offset: Annotated[int, Field(ge=0, le=1_000_000)] = 0,
        max_results: Annotated[int, Field(ge=1, le=500)] = 50,
        max_scan_files: Annotated[int, Field(ge=100, le=500_000)] = 100_000,
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
    ) -> dict[str, Any]:
        """Search files with optional fast first-page mode or immutable snapshot cursors."""

        root = resolve_path(path)
        fingerprint = search_fingerprint(
            root,
            {
                "query": query,
                "search_type": search_type,
                "regex": regex,
                "case_sensitive": case_sensitive,
                "glob": glob,
                "include_hidden": include_hidden,
                "exclude_common": exclude_common,
                "count_mode": count_mode,
                "max_scan_files": max_scan_files,
                "timeout_sec": float(timeout_sec),
            },
        )
        if cursor is not None:
            if snapshot:
                raise ToolError("ambiguous_search_pagination", "snapshot=true cannot be combined with cursor.")
            if offset != 0:
                raise ToolError("ambiguous_search_pagination", "offset must remain 0 when continuing with cursor.")
            snapshot_page = SEARCH_SNAPSHOTS.read_page(cursor, fingerprint=fingerprint, limit=max_results)
            return {
                "ok": True,
                "root": str(root),
                "scanned_files": 0,
                **SEARCH_SNAPSHOTS.public_page(snapshot_page),
            }

        if not root.is_dir():
            raise NotADirectoryError(f"Directory not found: {root}")
        if snapshot and offset != 0:
            raise ToolError("snapshot_requires_first_page", "snapshot=true requires offset=0.")

        if count_mode == "none":
            if search_type != "name":
                raise ToolError(
                    "streaming_name_search_only",
                    "count_mode='none' is currently supported only for search_type='name'.",
                    hint="Use count_mode='exact' for content/both search, or search_type='name' for streaming first-page results.",
                )
            if snapshot:
                named, scan_truncated = service.search_by_name(
                    root,
                    query,
                    regex=regex,
                    case_sensitive=case_sensitive,
                    glob=glob,
                    include_hidden=include_hidden,
                    max_scan_files=max_scan_files,
                    exclude_common=exclude_common,
                )
                snapshot_items = [{"path": str(item), "matched_in": ["name"]} for item in named]
                handle = SEARCH_SNAPSHOTS.create(
                    snapshot_items,
                    fingerprint=fingerprint,
                    count_mode="none",
                    result_order="traversal",
                    scan_truncated=scan_truncated,
                    total_count=None,
                )
                snapshot_page = SEARCH_SNAPSHOTS.first_page(handle, fingerprint=fingerprint, limit=max_results)
                return {
                    "ok": True,
                    "root": str(root),
                    "snapshot_created": True,
                    **SEARCH_SNAPSHOTS.public_page(snapshot_page),
                }

            streamed = service.search_by_name_streaming(
                root,
                query,
                regex=regex,
                case_sensitive=case_sensitive,
                glob=glob,
                include_hidden=include_hidden,
                max_scan_files=max_scan_files,
                exclude_common=exclude_common,
                offset=offset,
                limit=max_results,
            )
            items = [{"path": str(item), "matched_in": ["name"]} for item in streamed.items]
            next_offset = offset + len(items) if streamed.continuation_available else None
            return {
                "ok": True,
                "root": str(root),
                "scan_truncated": streamed.scan_truncated,
                "scanned_files": streamed.scanned_files,
                "count_mode": "none",
                "result_order": "traversal",
                "items": items,
                "count": len(items),
                "total_count": None,
                "offset": offset,
                "has_more": streamed.has_more,
                "next_offset": next_offset,
                "cursor": None,
                "truncated": streamed.has_more,
            }

        matches: dict[str, set[str]] = {}
        truncated = False
        if search_type in {"name", "both"}:
            named, was_truncated = service.search_by_name(
                root,
                query,
                regex=regex,
                case_sensitive=case_sensitive,
                glob=glob,
                include_hidden=include_hidden,
                max_scan_files=max_scan_files,
                exclude_common=exclude_common,
            )
            truncated |= was_truncated
            for item in named:
                matches.setdefault(str(item), set()).add("name")
        if search_type in {"content", "both"}:
            content, was_truncated = service.search_by_content(
                root,
                query,
                regex=regex,
                case_sensitive=case_sensitive,
                glob=glob,
                include_hidden=include_hidden,
                timeout_sec=timeout_sec,
                exclude_common=exclude_common,
            )
            truncated |= was_truncated
            for item in content:
                absolute = item if item.is_absolute() else root / item
                matches.setdefault(str(absolute.resolve(strict=False)), set()).add("content")
        ordered = [
            {"path": key, "matched_in": sorted(value)} for key, value in sorted(matches.items(), key=lambda pair: pair[0].casefold())
        ]
        total = len(ordered)
        if snapshot:
            handle = SEARCH_SNAPSHOTS.create(
                ordered,
                fingerprint=fingerprint,
                count_mode="exact",
                result_order="path",
                scan_truncated=truncated,
                total_count=total,
            )
            snapshot_page = SEARCH_SNAPSHOTS.first_page(handle, fingerprint=fingerprint, limit=max_results)
            return {
                "ok": True,
                "root": str(root),
                "snapshot_created": True,
                **SEARCH_SNAPSHOTS.public_page(snapshot_page),
            }

        total = len(ordered)
        result = page(ordered[offset : offset + max_results], total=total, offset=offset, limit=max_results)
        result["truncated"] = bool(result["truncated"] or truncated)
        return {
            "ok": True,
            "root": str(root),
            "scan_truncated": truncated,
            "count_mode": "exact",
            "result_order": "path",
            "cursor": None,
            **result,
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("list_directory")
    def list_directory(
        path: PathArg,
        offset: Annotated[int, Field(ge=0, le=1_000_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=500)] = 50,
        pattern: Annotated[str | None, Field(max_length=500)] = None,
        include_hidden: bool = False,
        sort_by: Literal["name", "size", "modified"] = "name",
        max_scan_items: Annotated[int, Field(ge=1, le=1_000_000)] = 100_000,
    ) -> dict[str, Any]:
        """List one directory level with filtering, pagination, and no recursive dump."""

        return service.directory_listing(
            path,
            offset=offset,
            limit=max_items,
            pattern=pattern,
            include_hidden=include_hidden,
            sort_by=sort_by,
            scan_limit=max_scan_items,
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("get_file_info")
    def get_file_info(
        path: PathArg,
        include_sha256: bool = False,
        include_child_count: bool = True,
        max_child_count: Annotated[int, Field(ge=1, le=1_000_000)] = 100_000,
    ) -> dict[str, Any]:
        """Return compact metadata for one file, directory, or symlink."""

        target = resolve_path(path)
        if not target.exists() and not target.is_symlink():
            raise FileNotFoundError(f"Target not found: {target}")
        info = target.lstat()
        kind = "symlink" if target.is_symlink() else "directory" if target.is_dir() else "file"
        result: dict[str, Any] = {
            "ok": True,
            "path": str(target),
            "name": target.name,
            "type": kind,
            "size": info.st_size if kind == "file" else None,
            "created": datetime.fromtimestamp(info.st_ctime, timezone.utc).isoformat(timespec="seconds"),
            "modified": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "read_only": not os.access(target, os.W_OK),
        }
        if kind == "file" and include_sha256:
            result["sha256"] = _hash_file(target)
        if kind == "directory" and include_child_count:
            with os.scandir(target) as children:
                child_count = sum(1 for _, _entry in zip(range(max_child_count + 1), children, strict=False))
            result["child_count"] = min(child_count, max_child_count)
            result["child_count_truncated"] = child_count > max_child_count
        if kind == "symlink":
            result["link_target"] = os.readlink(target)
        return result

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("replace_exact")
    def replace_exact(
        path: PathArg,
        old: Annotated[str, Field(min_length=1, max_length=100_000)],
        new: Annotated[str, Field(max_length=200_000)],
        expected_count: Annotated[int, Field(ge=1, le=100)] = 1,
        encoding: EncodingArg = "auto",
        backup: bool = True,
        validate_python: bool = True,
    ) -> dict[str, Any]:
        """Replace an exact small fragment only when its match count is known."""
        with RESOURCE_LOCKS.sync(path):
            target, before, used_encoding = service.load_text(path, encoding)
            after, count = service.exact_replace(before, old, new, expected_count)
            audit_action("replace_exact", target=target, details={"expected_count": expected_count})
            result = _edit_result(target, before, after, used_encoding, backup=backup, validate_python=validate_python)
            _invalidate_if_changed(target, result)
            result["replacements"] = count
            audit_action("replace_exact", target=target, outcome="succeeded", details={"replacements": count})
            return result

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("replace_between_anchors")
    def replace_between_anchors(
        path: PathArg,
        start_marker: Annotated[str, Field(min_length=1, max_length=10_000)],
        end_marker: Annotated[str, Field(min_length=1, max_length=10_000)],
        replacement: Annotated[str, Field(max_length=200_000)],
        include_markers: bool = False,
        encoding: EncodingArg = "auto",
        backup: bool = True,
        validate_python: bool = True,
    ) -> dict[str, Any]:
        """Replace one uniquely anchored region without touching the rest of the file."""
        with RESOURCE_LOCKS.sync(path):
            target, before, used_encoding = service.load_text(path, encoding)
            after = service.anchor_replace(before, start_marker, end_marker, replacement, include_markers)
            audit_action("replace_between_anchors", target=target, details={"include_markers": include_markers})
            result = _edit_result(target, before, after, used_encoding, backup=backup, validate_python=validate_python)
            _invalidate_if_changed(target, result)
            audit_action("replace_between_anchors", target=target, outcome="succeeded")
            return result

    def replace_python_body(
        path: str,
        qualified_name: str,
        new_body: str,
        kind: Literal["function", "class"],
        encoding: str,
        backup: bool,
    ) -> dict[str, Any]:
        with RESOURCE_LOCKS.sync(path):
            target, before, used_encoding = service.load_text(path, encoding)
            service._validate_python(target, before)
            after = service.replace_symbol_body(before, qualified_name, new_body, kind)
            audit_action(f"replace_{kind}", target=target, details={"symbol": qualified_name})
            result = _edit_result(target, before, after, used_encoding, backup=backup, validate_python=True)
            _invalidate_if_changed(target, result)
            result["symbol"] = qualified_name
            audit_action(f"replace_{kind}", target=target, outcome="succeeded", details={"symbol": qualified_name})
            return result

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("replace_function")
    def replace_function(
        path: PathArg,
        function_name: Annotated[str, Field(min_length=1, max_length=500)],
        new_body: Annotated[str, Field(min_length=1, max_length=200_000)],
        encoding: EncodingArg = "auto",
        backup: bool = True,
    ) -> dict[str, Any]:
        """Replace only a Python function or method body selected by qualified name."""

        return replace_python_body(path, function_name, new_body, "function", encoding, backup)

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("replace_class")
    def replace_class(
        path: PathArg,
        class_name: Annotated[str, Field(min_length=1, max_length=500)],
        new_body: Annotated[str, Field(min_length=1, max_length=200_000)],
        encoding: EncodingArg = "auto",
        backup: bool = True,
    ) -> dict[str, Any]:
        """Replace only a Python class body selected by qualified name."""

        return replace_python_body(path, class_name, new_body, "class", encoding, backup)

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("safe_refactor")
    def safe_refactor(
        path: PathArg,
        edits: Annotated[list[RefactorEdit], Field(min_length=1, max_length=25)],
        encoding: EncodingArg = "auto",
        validation_command: Annotated[list[str] | None, Field(max_length=50)] = None,
        validation_cwd: Annotated[str | None, Field(max_length=32_767)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=600)] = 60,
    ) -> dict[str, Any]:
        """Validate, back up, atomically edit, test, and roll back on validation failure."""
        with RESOURCE_LOCKS.sync(path):
            target = resolve_path(path)
            details: dict[str, Any] = {"edit_count": len(edits), "has_validation": bool(validation_command)}
            if validation_command:
                details.update(
                    {
                        "validation_executable": validation_command[0],
                        "validation_argument_count": len(validation_command) - 1,
                        "validation_sha256": hashlib.sha256("\0".join(validation_command).encode("utf-8", errors="replace")).hexdigest(),
                    }
                )
            audit_action("safe_refactor", target=target, details=details)
            try:
                result = service.apply_safe_refactor(
                    path,
                    edits,
                    encoding=encoding,
                    validation_command=validation_command,
                    validation_cwd=validation_cwd,
                    timeout_sec=timeout_sec,
                )
            except Exception:
                # Validation failure can write and then roll back before raising.
                # Invalidate even on failure so no concurrent parse of the transient
                # content survives after the rollback.
                PYTHON_METADATA_CACHE.invalidate(target)
                raise
            _invalidate_if_changed(target, result)
            audit_action("safe_refactor", target=target, outcome="succeeded", details={"edit_count": len(edits)})
            return result
