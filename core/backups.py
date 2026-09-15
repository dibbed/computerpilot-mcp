"""Bounded backup retention with coalesced background cleanup."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.config import SETTINGS, Settings


@dataclass(frozen=True, slots=True)
class BackupPolicy:
    """Retention limits for recoverable filesystem backups."""

    max_bytes: int
    max_age_sec: float
    cleanup_interval_sec: float


@dataclass(frozen=True, slots=True)
class BackupEntry:
    path: Path
    size: int
    created_at: float


@dataclass(frozen=True, slots=True)
class BackupCleanupResult:
    scanned_files: int
    scanned_bytes: int
    removed_files: int
    removed_bytes: int
    removed_for_age: int
    removed_for_quota: int
    remaining_files: int
    remaining_bytes: int
    quota_satisfied: bool
    safety_floor_preserved: bool
    errors: int

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)


def policy_from_settings(settings: Settings = SETTINGS) -> BackupPolicy:
    return BackupPolicy(
        max_bytes=settings.backup_max_bytes,
        max_age_sec=float(settings.backup_max_age_days * 86_400),
        cleanup_interval_sec=float(settings.backup_cleanup_interval_sec),
    )


def backup_created_at(path: Path, fallback_mtime: float) -> float:
    """Use the backup-event timestamp encoded in the filename, falling back for legacy names."""

    stamp = path.name.split("_", 1)[0]
    try:
        return datetime.strptime(stamp, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return fallback_mtime


def _scan_backups(directory: Path) -> tuple[list[BackupEntry], int]:
    entries: list[BackupEntry] = []
    errors = 0
    try:
        candidates = list(directory.glob("*.bak"))
    except OSError:
        return entries, 1
    for path in candidates:
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            errors += 1
            continue
        entries.append(
            BackupEntry(
                path=path,
                size=stat.st_size,
                created_at=backup_created_at(path, stat.st_mtime),
            )
        )
    entries.sort(key=lambda entry: (entry.created_at, entry.path.name.casefold()))
    return entries, errors


def cleanup_backups(
    directory: Path,
    policy: BackupPolicy,
    *,
    now: float | None = None,
    protected_paths: set[Path] | None = None,
) -> BackupCleanupResult:
    """Apply age and byte quotas while preserving at least the newest backup."""

    directory.mkdir(parents=True, exist_ok=True)
    entries, errors = _scan_backups(directory)
    scanned_files = len(entries)
    scanned_bytes = sum(entry.size for entry in entries)
    if not entries:
        return BackupCleanupResult(0, 0, 0, 0, 0, 0, 0, 0, True, False, errors)

    current = time.time() if now is None else now
    protected = {path.resolve(strict=False) for path in (protected_paths or set())}
    newest = entries[-1].path.resolve(strict=False)
    protected.add(newest)
    safety_floor_preserved = True

    survivors: list[BackupEntry] = []
    removed_files = 0
    removed_bytes = 0
    removed_for_age = 0
    removed_for_quota = 0

    cutoff = current - policy.max_age_sec if policy.max_age_sec > 0 else None
    for entry in entries:
        resolved = entry.path.resolve(strict=False)
        expired = cutoff is not None and entry.created_at < cutoff and resolved not in protected
        if not expired:
            survivors.append(entry)
            continue
        try:
            entry.path.unlink(missing_ok=True)
        except OSError:
            errors += 1
            survivors.append(entry)
            continue
        removed_files += 1
        removed_bytes += entry.size
        removed_for_age += 1

    remaining_bytes = sum(entry.size for entry in survivors)
    if policy.max_bytes > 0 and remaining_bytes > policy.max_bytes:
        kept: list[BackupEntry] = []
        for entry in survivors:
            resolved = entry.path.resolve(strict=False)
            if remaining_bytes <= policy.max_bytes or resolved in protected:
                kept.append(entry)
                continue
            try:
                entry.path.unlink(missing_ok=True)
            except OSError:
                errors += 1
                kept.append(entry)
                continue
            remaining_bytes -= entry.size
            removed_files += 1
            removed_bytes += entry.size
            removed_for_quota += 1
        survivors = kept

    # Concurrent cleanup may have removed files after our scan; recompute from
    # files that still exist so reported storage never intentionally overstates.
    remaining_files = 0
    actual_remaining_bytes = 0
    for entry in survivors:
        try:
            stat = entry.path.stat()
        except OSError:
            continue
        remaining_files += 1
        actual_remaining_bytes += stat.st_size
    quota_satisfied = policy.max_bytes <= 0 or actual_remaining_bytes <= policy.max_bytes

    return BackupCleanupResult(
        scanned_files=scanned_files,
        scanned_bytes=scanned_bytes,
        removed_files=removed_files,
        removed_bytes=removed_bytes,
        removed_for_age=removed_for_age,
        removed_for_quota=removed_for_quota,
        remaining_files=remaining_files,
        remaining_bytes=actual_remaining_bytes,
        quota_satisfied=quota_satisfied,
        safety_floor_preserved=safety_floor_preserved,
        errors=errors,
    )


class BackupRetentionManager:
    """Coalesce cleanup requests onto at most one low-priority daemon thread."""

    def __init__(
        self,
        directory: Path,
        policy: BackupPolicy,
    ) -> None:
        self.directory = directory
        self.policy = policy
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._dirty = False
        self._protected: set[Path] = set()
        self._last_run = 0.0
        self._last_result: BackupCleanupResult | None = None
        self._last_error: str | None = None

    def schedule(self, protected_path: Path | None = None) -> None:
        with self._lock:
            if protected_path is not None:
                self._protected.add(protected_path.resolve(strict=False))
            self._dirty = True
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="backup-retention", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._dirty:
                    self._thread = None
                    return
                self._dirty = False
                protected = set(self._protected)
                self._protected.clear()
                delay = max(0.0, self.policy.cleanup_interval_sec - (time.monotonic() - self._last_run))
            if delay:
                time.sleep(delay)
            try:
                result = cleanup_backups(self.directory, self.policy, protected_paths=protected)
            except Exception as exc:  # maintenance must never fail a successful user write
                with self._lock:
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self._last_run = time.monotonic()
                continue
            with self._lock:
                self._last_result = result
                self._last_error = None
                self._last_run = time.monotonic()

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait for any currently scheduled cleanup without raising maintenance errors."""

        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                thread = self._thread
            if thread is None:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            thread.join(timeout=remaining)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "dirty": self._dirty,
                "last_result": None if self._last_result is None else self._last_result.to_dict(),
                "last_error": self._last_error,
            }


BACKUP_RETENTION = BackupRetentionManager(SETTINGS.backup_dir, policy_from_settings())
