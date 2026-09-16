"""Bounded retention for terminal durable-job history and output."""

from __future__ import annotations

import re
import shutil
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path

from core.config import SETTINGS, Settings
from core.resource_locks import RESOURCE_LOCKS

TERMINAL_JOB_STATUSES = ("succeeded", "failed", "cancelled", "timed_out", "interrupted")
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True, slots=True)
class JobHistoryPolicy:
    max_age_sec: float
    max_count: int
    max_bytes: int
    cleanup_interval_sec: float
    orphan_grace_sec: float = 60.0


@dataclass(frozen=True, slots=True)
class JobHistoryEntry:
    job_id: str
    updated: float
    output_bytes: int


@dataclass(frozen=True, slots=True)
class JobHistoryCleanupResult:
    terminal_rows_scanned: int
    terminal_output_bytes: int
    removed_jobs: int
    removed_output_bytes: int
    removed_for_age: int
    removed_for_count: int
    removed_for_quota: int
    orphan_dirs_removed: int
    remaining_terminal_rows: int
    remaining_output_bytes: int
    quota_satisfied: bool
    safety_floor_preserved: bool
    errors: int

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)


def policy_from_settings(settings: Settings = SETTINGS) -> JobHistoryPolicy:
    return JobHistoryPolicy(
        max_age_sec=float(settings.job_history_max_age_days * 86_400),
        max_count=settings.job_history_max_count,
        max_bytes=settings.job_history_max_bytes,
        cleanup_interval_sec=float(settings.job_history_cleanup_interval_sec),
    )


def _directory_size(path: Path) -> tuple[int, int]:
    total = 0
    errors = 0
    try:
        items = list(path.iterdir())
    except FileNotFoundError:
        return 0, 0
    except OSError:
        return 0, 1
    for item in items:
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            errors += 1
    return total, errors


def _terminal_rows(db: sqlite3.Connection, output_dir: Path) -> tuple[list[JobHistoryEntry], int]:
    placeholders = ",".join("?" for _ in TERMINAL_JOB_STATUSES)
    rows = db.execute(
        f"SELECT id,updated FROM jobs WHERE status IN ({placeholders}) ORDER BY updated,id",
        TERMINAL_JOB_STATUSES,
    ).fetchall()
    entries: list[JobHistoryEntry] = []
    errors = 0
    for row in rows:
        size, size_errors = _directory_size(output_dir / str(row["id"]))
        errors += size_errors
        entries.append(JobHistoryEntry(str(row["id"]), float(row["updated"]), size))
    return entries, errors


def _select_removals(
    entries: list[JobHistoryEntry],
    policy: JobHistoryPolicy,
    now: float,
) -> tuple[dict[str, str], bool]:
    if not entries:
        return {}, False
    newest_id = entries[-1].job_id
    survivors = entries[:]
    reasons: dict[str, str] = {}

    cutoff = now - policy.max_age_sec if policy.max_age_sec > 0 else None
    if cutoff is not None:
        kept: list[JobHistoryEntry] = []
        for entry in survivors:
            if entry.job_id != newest_id and entry.updated < cutoff:
                reasons[entry.job_id] = "age"
            else:
                kept.append(entry)
        survivors = kept

    if policy.max_count > 0:
        while len(survivors) > policy.max_count:
            removable = next((entry for entry in survivors if entry.job_id != newest_id), None)
            if removable is None:
                break
            reasons[removable.job_id] = "count"
            survivors.remove(removable)

    if policy.max_bytes > 0:
        total = sum(entry.output_bytes for entry in survivors)
        while total > policy.max_bytes:
            removable = next((entry for entry in survivors if entry.job_id != newest_id), None)
            if removable is None:
                break
            reasons[removable.job_id] = "quota"
            total -= removable.output_bytes
            survivors.remove(removable)

    return reasons, bool(survivors)


def _delete_terminal_batch(
    db_path: Path,
    output_dir: Path,
    entries: list[JobHistoryEntry],
) -> tuple[list[JobHistoryEntry], int, int]:
    if not entries:
        return [], 0, 0
    paths: list[Path] = []
    for entry in entries:
        directory = output_dir / entry.job_id
        paths.extend((directory / "stdout.bin", directory / "stderr.bin"))
    with RESOURCE_LOCKS.sync(*paths):
        deleted_entries: list[JobHistoryEntry] = []
        with closing(sqlite3.connect(db_path, timeout=10)) as db:
            placeholders = ",".join("?" for _ in TERMINAL_JOB_STATUSES)
            db.execute("BEGIN IMMEDIATE")
            try:
                for entry in entries:
                    deleted = db.execute(
                        f"DELETE FROM jobs WHERE id=? AND updated<=? AND status IN ({placeholders})",
                        (entry.job_id, entry.updated, *TERMINAL_JOB_STATUSES),
                    ).rowcount
                    if deleted:
                        deleted_entries.append(entry)
                db.commit()
            except BaseException:
                db.rollback()
                raise
        removed_output_bytes = 0
        errors = 0
        for entry in deleted_entries:
            directory = output_dir / entry.job_id
            try:
                shutil.rmtree(directory)
            except FileNotFoundError:
                removed_output_bytes += entry.output_bytes
            except OSError:
                errors += 1
            else:
                removed_output_bytes += entry.output_bytes
        return deleted_entries, removed_output_bytes, errors


def _cleanup_orphan_dirs(db_path: Path, output_dir: Path, *, now: float, grace_sec: float) -> tuple[int, int]:
    removed = 0
    errors = 0
    try:
        directories = list(output_dir.iterdir())
    except FileNotFoundError:
        return 0, 0
    except OSError:
        return 0, 1
    for directory in directories:
        if not directory.is_dir() or not _JOB_ID.fullmatch(directory.name):
            continue
        try:
            if now - directory.stat().st_mtime < grace_sec:
                continue
        except OSError:
            errors += 1
            continue
        with closing(sqlite3.connect(db_path, timeout=10)) as db:
            exists = db.execute("SELECT 1 FROM jobs WHERE id=?", (directory.name,)).fetchone()
        if exists is not None:
            continue
        try:
            shutil.rmtree(directory)
        except FileNotFoundError:
            continue
        except OSError:
            errors += 1
        else:
            removed += 1
    return removed, errors


def cleanup_job_history(
    db_path: Path,
    output_dir: Path,
    policy: JobHistoryPolicy,
    *,
    now: float | None = None,
) -> JobHistoryCleanupResult:
    """Prune terminal rows first, then their output; orphan directories are recovered later."""

    if not db_path.is_file():
        return JobHistoryCleanupResult(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, True, False, 0)
    current = time.time() if now is None else now
    with closing(sqlite3.connect(db_path, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        entries, errors = _terminal_rows(db, output_dir)
    scanned_bytes = sum(entry.output_bytes for entry in entries)
    reasons, safety_floor = _select_removals(entries, policy, current)
    by_id = {entry.job_id: entry for entry in entries}
    removed_jobs = 0
    removed_output_bytes = 0
    reason_counts = {"age": 0, "count": 0, "quota": 0}
    selected = [entry for entry in entries if entry.job_id in reasons]
    for start in range(0, len(selected), 100):
        batch = selected[start:start + 100]
        try:
            deleted_entries, output_bytes, delete_errors = _delete_terminal_batch(db_path, output_dir, batch)
        except (OSError, sqlite3.Error):
            errors += 1
            continue
        errors += delete_errors
        removed_jobs += len(deleted_entries)
        removed_output_bytes += output_bytes
        for entry in deleted_entries:
            reason_counts[reasons[entry.job_id]] += 1

    orphan_removed, orphan_errors = _cleanup_orphan_dirs(
        db_path,
        output_dir,
        now=current,
        grace_sec=policy.orphan_grace_sec,
    )
    errors += orphan_errors
    with closing(sqlite3.connect(db_path, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        remaining, remaining_errors = _terminal_rows(db, output_dir)
    errors += remaining_errors
    remaining_bytes = sum(entry.output_bytes for entry in remaining)
    quota_satisfied = policy.max_bytes <= 0 or remaining_bytes <= policy.max_bytes
    del by_id
    return JobHistoryCleanupResult(
        len(entries),
        scanned_bytes,
        removed_jobs,
        removed_output_bytes,
        reason_counts["age"],
        reason_counts["count"],
        reason_counts["quota"],
        orphan_removed,
        len(remaining),
        remaining_bytes,
        quota_satisfied,
        safety_floor and bool(remaining),
        errors,
    )


class JobHistoryRetentionManager:
    def __init__(self, db_path: Path, output_dir: Path, policy: JobHistoryPolicy) -> None:
        self.db_path = db_path
        self.output_dir = output_dir
        self.policy = policy
        self._condition = threading.Condition()
        self._dirty = False
        self._running = False
        self._last_started = 0.0
        self._last_result: JobHistoryCleanupResult | None = None
        self._last_error: str | None = None

    def schedule(self) -> None:
        with self._condition:
            self._dirty = True
            if self._running:
                self._condition.notify_all()
                return
            self._running = True
            threading.Thread(target=self._run, name="job-history-retention", daemon=True).start()

    def _run(self) -> None:
        while True:
            with self._condition:
                if not self._dirty:
                    self._running = False
                    self._condition.notify_all()
                    return
                self._dirty = False
                wait_for = max(0.0, self.policy.cleanup_interval_sec - (time.monotonic() - self._last_started))
            if wait_for:
                time.sleep(wait_for)
            self._last_started = time.monotonic()
            try:
                result = cleanup_job_history(self.db_path, self.output_dir, self.policy)
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


_MANAGERS_LOCK = threading.Lock()
_MANAGERS: dict[tuple[str, str, JobHistoryPolicy], JobHistoryRetentionManager] = {}


def schedule_job_history_retention(
    db_path: Path | None = None,
    output_dir: Path | None = None,
    *,
    settings: Settings = SETTINGS,
) -> None:
    database = db_path or settings.state_dir / "jobs.sqlite3"
    outputs = output_dir or database.parent / "jobs"
    policy = policy_from_settings(settings)
    key = (str(database.resolve(strict=False)).casefold(), str(outputs.resolve(strict=False)).casefold(), policy)
    with _MANAGERS_LOCK:
        manager = _MANAGERS.get(key)
        if manager is None:
            manager = JobHistoryRetentionManager(database, outputs, policy)
            _MANAGERS[key] = manager
    manager.schedule()


def flush_job_history_retention(timeout: float = 5.0) -> bool:
    with _MANAGERS_LOCK:
        managers = list(_MANAGERS.values())
    deadline = time.monotonic() + timeout
    for manager in managers:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not manager.flush(remaining):
            return False
    return True
