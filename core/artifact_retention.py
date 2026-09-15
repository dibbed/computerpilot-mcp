"""Bounded retention for disk-backed output artifacts."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from core.config import SETTINGS, Settings


@dataclass(frozen=True, slots=True)
class ArtifactPolicy:
    max_bytes: int
    max_age_sec: float
    max_count: int
    cleanup_interval_sec: float


@dataclass(frozen=True, slots=True)
class ArtifactEntry:
    path: Path
    size: int
    mtime: float


@dataclass(frozen=True, slots=True)
class ArtifactCleanupResult:
    scanned_files: int
    scanned_bytes: int
    removed_files: int
    removed_bytes: int
    removed_for_age: int
    removed_for_count: int
    removed_for_quota: int
    remaining_files: int
    remaining_bytes: int
    quota_satisfied: bool
    safety_floor_preserved: bool
    errors: int

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)


def policy_from_settings(settings: Settings = SETTINGS) -> ArtifactPolicy:
    return ArtifactPolicy(
        max_bytes=settings.artifact_max_bytes,
        max_age_sec=float(settings.artifact_max_age_hours * 3_600),
        max_count=settings.artifact_max_count,
        cleanup_interval_sec=float(settings.artifact_cleanup_interval_sec),
    )


def _scan(directory: Path) -> tuple[list[ArtifactEntry], int]:
    entries: list[ArtifactEntry] = []
    errors = 0
    try:
        candidates = list(directory.glob("*.bin"))
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
        entries.append(ArtifactEntry(path=path, size=stat.st_size, mtime=stat.st_mtime))
    entries.sort(key=lambda entry: (entry.mtime, entry.path.name.casefold()))
    return entries, errors


def cleanup_artifacts(
    directory: Path,
    policy: ArtifactPolicy,
    *,
    now: float | None = None,
    protected_paths: set[Path] | None = None,
) -> ArtifactCleanupResult:
    """Apply age/count/byte limits while preserving the newest artifact."""

    directory.mkdir(parents=True, exist_ok=True)
    entries, errors = _scan(directory)
    scanned_files = len(entries)
    scanned_bytes = sum(entry.size for entry in entries)
    if not entries:
        return ArtifactCleanupResult(0, 0, 0, 0, 0, 0, 0, 0, 0, True, False, errors)

    current = time.time() if now is None else now
    protected = {path.resolve(strict=False) for path in (protected_paths or set())}
    protected.add(entries[-1].path.resolve(strict=False))
    survivors = entries[:]
    removed_files = 0
    removed_bytes = 0
    removed_for_age = 0
    removed_for_count = 0
    removed_for_quota = 0

    def remove(entry: ArtifactEntry) -> bool:
        nonlocal errors, removed_files, removed_bytes
        try:
            entry.path.unlink(missing_ok=True)
        except OSError:
            errors += 1
            return False
        removed_files += 1
        removed_bytes += entry.size
        return True

    cutoff = current - policy.max_age_sec if policy.max_age_sec > 0 else None
    if cutoff is not None:
        kept: list[ArtifactEntry] = []
        for entry in survivors:
            if entry.mtime < cutoff and entry.path.resolve(strict=False) not in protected and remove(entry):
                removed_for_age += 1
            else:
                kept.append(entry)
        survivors = kept

    if policy.max_count > 0:
        index = 0
        while len(survivors) > policy.max_count and index < len(survivors):
            entry = survivors[index]
            if entry.path.resolve(strict=False) in protected:
                index += 1
                continue
            if remove(entry):
                removed_for_count += 1
                survivors.pop(index)
            else:
                index += 1

    remaining_bytes = sum(entry.size for entry in survivors)
    if policy.max_bytes > 0:
        index = 0
        while remaining_bytes > policy.max_bytes and index < len(survivors):
            entry = survivors[index]
            if entry.path.resolve(strict=False) in protected:
                index += 1
                continue
            if remove(entry):
                removed_for_quota += 1
                remaining_bytes -= entry.size
                survivors.pop(index)
            else:
                index += 1

    remaining_bytes = sum(entry.size for entry in survivors)
    quota_satisfied = policy.max_bytes <= 0 or remaining_bytes <= policy.max_bytes
    safety_floor_preserved = bool(survivors)
    return ArtifactCleanupResult(
        scanned_files,
        scanned_bytes,
        removed_files,
        removed_bytes,
        removed_for_age,
        removed_for_count,
        removed_for_quota,
        len(survivors),
        remaining_bytes,
        quota_satisfied,
        safety_floor_preserved,
        errors,
    )


class ArtifactRetentionManager:
    """Coalesce cleanup requests onto one daemon thread."""

    def __init__(self, directory: Path, policy: ArtifactPolicy | None = None) -> None:
        self.directory = directory
        self.policy = policy or policy_from_settings()
        self._condition = threading.Condition()
        self._dirty = False
        self._running = False
        self._protected: set[Path] = set()
        self._last_started = 0.0
        self._last_result: ArtifactCleanupResult | None = None
        self._last_error: str | None = None

    def schedule(self, protected_path: Path | None = None) -> None:
        with self._condition:
            if protected_path is not None:
                self._protected.add(protected_path.resolve(strict=False))
            self._dirty = True
            if self._running:
                self._condition.notify_all()
                return
            self._running = True
            thread = threading.Thread(target=self._run, name="artifact-retention", daemon=True)
            thread.start()

    def _run(self) -> None:
        while True:
            with self._condition:
                if not self._dirty:
                    self._running = False
                    self._condition.notify_all()
                    return
                self._dirty = False
                protected = set(self._protected)
                self._protected.clear()
                wait_for = max(0.0, self.policy.cleanup_interval_sec - (time.monotonic() - self._last_started))
            if wait_for:
                time.sleep(wait_for)
            self._last_started = time.monotonic()
            try:
                result = cleanup_artifacts(self.directory, self.policy, protected_paths=protected)
            except BaseException as exc:
                with self._condition:
                    self._last_error = str(exc)
            else:
                with self._condition:
                    self._last_result = result
                    self._last_error = None

    def flush(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._running:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def snapshot(self) -> dict[str, object]:
        with self._condition:
            return {
                "running": self._running,
                "dirty": self._dirty,
                "last_result": self._last_result.to_dict() if self._last_result else None,
                "last_error": self._last_error,
            }


_MANAGERS_LOCK = threading.Lock()
_MANAGERS: dict[tuple[str, ArtifactPolicy], ArtifactRetentionManager] = {}


def _manager(directory: Path, policy: ArtifactPolicy) -> ArtifactRetentionManager:
    key = (str(directory.resolve(strict=False)).casefold(), policy)
    with _MANAGERS_LOCK:
        manager = _MANAGERS.get(key)
        if manager is None:
            manager = ArtifactRetentionManager(directory, policy)
            _MANAGERS[key] = manager
        return manager


def schedule_artifact_retention(path: Path | None = None, *, settings: Settings = SETTINGS) -> None:
    directory = path.parent if path is not None else settings.state_dir / "artifacts"
    _manager(directory, policy_from_settings(settings)).schedule(path)


def flush_artifact_retention(timeout: float = 5.0) -> bool:
    with _MANAGERS_LOCK:
        managers = list(_MANAGERS.values())
    deadline = time.monotonic() + timeout
    for manager in managers:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not manager.flush(remaining):
            return False
    return True
