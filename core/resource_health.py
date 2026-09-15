"""Low-cost runtime resource accounting for server_health."""

from __future__ import annotations

import os
import sqlite3
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil

from core.config import SETTINGS
from core.executor import background_stats
from tools.filesystem.search_snapshots import SEARCH_SNAPSHOTS
from tools.project.index import PYTHON_METADATA_CACHE

_TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}


def _files_usage(root: Path, *, accept: Callable[[Path], bool] | None = None) -> tuple[int, int]:
    if not root.is_dir():
        return 0, 0
    count = 0
    total = 0
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0, 0
    for entry in entries:
        try:
            if entry.is_dir():
                nested_count, nested_bytes = _files_usage(entry, accept=accept)
                count += nested_count
                total += nested_bytes
            elif entry.is_file() and (accept is None or accept(entry)):
                count += 1
                total += entry.stat().st_size
        except OSError:
            continue
    return count, total


def _single_file_bytes(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _job_metrics() -> dict[str, int]:
    db_path = SETTINGS.state_dir / "jobs.sqlite3"
    output_dir = SETTINGS.state_dir / "jobs"
    counts: Counter[str] = Counter()
    terminal_ids: set[str] = set()
    if db_path.is_file():
        try:
            with sqlite3.connect(db_path, timeout=1.0) as db:
                db.execute("PRAGMA query_only=ON")
                for status, count in db.execute("SELECT status, count(*) FROM jobs GROUP BY status"):
                    counts[str(status)] = int(count)
                terminal_ids = {
                    str(row[0])
                    for row in db.execute(
                        "SELECT id FROM jobs WHERE status IN ('succeeded','failed','cancelled','timed_out','interrupted')"
                    )
                }
        except sqlite3.Error:
            counts["query_errors"] += 1

    output_files, output_bytes = _files_usage(output_dir)
    terminal_output_bytes = 0
    if output_dir.is_dir() and terminal_ids:
        for job_id in terminal_ids:
            _, size = _files_usage(output_dir / job_id)
            terminal_output_bytes += size

    db_bytes = sum(
        _single_file_bytes(path)
        for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm"))
    )
    return {
        "queued_jobs": counts["queued"],
        "running_jobs": counts["running"],
        "orphaned_jobs": counts["orphaned"],
        "active_jobs": counts["running"] + counts["orphaned"],
        "terminal_jobs": sum(counts[status] for status in _TERMINAL_JOB_STATUSES),
        "job_query_errors": counts["query_errors"],
        "job_output_files": output_files,
        "job_output_bytes": output_bytes,
        "job_history_output_bytes": terminal_output_bytes,
        "job_db_bytes": db_bytes,
        "job_storage_bytes": db_bytes + output_bytes,
    }


def _audit_usage() -> tuple[int, int]:
    state = SETTINGS.state_dir
    if not state.is_dir():
        return 0, 0
    files: list[Path] = []
    active = SETTINGS.audit_log
    if active.is_file():
        files.append(active)
    for index in range(1, SETTINGS.audit_keep_files + 1):
        candidate = state / f"audit.{index}.jsonl"
        if candidate.is_file():
            files.append(candidate)
    return len(files), sum(_single_file_bytes(path) for path in files)


def _ratio(value: int, maximum: int) -> float | None:
    if maximum <= 0:
        return None
    return round(value / maximum, 4)


def _budget(value: int, maximum: int, *, unit: str) -> dict[str, Any]:
    return {"value": value, "max": maximum, "unit": unit, "usage_ratio": _ratio(value, maximum)}


def collect_resource_metrics(browser: dict[str, int]) -> dict[str, Any]:
    """Collect bounded runtime/storage usage without mutating or evicting resources."""

    process = psutil.Process(os.getpid())
    ast_stats = PYTHON_METADATA_CACHE.stats()
    snapshot_stats = SEARCH_SNAPSHOTS.stats()
    background = background_stats()
    jobs = _job_metrics()
    artifact_count, artifact_bytes = _files_usage(
        SETTINGS.state_dir / "artifacts", accept=lambda path: path.suffix.casefold() == ".bin"
    )
    backup_count, backup_bytes = _files_usage(
        SETTINGS.backup_dir, accept=lambda path: path.suffix.casefold() == ".bak"
    )
    audit_files, audit_bytes = _audit_usage()

    audit_max_bytes = SETTINGS.audit_max_file_bytes * (SETTINGS.audit_keep_files + 1)
    budgets = {
        "ast_cache_entries": _budget(ast_stats["entries"], SETTINGS.ast_cache_max_files, unit="files"),
        "ast_cache_bytes": _budget(ast_stats["bytes"], SETTINGS.ast_cache_max_bytes, unit="bytes"),
        "search_snapshots": _budget(snapshot_stats["count"], SETTINGS.search_snapshot_max_count, unit="snapshots"),
        "search_snapshot_bytes": _budget(snapshot_stats["bytes"], SETTINGS.search_snapshot_max_bytes, unit="bytes"),
        "browser_sessions": _budget(
            browser["active_sessions"] + browser["pending_sessions"],
            SETTINGS.browser_max_sessions,
            unit="sessions",
        ),
        "browser_pools": _budget(browser["active_pools"], SETTINGS.browser_max_pools, unit="pools"),
        "running_jobs": _budget(jobs["active_jobs"], SETTINGS.max_running_jobs, unit="jobs"),
        "artifacts": _budget(artifact_count, SETTINGS.artifact_max_count, unit="files"),
        "artifact_bytes": _budget(artifact_bytes, SETTINGS.artifact_max_bytes, unit="bytes"),
        "backup_bytes": _budget(backup_bytes, SETTINGS.backup_max_bytes, unit="bytes"),
        "job_history": _budget(jobs["terminal_jobs"], SETTINGS.job_history_max_count, unit="jobs"),
        "job_history_bytes": _budget(jobs["job_history_output_bytes"], SETTINGS.job_history_max_bytes, unit="bytes"),
        "audit_bytes": _budget(audit_bytes, audit_max_bytes, unit="bytes"),
    }
    ratios = [entry["usage_ratio"] for entry in budgets.values() if entry["usage_ratio"] is not None]
    peak_ratio = max(ratios, default=0.0)
    pressure = "critical" if peak_ratio >= 1.0 else "warning" if peak_ratio >= 0.8 else "normal"

    return {
        "rss_mb": round(process.memory_info().rss / (1024 * 1024), 3),
        "ast_cache_entries": ast_stats["entries"],
        "ast_cache_bytes": ast_stats["bytes"],
        "ast_cache_estimated_bytes": ast_stats["bytes"],
        "cached_files": ast_stats["entries"],
        "search_snapshot_count": snapshot_stats["count"],
        "search_snapshot_bytes": snapshot_stats["bytes"],
        "active_browser_sessions": browser["active_sessions"],
        "pending_browser_sessions": browser["pending_sessions"],
        "active_browser_pools": browser["active_pools"],
        "active_browser_contexts": browser["active_contexts"],
        "active_background_processes": background["running"],
        "tracked_background_processes": background["tracked"],
        **jobs,
        "active_processes": background["running"] + jobs["active_jobs"],
        "artifact_count": artifact_count,
        "artifact_bytes": artifact_bytes,
        "artifact_storage_bytes": artifact_bytes,
        "backup_count": backup_count,
        "backup_bytes": backup_bytes,
        "audit_files": audit_files,
        "audit_bytes": audit_bytes,
        "resource_pressure": pressure,
        "budgets": budgets,
    }
