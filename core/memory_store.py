"""Versioned compact project-memory storage with legacy migration support."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.errors import ToolError

MEMORY_SCHEMA_VERSION = 2
MEMORY_MAX_BYTES = 131_072
SECTIONS = ("architecture_decisions", "important_paths", "user_preferences", "previous_fixes")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty_memory(project_name: str) -> dict[str, Any]:
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "project": project_name,
        "revision": 0,
        "updated_at": None,
        **{section: [] for section in SECTIONS},
    }


def _legacy_id(project_name: str, section: str, index: int, text: str) -> str:
    raw = f"{project_name}\0{section}\0{index}\0{text}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:32]


def _clean_text(value: object) -> str:
    return str(value).strip()[:2_000]


def _legacy_created_at(path: Path, data: dict[str, Any]) -> str:
    updated_at = data.get("updated_at")
    if isinstance(updated_at, str) and updated_at:
        return updated_at
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return utc_now()
    return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat(timespec="seconds")


def _normalize_record(
    raw: object,
    *,
    project_name: str,
    section: str,
    index: int,
    legacy_created_at: str,
) -> dict[str, Any] | None:
    if isinstance(raw, str):
        text = _clean_text(raw)
        if not text:
            return None
        return {
            "id": _legacy_id(project_name, section, index, text),
            "text": text,
            "created_at": legacy_created_at,
            "revision": 1,
        }
    if not isinstance(raw, dict):
        raise ToolError("memory_corrupt", f"Memory section '{section}' contains an unsupported item.")
    text = _clean_text(raw.get("text", ""))
    if not text:
        return None
    item_id = raw.get("id")
    created_at = raw.get("created_at")
    revision = raw.get("revision", 1)
    if not isinstance(item_id, str) or not item_id or len(item_id) > 128:
        raise ToolError("memory_corrupt", f"Memory section '{section}' contains an invalid item id.")
    if not isinstance(created_at, str) or not created_at or len(created_at) > 100:
        raise ToolError("memory_corrupt", f"Memory section '{section}' contains an invalid created_at value.")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ToolError("memory_corrupt", f"Memory section '{section}' contains an invalid item revision.")
    return {"id": item_id, "text": text, "created_at": created_at, "revision": revision}


def load_memory(path: Path, project_name: str) -> dict[str, Any]:
    if not path.exists():
        return empty_memory(project_name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError("memory_corrupt", f"Cannot read project memory {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ToolError("memory_corrupt", f"Project memory {path} must contain a JSON object.")
    schema_version = data.get("schema_version", 1)
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version < 1:
        raise ToolError("memory_corrupt", f"Project memory {path} has an invalid schema version.")
    if schema_version > MEMORY_SCHEMA_VERSION:
        raise ToolError(
            "memory_schema_newer",
            f"Project memory schema {schema_version} is newer than supported schema {MEMORY_SCHEMA_VERSION}.",
        )
    revision = data.get("revision", 0)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ToolError("memory_corrupt", f"Project memory {path} has an invalid revision.")
    result = empty_memory(project_name)
    result["revision"] = revision
    updated_at = data.get("updated_at")
    result["updated_at"] = updated_at if isinstance(updated_at, str) or updated_at is None else None
    legacy_created_at = _legacy_created_at(path, data)
    for section in SECTIONS:
        raw_items = data.get(section, [])
        if not isinstance(raw_items, list):
            raise ToolError("memory_corrupt", f"Memory section '{section}' must be a list.")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_items[:100]):
            record = _normalize_record(
                raw,
                project_name=project_name,
                section=section,
                index=index,
                legacy_created_at=legacy_created_at,
            )
            if record is None or record["text"] in seen:
                continue
            seen.add(record["text"])
            records.append(record)
        result[section] = records
    return result


def merge_texts(
    current: dict[str, Any],
    incoming: dict[str, list[str]],
    *,
    replace: bool,
    now: str | None = None,
) -> tuple[dict[str, Any], bool]:
    timestamp = now or utc_now()
    changed = False
    next_data = empty_memory(str(current["project"]))
    next_data["revision"] = int(current.get("revision", 0))
    next_data["updated_at"] = current.get("updated_at")
    for section in SECTIONS:
        current_records = [dict(record) for record in current[section]]
        incoming_texts: list[str] = []
        seen_incoming: set[str] = set()
        for raw in incoming.get(section, []):
            text = _clean_text(raw)
            if not text or text in seen_incoming:
                continue
            seen_incoming.add(text)
            incoming_texts.append(text)

        if replace:
            previous_by_text = {str(record["text"]): record for record in current_records}
            records: list[dict[str, Any]] = []
            for text in incoming_texts:
                previous = previous_by_text.get(text)
                if previous is not None:
                    records.append(dict(previous))
                else:
                    records.append({"id": uuid.uuid4().hex, "text": text, "created_at": timestamp, "revision": 1})
            if [record["text"] for record in records] != [record["text"] for record in current_records]:
                changed = True
        else:
            records = current_records
            existing_texts = {str(record["text"]) for record in records}
            for text in incoming_texts:
                if text in existing_texts or len(records) >= 100:
                    continue
                records.append({"id": uuid.uuid4().hex, "text": text, "created_at": timestamp, "revision": 1})
                existing_texts.add(text)
                changed = True
        next_data[section] = records[:100]

    if changed:
        next_data["revision"] = int(current.get("revision", 0)) + 1
        next_data["updated_at"] = timestamp
    return next_data, changed


def atomic_save(path: Path, data: dict[str, Any]) -> int:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) > MEMORY_MAX_BYTES:
        raise ToolError("memory_too_large", "Project memory exceeds the 128 KiB compact-memory limit.")
    path.parent.mkdir(parents=True, exist_ok=True)
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
    return len(raw)
