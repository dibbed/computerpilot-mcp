"""MCP registration for compact versioned JSON project memory."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from core.audit import audit_action
from core.config import SETTINGS, ensure_runtime_dirs
from core.errors import ToolError
from core.memory_store import MEMORY_SCHEMA_VERSION, SECTIONS, atomic_save, empty_memory, load_memory, merge_texts
from core.resource_locks import RESOURCE_LOCKS
from core.response import page
from core.tooling import MUTATING, READ_ONLY, compact_errors

ProjectName = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")]
MemoryItems = Annotated[list[str] | None, Field(max_length=100)]


def _path(project_name: str) -> Path:
    ensure_runtime_dirs()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", project_name):
        raise ToolError("invalid_project_name", "Project name may contain only letters, numbers, dot, underscore, and hyphen.")
    return SETTINGS.memory_dir / f"{project_name}.json"


def _empty(project_name: str) -> dict[str, Any]:
    return empty_memory(project_name)


def _load(project_name: str) -> dict[str, Any]:
    return load_memory(_path(project_name), project_name)


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("memory_read")
    def memory_read(
        project_name: ProjectName,
        section: Literal["architecture_decisions", "important_paths", "user_preferences", "previous_fixes"] | None = None,
        offset: Annotated[int, Field(ge=0, le=10_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        """Read versioned compact project-memory records with bounded pagination."""

        data = _load(project_name)
        common = {
            "ok": True,
            "schema_version": data["schema_version"],
            "project": project_name,
            "revision": data["revision"],
            "updated_at": data["updated_at"],
        }
        if section:
            values = data[section]
            return {
                **common,
                "section": section,
                **page(values[offset : offset + max_items], total=len(values), offset=offset, limit=max_items),
            }
        samples = {name: data[name][:max_items] for name in SECTIONS}
        counts = {name: len(data[name]) for name in SECTIONS}
        return {
            **common,
            "counts": counts,
            "sections": samples,
            "truncated": any(count > max_items for count in counts.values()),
        }

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("memory_update")
    def memory_update(
        project_name: ProjectName,
        architecture_decisions: MemoryItems = None,
        important_paths: MemoryItems = None,
        user_preferences: MemoryItems = None,
        previous_fixes: MemoryItems = None,
        replace: bool = False,
        source: Literal["user", "project_scan", "manual", "tool"] | None = None,
        source_ref: Annotated[str | None, Field(max_length=2_000)] = None,
        verified: bool = False,
        expected_revision: Annotated[int | None, Field(ge=0)] = None,
    ) -> dict[str, Any]:
        """Merge or replace bounded project memory with provenance and optimistic revision checks."""

        with RESOURCE_LOCKS.sync(_path(project_name)):
            path = _path(project_name)
            current = _load(project_name)
            current_revision = int(current["revision"])
            if replace and expected_revision is None:
                raise ToolError(
                    "memory_revision_required",
                    "replace=True requires expected_revision to prevent blind overwrites.",
                    hint="Call memory_read first and retry with its current revision.",
                )
            if expected_revision is not None and expected_revision != current_revision:
                raise ToolError(
                    "memory_conflict",
                    f"Expected memory revision {expected_revision}, but current revision is {current_revision}.",
                    hint="Read the latest memory, merge your changes, and retry with the new revision.",
                )
            incoming = {
                "architecture_decisions": architecture_decisions or [],
                "important_paths": important_paths or [],
                "user_preferences": user_preferences or [],
                "previous_fixes": previous_fixes or [],
            }
            next_data, changed = merge_texts(
                current,
                incoming,
                replace=replace,
                source=source,
                source_ref=source_ref,
                verified=verified,
            )
            audit_action(
                "memory_update",
                target=path,
                details={
                    "replace": replace,
                    "changed": changed,
                    "revision_before": current["revision"],
                    "revision_after": next_data["revision"],
                    "expected_revision": expected_revision,
                    "source": source or "manual",
                    "verified": verified,
                    "item_counts": {key: len(value) for key, value in incoming.items()},
                },
            )
            size = atomic_save(path, next_data)
            return {
                "ok": True,
                "schema_version": MEMORY_SCHEMA_VERSION,
                "project": project_name,
                "path": str(path),
                "changed": changed,
                "revision": next_data["revision"],
                "updated_at": next_data["updated_at"],
                "counts": {section: len(next_data[section]) for section in SECTIONS},
                "bytes": size,
            }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("memory_list")
    def memory_list(
        offset: Annotated[int, Field(ge=0, le=10_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        """List local project-memory files with schema/revision metadata and pagination."""

        ensure_runtime_dirs()
        rows: list[dict[str, Any]] = []
        for path in SETTINGS.memory_dir.glob("*.json"):
            try:
                data = load_memory(path, path.stem)
                counts = {section: len(data[section]) for section in SECTIONS}
                rows.append(
                    {
                        "project": path.stem,
                        "schema_version": data["schema_version"],
                        "revision": data["revision"],
                        "updated_at": data["updated_at"],
                        "counts": counts,
                        "bytes": path.stat().st_size,
                    }
                )
            except (OSError, ToolError) as exc:
                rows.append({"project": path.stem, "error": exc.code if isinstance(exc, ToolError) else "unreadable"})
        rows.sort(key=lambda item: item["project"].casefold())
        total = len(rows)
        return {"ok": True, **page(rows[offset : offset + max_items], total=total, offset=offset, limit=max_items)}
