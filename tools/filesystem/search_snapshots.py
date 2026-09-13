"""Bounded immutable disk-backed snapshots for consistent search pagination."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import chain
from pathlib import Path
from typing import Any

from core.config import SETTINGS
from core.errors import ToolError
from core.resource_locks import canonical_path

SNAPSHOT_SCHEMA_VERSION = 1
_CURSOR_RE = re.compile(r"^s1\.([0-9a-f]{32})\.(0|[1-9][0-9]{0,9})$")
_SNAPSHOT_RE = re.compile(r"^[0-9a-f]{32}\.jsonl$")


@dataclass(frozen=True, slots=True)
class SnapshotHandle:
    snapshot_id: str
    expires_at: float
    item_count: int
    bytes: int


@dataclass(frozen=True, slots=True)
class SnapshotPage:
    snapshot_id: str
    items: tuple[dict[str, Any], ...]
    offset: int
    total_count: int | None
    item_count: int
    count_mode: str
    result_order: str
    scan_truncated: bool
    has_more: bool
    next_offset: int | None
    cursor: str | None
    expires_at: float


def search_fingerprint(root: Path, parameters: Mapping[str, Any]) -> str:
    """Hash only result-shaping search parameters plus canonical root identity."""

    payload = {
        "root": canonical_path(root),
        "parameters": dict(parameters),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _iso_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


class SearchSnapshotStore:
    """Persist bounded search result sets and expose opaque cursor pagination."""

    def __init__(
        self,
        directory: Path | None = None,
        *,
        ttl_sec: int | None = None,
        max_bytes: int | None = None,
        max_count: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.directory = SETTINGS.search_snapshot_dir if directory is None else directory
        self.ttl_sec = SETTINGS.search_snapshot_ttl_sec if ttl_sec is None else ttl_sec
        self.max_bytes = SETTINGS.search_snapshot_max_bytes if max_bytes is None else max_bytes
        self.max_count = SETTINGS.search_snapshot_max_count if max_count is None else max_count
        if self.ttl_sec < 1 or self.max_bytes < 1 or self.max_count < 1:
            raise ValueError("Search snapshot limits must be positive.")
        self._clock = clock
        self._lock = threading.RLock()

    @staticmethod
    def cursor(snapshot_id: str, offset: int) -> str:
        if not re.fullmatch(r"[0-9a-f]{32}", snapshot_id) or offset < 0:
            raise ValueError("Invalid snapshot cursor components.")
        return f"s1.{snapshot_id}.{offset}"

    @staticmethod
    def parse_cursor(cursor: str) -> tuple[str, int]:
        matched = _CURSOR_RE.fullmatch(cursor)
        if matched is None:
            raise ToolError("invalid_search_cursor", "Search cursor is malformed or unsupported.")
        return matched.group(1), int(matched.group(2))

    def create(
        self,
        items: Sequence[Mapping[str, Any]],
        *,
        fingerprint: str,
        count_mode: str,
        result_order: str,
        scan_truncated: bool,
        total_count: int | None,
    ) -> SnapshotHandle:
        now = self._clock()
        expires_at = now + self.ttl_sec
        snapshot_id = secrets.token_hex(16)
        header = {
            "kind": "meta",
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "created_at": now,
            "expires_at": expires_at,
            "fingerprint": fingerprint,
            "count_mode": count_mode,
            "result_order": result_order,
            "scan_truncated": bool(scan_truncated),
            "total_count": total_count,
            "item_count": len(items),
        }

        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._cleanup_expired_locked(now)
            descriptor, temp_name = tempfile.mkstemp(prefix=".search-snapshot-", suffix=".tmp", dir=self.directory)
            temp_path = Path(temp_name)
            final_path = self.directory / f"{snapshot_id}.jsonl"
            written = 0
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    for record in chain((header,), items):
                        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
                        written += len(line)
                        if written > self.max_bytes:
                            raise ToolError(
                                "search_snapshot_too_large",
                                f"Search snapshot exceeds the configured {self.max_bytes}-byte budget.",
                                hint="Use a narrower query or increase MCP_SEARCH_SNAPSHOT_MAX_BYTES.",
                            )
                        handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, final_path)
                self._enforce_quota_locked(protect=final_path, now=now)
            except BaseException:
                temp_path.unlink(missing_ok=True)
                final_path.unlink(missing_ok=True)
                raise

        return SnapshotHandle(snapshot_id=snapshot_id, expires_at=expires_at, item_count=len(items), bytes=written)

    def read_page(self, cursor: str, *, fingerprint: str, limit: int) -> SnapshotPage:
        if limit < 1:
            raise ValueError("Snapshot page limit must be positive.")
        snapshot_id, offset = self.parse_cursor(cursor)
        now = self._clock()
        with self._lock:
            path = self.directory / f"{snapshot_id}.jsonl"
            if not path.is_file():
                raise ToolError("search_cursor_not_found", "Search cursor snapshot no longer exists.")
            header = self._read_header_locked(path)
            expires_at = float(header["expires_at"])
            if expires_at <= now:
                path.unlink(missing_ok=True)
                raise ToolError("search_cursor_expired", "Search cursor expired; run the search again.")
            if header.get("fingerprint") != fingerprint:
                raise ToolError(
                    "search_cursor_mismatch",
                    "Search cursor does not belong to these search parameters.",
                    hint="Reuse the same path/query/search options that created the cursor.",
                )
            item_count = int(header["item_count"])
            if offset > item_count:
                raise ToolError("search_cursor_out_of_range", "Search cursor offset is outside the snapshot.")

            items: list[dict[str, Any]] = []
            try:
                with path.open("r", encoding="utf-8", newline="") as handle:
                    next(handle)
                    for index, line in enumerate(handle):
                        if index < offset:
                            continue
                        if len(items) >= limit:
                            break
                        value = json.loads(line)
                        if not isinstance(value, dict):
                            raise ValueError("snapshot row is not an object")
                        items.append(value)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError, StopIteration) as exc:
                path.unlink(missing_ok=True)
                raise ToolError("search_snapshot_corrupt", "Search snapshot is unreadable; run the search again.") from exc

            next_position = offset + len(items)
            known_more = next_position < item_count
            scan_truncated = bool(header.get("scan_truncated"))
            has_more = known_more or scan_truncated
            next_offset = next_position if known_more else None
            next_cursor = self.cursor(snapshot_id, next_position) if known_more else None
            self._cleanup_expired_locked(now, exclude={path})
            return SnapshotPage(
                snapshot_id=snapshot_id,
                items=tuple(items),
                offset=offset,
                total_count=header.get("total_count"),
                item_count=item_count,
                count_mode=str(header["count_mode"]),
                result_order=str(header["result_order"]),
                scan_truncated=scan_truncated,
                has_more=has_more,
                next_offset=next_offset,
                cursor=next_cursor,
                expires_at=expires_at,
            )

    def first_page(self, handle: SnapshotHandle, *, fingerprint: str, limit: int) -> SnapshotPage:
        return self.read_page(self.cursor(handle.snapshot_id, 0), fingerprint=fingerprint, limit=limit)

    def cleanup(self) -> dict[str, int]:
        now = self._clock()
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            removed = self._cleanup_expired_locked(now)
            self._enforce_quota_locked(protect=None, now=now)
            files = self._snapshot_files_locked()
            return {
                "removed": removed,
                "count": len(files),
                "bytes": sum(path.stat().st_size for path in files if path.exists()),
            }

    def _read_header_locked(self, path: Path) -> dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                first = handle.readline()
            header = json.loads(first)
            if (
                not isinstance(header, dict)
                or header.get("kind") != "meta"
                or header.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
                or header.get("snapshot_id") != path.stem
            ):
                raise ValueError("invalid snapshot header")
            required = {"expires_at", "fingerprint", "count_mode", "result_order", "item_count"}
            if not required <= header.keys():
                raise ValueError("incomplete snapshot header")
            return header
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            path.unlink(missing_ok=True)
            raise ToolError("search_snapshot_corrupt", "Search snapshot is unreadable; run the search again.") from exc

    def _snapshot_files_locked(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return [path for path in self.directory.iterdir() if path.is_file() and _SNAPSHOT_RE.fullmatch(path.name)]

    def _cleanup_expired_locked(self, now: float, *, exclude: set[Path] | None = None) -> int:
        excluded = exclude or set()
        removed = 0
        for path in self._snapshot_files_locked():
            if path in excluded:
                continue
            try:
                header = self._read_header_locked(path)
            except ToolError:
                removed += 1
                continue
            if float(header["expires_at"]) <= now:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def _enforce_quota_locked(self, *, protect: Path | None, now: float) -> None:
        self._cleanup_expired_locked(now, exclude={protect} if protect is not None else None)
        files = self._snapshot_files_locked()
        records: list[tuple[float, Path, int]] = []
        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            records.append((stat.st_mtime_ns, path, stat.st_size))
        records.sort(key=lambda item: item[0])
        total_bytes = sum(item[2] for item in records)

        while len(records) > self.max_count or total_bytes > self.max_bytes:
            removable_index = next((index for index, (_, path, _) in enumerate(records) if path != protect), None)
            if removable_index is None:
                if protect is not None:
                    protect.unlink(missing_ok=True)
                raise ToolError(
                    "search_snapshot_quota_exceeded",
                    "Search snapshot cannot fit inside the configured snapshot quota.",
                )
            _, path, size = records.pop(removable_index)
            path.unlink(missing_ok=True)
            total_bytes -= size

    @staticmethod
    def public_page(page: SnapshotPage) -> dict[str, Any]:
        return {
            "snapshot_id": page.snapshot_id,
            "snapshot_expires_at": _iso_timestamp(page.expires_at),
            "from_snapshot": True,
            "snapshot_consistent": True,
            "items": list(page.items),
            "count": len(page.items),
            "total_count": page.total_count,
            "offset": page.offset,
            "has_more": page.has_more,
            "next_offset": page.next_offset,
            "cursor": page.cursor,
            "truncated": page.has_more,
            "scan_truncated": page.scan_truncated,
            "count_mode": page.count_mode,
            "result_order": page.result_order,
        }


SEARCH_SNAPSHOTS = SearchSnapshotStore()
