"""MCP registration for compact JSON project memory."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from core.audit import audit_action
from core.config import SETTINGS, ensure_runtime_dirs
from core.errors import ToolError
from core.response import page
from core.tooling import MUTATING, READ_ONLY, compact_errors

SECTIONS = ("architecture_decisions", "important_paths", "user_preferences", "previous_fixes")
ProjectName = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")]
MemoryItems = Annotated[list[str] | None, Field(max_length=100)]


def _path(project_name: str) -> Path:
    ensure_runtime_dirs()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", project_name):
        raise ToolError("invalid_project_name", "Project name may contain only letters, numbers, dot, underscore, and hyphen.")
    return SETTINGS.memory_dir / f"{project_name}.json"


def _empty(project_name: str) -> dict[str, Any]:
    return {"project": project_name, "updated_at": None, **{section: [] for section in SECTIONS}}


def _load(project_name: str) -> dict[str, Any]:
    path = _path(project_name)
    if not path.exists():
        return _empty(project_name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError("memory_corrupt", f"Cannot read project memory {path}: {exc}") from exc
    result = _empty(project_name)
    result["updated_at"] = data.get("updated_at")
    for section in SECTIONS:
        result[section] = [str(item) for item in data.get(section, [])][:100]
    return result


def _atomic_save(path: Path, data: dict[str, Any]) -> None:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) > 131_072:
        raise ToolError("memory_too_large", "Project memory exceeds the 128 KiB compact-memory limit.")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _clean_items(items: list[str]) -> list[str]:
    result = []
    seen = set()
    for raw in items:
        item = raw.strip()[:2_000]
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result[:100]


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("memory_read")
    def memory_read(
        project_name: ProjectName,
        section: Literal["architecture_decisions", "important_paths", "user_preferences", "previous_fixes"] | None = None,
        offset: Annotated[int, Field(ge=0, le=10_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        """Read one compact project-memory section or bounded samples of all sections."""

        data = _load(project_name)
        if section:
            values = data[section]
            return {
                "ok": True,
                "project": project_name,
                "updated_at": data["updated_at"],
                "section": section,
                **page(values[offset : offset + max_items], total=len(values), offset=offset, limit=max_items),
            }
        samples = {name: values[:max_items] for name, values in data.items() if name in SECTIONS}
        counts = {name: len(data[name]) for name in SECTIONS}
        return {
            "ok": True,
            "project": project_name,
            "updated_at": data["updated_at"],
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
    ) -> dict[str, Any]:
        """Merge or replace bounded project memory without storing arbitrary raw transcripts."""

        path = _path(project_name)
        current = _empty(project_name) if replace else _load(project_name)
        incoming = {
            "architecture_decisions": architecture_decisions or [],
            "important_paths": important_paths or [],
            "user_preferences": user_preferences or [],
            "previous_fixes": previous_fixes or [],
        }
        for section, items in incoming.items():
            base = [] if replace else current[section]
            current[section] = _clean_items([*base, *items])
        current["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        audit_action(
            "memory_update", target=path, details={"replace": replace, "item_counts": {key: len(value) for key, value in incoming.items()}}
        )
        _atomic_save(path, current)
        return {
            "ok": True,
            "project": project_name,
            "path": str(path),
            "updated_at": current["updated_at"],
            "counts": {section: len(current[section]) for section in SECTIONS},
            "bytes": path.stat().st_size,
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("memory_list")
    def memory_list(
        offset: Annotated[int, Field(ge=0, le=10_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        """List local project-memory files with metadata and pagination."""

        ensure_runtime_dirs()
        rows = []
        for path in SETTINGS.memory_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                counts = {section: len(data.get(section, [])) for section in SECTIONS}
                rows.append({"project": path.stem, "updated_at": data.get("updated_at"), "counts": counts, "bytes": path.stat().st_size})
            except (OSError, ValueError):
                rows.append({"project": path.stem, "error": "unreadable"})
        rows.sort(key=lambda item: item["project"].casefold())
        total = len(rows)
        return {"ok": True, **page(rows[offset : offset + max_items], total=total, offset=offset, limit=max_items)}
