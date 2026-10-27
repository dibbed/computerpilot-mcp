"""Bounded backup retention with coalesced background cleanup."""

from __future__ import annotations

import os
import stat
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.config import SETTINGS, Settings
from core.errors import ToolError
from core.file_lock import exclusive_file_lock
from core.state_paths import unlinked_directory_tree

BACKUP_RECENT_GRACE_SEC = 60.0
_MAX_PINNED_BACKUPS = 10_000
_COORDINATION_TIMEOUT_SEC = 30.0


@dataclass(frozen=True, slots=True)
class BackupPolicy:
    """Retention limits for recoverable filesystem backups."""

    max_bytes: int
    max_age_sec: float
    cleanup_interval_sec: float
    recent_grace_sec: float = BACKUP_RECENT_GRACE_SEC
    max_count: int = 0


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
    removed_for_count: int = 0

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)


def policy_from_settings(settings: Settings = SETTINGS) -> BackupPolicy:
    return BackupPolicy(
        max_bytes=settings.backup_max_bytes,
        max_age_sec=float(settings.backup_max_age_days * 86_400),
        cleanup_interval_sec=float(settings.backup_cleanup_interval_sec),
        max_count=settings.backup_max_count,
    )


def backup_created_at(path: Path, fallback_mtime: float) -> float:
    """Use the backup-event timestamp encoded in the filename, falling back for legacy names."""

    stamp = path.name.split("_", 1)[0]
    if len(stamp) < 17 or stamp[8] != "T" or not stamp.endswith("Z"):
        return fallback_mtime
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
            file_stat = path.lstat()
            if not stat.S_ISREG(file_stat.st_mode) or (
                os.name == "nt" and int(getattr(file_stat, "st_file_attributes", 0)) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                continue
        except FileNotFoundError:
            continue
        except OSError:
            errors += 1
            continue
        entries.append(
            BackupEntry(
                path=path,
                size=file_stat.st_size,
                created_at=backup_created_at(path, file_stat.st_mtime),
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
    """Apply age/count/byte quotas while preserving at least the newest backup."""

    if not unlinked_directory_tree(directory, allow_missing=True):
        return BackupCleanupResult(0, 0, 0, 0, 0, 0, 0, 0, True, False, 1)
    directory.mkdir(parents=True, exist_ok=True)
    if not unlinked_directory_tree(directory):
        return BackupCleanupResult(0, 0, 0, 0, 0, 0, 0, 0, True, False, 1)
    entries, errors = _scan_backups(directory)
    scanned_files = len(entries)
    scanned_bytes = sum(entry.size for entry in entries)
    if not entries:
        return BackupCleanupResult(0, 0, 0, 0, 0, 0, 0, 0, True, False, errors)

    current = time.time() if now is None else now
    protected = {path.resolve(strict=False) for path in (protected_paths or set())}
    newest = entries[-1].path.resolve(strict=False)
    protected.add(newest)
    if policy.recent_grace_sec > 0:
        grace_cutoff = current - policy.recent_grace_sec
        protected.update(
            entry.path.resolve(strict=False)
            for entry in entries
            if entry.created_at >= grace_cutoff
        )
    safety_floor_preserved = True

    survivors: list[BackupEntry] = []
    removed_files = 0
    removed_bytes = 0
    removed_for_age = 0
    removed_for_quota = 0
    removed_for_count = 0

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
    remaining_files = len(survivors)
    if (policy.max_bytes > 0 and remaining_bytes > policy.max_bytes) or (
        policy.max_count > 0 and remaining_files > policy.max_count
    ):
        kept: list[BackupEntry] = []
        for entry in survivors:
            resolved = entry.path.resolve(strict=False)
            over_bytes = policy.max_bytes > 0 and remaining_bytes > policy.max_bytes
            over_count = policy.max_count > 0 and remaining_files > policy.max_count
            if not (over_bytes or over_count) or resolved in protected:
                kept.append(entry)
                continue
            try:
                entry.path.unlink(missing_ok=True)
            except OSError:
                errors += 1
                kept.append(entry)
                continue
            remaining_bytes -= entry.size
            remaining_files -= 1
            removed_files += 1
            removed_bytes += entry.size
            if over_bytes:
                removed_for_quota += 1
            else:
                removed_for_count += 1
        survivors = kept

    # Exclude confirmed deletions, but retain scanned usage when a survivor
    # cannot be inspected so an I/O failure cannot hide quota pressure.
    remaining_files = 0
    actual_remaining_bytes = 0
    for entry in survivors:
        try:
            file_info = entry.path.stat()
        except FileNotFoundError:
            continue
        except OSError:
            errors += 1
            actual_remaining_bytes += entry.size
        else:
            actual_remaining_bytes += file_info.st_size
        remaining_files += 1
    quota_satisfied = (policy.max_bytes <= 0 or actual_remaining_bytes <= policy.max_bytes) and (
        policy.max_count <= 0 or remaining_files <= policy.max_count
    )

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
        removed_for_count=removed_for_count,
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
        self._cleanup_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._dirty = False
        self._protected: set[Path] = set()
        self._pins: dict[Path, int] = {}
        self._last_run = 0.0
        self._last_result: BackupCleanupResult | None = None
        self._last_error: str | None = None

    @contextmanager
    def pin_paths(self, paths: tuple[Path, ...] | list[Path]) -> Iterator[None]:
        """Keep reserved backup paths alive until their durable checkpoint is published."""

        if not unlinked_directory_tree(self.directory, allow_missing=True):
            raise ToolError("backup_path_invalid", "The backup directory must not contain links.")
        root = self.directory.resolve(strict=False)
        normalized: list[Path] = []
        seen: set[Path] = set()
        for path in paths:
            original = Path(path)
            if original.is_symlink():
                raise ToolError("backup_path_invalid", "Pinned backup paths must not be symlinks.")
            candidate = original.resolve(strict=False)
            if candidate.parent != root:
                raise ToolError("backup_path_invalid", "Pinned backups must be direct children of the backup directory.")
            if candidate not in seen:
                normalized.append(candidate)
                seen.add(candidate)
        with exclusive_file_lock(root.parent / f".{root.name}.retention.lock", timeout_sec=_COORDINATION_TIMEOUT_SEC,
                                 label="backup retention"):
            with self._cleanup_lock, self._lock:
                new_paths = [path for path in normalized if path not in self._pins]
                if len(self._pins) + len(new_paths) > _MAX_PINNED_BACKUPS:
                    raise ToolError("backup_pin_limit", "Too many backups are awaiting publication.")
                for path in normalized:
                    self._pins[path] = self._pins.get(path, 0) + 1
            try:
                yield
            finally:
                with self._cleanup_lock, self._lock:
                    for path in normalized:
                        remaining = self._pins.get(path, 0) - 1
                        if remaining <= 0:
                            self._pins.pop(path, None)
                        else:
                            self._pins[path] = remaining

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
                delay = max(0.0, self.policy.cleanup_interval_sec - (time.monotonic() - self._last_run))
            if delay:
                time.sleep(delay)
            # Coordinate publication and journal snapshots across processes.
            try:
                if not unlinked_directory_tree(self.directory, allow_missing=True):
                    raise ToolError("backup_path_invalid", "The backup directory must not contain links.")
                root = self.directory.resolve(strict=False)
                with exclusive_file_lock(root.parent / f".{root.name}.retention.lock", timeout_sec=_COORDINATION_TIMEOUT_SEC,
                                         label="backup retention"), self._cleanup_lock:
                    with self._lock:
                        protected |= self._protected
                        self._protected.clear()
                        pinned = set(self._pins)
                    try:
                        from core.recovery import OPERATION_RECOVERY

                        journal_path = root.parent / "operation-recovery.jsonl"
                        journal_protected = OPERATION_RECOVERY.protected_workspace_backups(expected_journal=journal_path)
                    except Exception as exc:  # never delete while unresolved recovery evidence is unreadable
                        with self._lock:
                            self._last_error = f"recovery_unavailable: {type(exc).__name__}"
                            self._last_run = time.monotonic()
                        continue
                    result = cleanup_backups(
                        self.directory,
                        self.policy,
                        protected_paths=protected | pinned | journal_protected,
                    )
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
                "pinned": len(self._pins),
            }


BACKUP_RETENTION = BackupRetentionManager(SETTINGS.backup_dir, policy_from_settings())
