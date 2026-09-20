"""Bounded retention for terminal durable workflow history."""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.config import SETTINGS, Settings

_TERMINAL_WORKFLOW_STATES = ("completed", "cancelled")
_UNRESOLVED_OPERATION_STATES = ("uncertain", "reconciling", "unresolvable")


@dataclass(frozen=True, slots=True)
class WorkflowHistoryPolicy:
    max_age_days: int
    max_count: int
    cleanup_interval_sec: float


@dataclass(frozen=True, slots=True)
class WorkflowHistoryCleanupResult:
    eligible_rows: int
    removed_rows: int
    removed_for_age: int
    removed_for_count: int
    remaining_eligible_rows: int
    protected_unresolved_rows: int
    errors: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def policy_from_settings(settings: Settings = SETTINGS) -> WorkflowHistoryPolicy:
    return WorkflowHistoryPolicy(
        max_age_days=settings.workflow_history_max_age_days,
        max_count=settings.workflow_history_max_count,
        cleanup_interval_sec=float(settings.workflow_cleanup_interval_sec),
    )


def cleanup_workflow_history(
    db_path: Path,
    policy: WorkflowHistoryPolicy,
    *,
    now: datetime | None = None,
) -> WorkflowHistoryCleanupResult:
    if not db_path.is_file():
        return WorkflowHistoryCleanupResult(0, 0, 0, 0, 0, 0, 0)
    current = now or datetime.now(timezone.utc)
    cutoff = (
        (current - timedelta(days=policy.max_age_days)).isoformat(timespec="milliseconds")
        if policy.max_age_days > 0
        else None
    )
    errors = 0
    removed_age = 0
    removed_count = 0
    with closing(sqlite3.connect(db_path, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        protected = int(
            db.execute(
                """
                SELECT COUNT(DISTINCT w.workflow_id)
                FROM workflows w
                JOIN workflow_operations o ON o.workflow_id = w.workflow_id
                WHERE w.state IN ('completed','cancelled')
                  AND o.state IN ('uncertain','reconciling','unresolvable')
                """
            ).fetchone()[0]
        )
        rows = db.execute(
            """
            SELECT w.workflow_id, w.updated_at
            FROM workflows w
            WHERE w.state IN ('completed','cancelled')
              AND NOT EXISTS (
                  SELECT 1 FROM workflow_operations o
                  WHERE o.workflow_id = w.workflow_id
                    AND o.state IN ('uncertain','reconciling','unresolvable')
              )
              AND NOT EXISTS (
                  SELECT 1 FROM workflow_leases l
                  WHERE l.workflow_id = w.workflow_id
                    AND l.expires_at > ?
              )
            ORDER BY w.updated_at ASC, w.workflow_id ASC
            """,
            (current.isoformat(timespec="milliseconds"),),
        ).fetchall()
        eligible = [(str(row["workflow_id"]), str(row["updated_at"])) for row in rows]
        reasons: dict[str, str] = {}
        if cutoff is not None:
            for workflow_id, updated_at in eligible:
                if updated_at < cutoff:
                    reasons[workflow_id] = "age"
        survivors = [item for item in eligible if item[0] not in reasons]
        if policy.max_count > 0 and len(survivors) > policy.max_count:
            excess = len(survivors) - policy.max_count
            for workflow_id, _ in survivors[:excess]:
                reasons[workflow_id] = "count"
        if reasons:
            db.execute("BEGIN IMMEDIATE")
            try:
                for workflow_id, reason in reasons.items():
                    deleted = db.execute(
                        """
                        DELETE FROM workflows
                        WHERE workflow_id = ?
                          AND state IN ('completed','cancelled')
                          AND NOT EXISTS (
                              SELECT 1 FROM workflow_operations o
                              WHERE o.workflow_id = workflows.workflow_id
                                AND o.state IN ('uncertain','reconciling','unresolvable')
                          )
                        """,
                        (workflow_id,),
                    ).rowcount
                    if deleted:
                        if reason == "age":
                            removed_age += 1
                        else:
                            removed_count += 1
                db.commit()
            except (sqlite3.Error, OSError):
                db.rollback()
                errors += 1
        remaining = int(
            db.execute(
                """
                SELECT COUNT(*) FROM workflows w
                WHERE w.state IN ('completed','cancelled')
                  AND NOT EXISTS (
                      SELECT 1 FROM workflow_operations o
                      WHERE o.workflow_id = w.workflow_id
                        AND o.state IN ('uncertain','reconciling','unresolvable')
                  )
                """
            ).fetchone()[0]
        )
    removed = removed_age + removed_count
    return WorkflowHistoryCleanupResult(
        eligible_rows=len(eligible),
        removed_rows=removed,
        removed_for_age=removed_age,
        removed_for_count=removed_count,
        remaining_eligible_rows=remaining,
        protected_unresolved_rows=protected,
        errors=errors,
    )


class WorkflowHistoryRetentionManager:
    def __init__(self, db_path: Path, policy: WorkflowHistoryPolicy) -> None:
        self.db_path = db_path
        self.policy = policy
        self._condition = threading.Condition()
        self._dirty = False
        self._running = False
        self._last_started = 0.0
        self._last_result: WorkflowHistoryCleanupResult | None = None
        self._last_error: str | None = None

    def schedule(self) -> None:
        with self._condition:
            self._dirty = True
            if self._running:
                self._condition.notify_all()
                return
            self._running = True
            threading.Thread(target=self._run, name="workflow-history-retention", daemon=True).start()

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
                result = cleanup_workflow_history(self.db_path, self.policy)
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
_MANAGERS: dict[tuple[str, WorkflowHistoryPolicy], WorkflowHistoryRetentionManager] = {}


def schedule_workflow_history_retention(
    db_path: Path | None = None,
    *,
    settings: Settings = SETTINGS,
) -> None:
    database = db_path or settings.workflow_db
    policy = policy_from_settings(settings)
    key = (str(database.resolve(strict=False)).casefold(), policy)
    with _MANAGERS_LOCK:
        manager = _MANAGERS.get(key)
        if manager is None:
            manager = WorkflowHistoryRetentionManager(database, policy)
            _MANAGERS[key] = manager
    manager.schedule()


def flush_workflow_history_retention(timeout: float = 5.0) -> bool:
    with _MANAGERS_LOCK:
        managers = list(_MANAGERS.values())
    deadline = time.monotonic() + timeout
    for manager in managers:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not manager.flush(remaining):
            return False
    return True
