"""Durable command jobs with atomic request deduplication and explicit recovery."""

from __future__ import annotations

import codecs
import hashlib
import json
import math
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

import psutil

from core.artifacts import Delivery, deliver_file
from core.config import SETTINGS
from core.errors import ToolError
from core.resource_locks import RESOURCE_LOCKS

JOB_SCHEMA_VERSION = 3
JOB_STATUS_COLUMNS = (
    "id,status,created,updated,version,queue_deadline,worker_pid,worker_created,pid,pid_created,"
    "exit_code,cancel_requested,error"
)
JOB_MUTABLE_COLUMNS = {
    "status",
    "worker_pid",
    "worker_created",
    "pid",
    "pid_created",
    "exit_code",
    "cancel_requested",
    "error",
}
QUEUE_TIMEOUT_ERROR = "Queue deadline exceeded before execution started."
LAUNCH_RESERVATION_STALE_SEC = 10.0
ACTIVE_JOB_STATUSES = ("running", "orphaned")
ClaimResult = Literal["claimed", "wait", "terminal"]
MAX_JOB_WAIT_SEC = 300.0


def _job_wait_poll_interval(elapsed: float) -> float:
    """Poll quickly at first, then reduce SQLite read pressure for long waits."""
    if elapsed < 1.0:
        return 0.05
    if elapsed < 5.0:
        return 0.1
    if elapsed < 30.0:
        return 0.25
    return 0.5


def same_process(pid: int | None, created: float | None) -> bool:
    if not pid or created is None:
        return False
    try:
        process = psutil.Process(pid)
        return abs(process.create_time() - created) < 0.01 and process.is_running()
    except psutil.NoSuchProcess:
        return False


class JobStore:
    def __init__(self, path: Path | None = None, *, initialize: bool = True) -> None:
        self.path = path or SETTINGS.state_dir / "jobs.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.output_dir = self.path.parent / "jobs"
        self.output_dir.mkdir(exist_ok=True)
        if initialize:
            self._initialize_schema()

    def _initialize_schema(self) -> None:
        with closing(self.connect()) as db:
            db.execute("PRAGMA busy_timeout=10000")
            for attempt in range(50):
                try:
                    db.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).casefold() or attempt == 49:
                        raise
                    time.sleep(0.05)
            db.execute("BEGIN IMMEDIATE")
            try:
                current = int(db.execute("PRAGMA user_version").fetchone()[0])
                if current > JOB_SCHEMA_VERSION:
                    raise RuntimeError(
                        f"Job database schema {current} is newer than supported schema {JOB_SCHEMA_VERSION}."
                    )
                db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                    spec TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1, queue_deadline REAL,
                    launch_token TEXT, launch_started REAL,
                    worker_pid INTEGER, worker_created REAL, pid INTEGER, pid_created REAL,
                    exit_code INTEGER, cancel_requested INTEGER NOT NULL DEFAULT 0, error TEXT
                )""")
                columns = {str(row[1]) for row in db.execute("PRAGMA table_info(jobs)")}
                if "version" not in columns:
                    db.execute("ALTER TABLE jobs ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
                if "queue_deadline" not in columns:
                    db.execute("ALTER TABLE jobs ADD COLUMN queue_deadline REAL")
                if "launch_token" not in columns:
                    db.execute("ALTER TABLE jobs ADD COLUMN launch_token TEXT")
                if "launch_started" not in columns:
                    db.execute("ALTER TABLE jobs ADD COLUMN launch_started REAL")
                db.execute(f"PRAGMA user_version={JOB_SCHEMA_VERSION}")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def submit(
        self,
        command: list[str],
        cwd: Path,
        timeout_sec: float,
        request_key: str,
        encoding: str = "utf-8",
        *,
        queue_timeout_sec: float | None = None,
        _retention_retry: bool = False,
    ) -> dict[str, Any]:
        if not command or not command[0] or not cwd.is_dir():
            raise ValueError("An executable and existing working directory are required.")
        if timeout_sec <= 0:
            raise ValueError("Execution timeout must be positive.")
        if queue_timeout_sec is not None and queue_timeout_sec <= 0:
            raise ValueError("Queue timeout must be positive when provided.")
        codecs.lookup(encoding)
        spec_data: dict[str, Any] = {
            "command": command,
            "cwd": str(cwd.resolve()),
            "timeout_sec": timeout_sec,
            "encoding": encoding,
        }
        if queue_timeout_sec is not None:
            spec_data["queue_timeout_sec"] = queue_timeout_sec
        spec = json.dumps(spec_data, sort_keys=True)
        fingerprint = hashlib.sha256(spec.encode()).hexdigest()
        job_id = uuid.uuid4().hex
        now = time.time()
        queue_deadline = None if queue_timeout_sec is None else now + queue_timeout_sec
        with closing(self.connect()) as db, db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO jobs "
                "(id,request_key,fingerprint,spec,status,created,updated,version,queue_deadline) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (job_id, request_key, fingerprint, spec, "queued", now, now, 1, queue_deadline),
            ).rowcount
            existing = db.execute("SELECT id,fingerprint FROM jobs WHERE request_key=?", (request_key,)).fetchone()
        if not inserted:
            if existing["fingerprint"] != fingerprint:
                raise ToolError("idempotency_conflict", "This request key already belongs to a different command.")
            try:
                result = self.get(existing["id"])
            except ToolError as exc:
                if exc.code != "job_not_found" or _retention_retry:
                    raise
                # Terminal-history retention may delete the row after the
                # idempotency transaction commits but before the follow-up
                # status read. Retry once: if the history row is truly gone,
                # the same request key is now eligible to create a fresh job.
                return self.submit(
                    command,
                    cwd,
                    timeout_sec,
                    request_key,
                    encoding,
                    queue_timeout_sec=queue_timeout_sec,
                    _retention_retry=True,
                )
            if result["status"] == "queued":
                from core.job_scheduler import ensure_job_scheduler
                ensure_job_scheduler(self)
            return {"ok": True, "deduplicated": True, **result}
        directory = self.output_dir / job_id
        # A concurrent idempotent submit may already have kicked the scheduler,
        # which creates the output directory before this inserter reaches it.
        directory.mkdir(exist_ok=True)
        (directory / "stdout.bin").touch(exist_ok=True)
        (directory / "stderr.bin").touch(exist_ok=True)
        from core.job_scheduler import ensure_job_scheduler
        ensure_job_scheduler(self)
        return {"ok": True, "deduplicated": False, **self.get(job_id)}

    def update(self, job_id: str, **values: Any) -> None:
        if not values or not values.keys() <= JOB_MUTABLE_COLUMNS:
            raise ValueError("Unsupported job update.")
        values["updated"] = time.time()
        assignment = ",".join(f"{key}=?" for key in values)
        with closing(self.connect()) as db, db:
            db.execute(
                f"UPDATE jobs SET {assignment},version=version+1 WHERE id=?",
                (*values.values(), job_id),
            )

    def raw(self, job_id: str) -> dict[str, Any]:
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ToolError("job_not_found", "Unknown job_id.")
        return dict(row)

    def _status_raw(self, job_id: str) -> dict[str, Any]:
        with closing(self.connect()) as db:
            row = db.execute(f"SELECT {JOB_STATUS_COLUMNS} FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ToolError("job_not_found", "Unknown job_id.")
        return dict(row)

    def _reconcile(
        self,
        rows: list[dict[str, Any]],
        *,
        db: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """Check active processes outside the DB lock, then reconcile in one transaction."""
        now = time.time()
        changes: list[tuple[str, str | None, float, str, str]] = []
        for row in rows:
            if row["status"] == "running" and not same_process(row["worker_pid"], row["worker_created"]):
                status = "orphaned" if same_process(row["pid"], row["pid_created"]) else "interrupted"
                changes.append((status, None, now, row["id"], "running"))
            elif row["status"] == "orphaned" and not same_process(row["pid"], row["pid_created"]):
                changes.append(("interrupted", None, now, row["id"], "orphaned"))
            elif (
                row["status"] == "queued"
                and row["queue_deadline"] is not None
                and now >= float(row["queue_deadline"])
            ):
                changes.append(("timed_out", QUEUE_TIMEOUT_ERROR, now, row["id"], "queued"))
        if not changes:
            return rows

        def apply(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            with connection:
                connection.executemany(
                    "UPDATE jobs SET status=?,error=?,updated=?,version=version+1 WHERE id=? AND status=?",
                    changes,
                )
                ids = [change[3] for change in changes]
                refreshed: dict[str, dict[str, Any]] = {}
                for start in range(0, len(ids), 500):
                    batch = ids[start:start + 500]
                    placeholders = ",".join("?" for _ in batch)
                    refreshed.update({row["id"]: dict(row) for row in connection.execute(
                        f"SELECT {JOB_STATUS_COLUMNS} FROM jobs WHERE id IN ({placeholders})", batch,
                    )})
            return [refreshed.get(row["id"], row) for row in rows]

        if db is not None:
            return apply(db)
        with closing(self.connect()) as connection:
            return apply(connection)

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        return {"job_id": row["id"], **{key: row[key] for key in
                ("status", "created", "updated", "version", "queue_deadline", "pid", "exit_code", "cancel_requested", "error")}}

    def _reconcile_active_rows(self, db: sqlite3.Connection) -> None:
        rows = [dict(row) for row in db.execute(
            f"SELECT {JOB_STATUS_COLUMNS} FROM jobs WHERE status IN (?,?)",
            ACTIVE_JOB_STATUSES,
        )]
        self._reconcile(rows, db=db)

    def _recover_stale_launch_reservations(self, db: sqlite3.Connection) -> None:
        now = time.time()
        rows = [dict(row) for row in db.execute(
            "SELECT id,launch_token,launch_started,worker_pid,worker_created FROM jobs "
            "WHERE status='queued' AND launch_token IS NOT NULL"
        )]
        stale: list[tuple[float, str, str]] = []
        for row in rows:
            started = row["launch_started"]
            if same_process(row["worker_pid"], row["worker_created"]):
                continue
            if started is None or now - float(started) >= LAUNCH_RESERVATION_STALE_SEC:
                stale.append((now, row["id"], row["launch_token"]))
        if stale:
            with db:
                db.executemany(
                    "UPDATE jobs SET launch_token=NULL,launch_started=NULL,worker_pid=NULL,worker_created=NULL,"
                    "updated=?,version=version+1 WHERE id=? AND status='queued' AND launch_token=?",
                    stale,
                )

    def reserve_worker_launches(
        self,
        *,
        max_running: int,
        db: sqlite3.Connection | None = None,
    ) -> tuple[list[tuple[str, str]], int]:
        """Reserve queued jobs for worker launch without exceeding active capacity."""
        if max_running < 1:
            raise ValueError("max_running must be positive.")
        if db is None:
            with closing(self.connect()) as connection:
                return self.reserve_worker_launches(max_running=max_running, db=connection)

        self._recover_stale_launch_reservations(db)
        self._reconcile_active_rows(db)
        now = time.time()
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                "UPDATE jobs SET status='cancelled',launch_token=NULL,launch_started=NULL,updated=?,version=version+1 "
                "WHERE status='queued' AND cancel_requested=1",
                (now,),
            )
            db.execute(
                "UPDATE jobs SET status='timed_out',error=?,launch_token=NULL,launch_started=NULL,"
                "updated=?,version=version+1 WHERE status='queued' AND queue_deadline IS NOT NULL "
                "AND queue_deadline<=?",
                (QUEUE_TIMEOUT_ERROR, now, now),
            )
            active_count = int(db.execute(
                "SELECT count(*) FROM jobs WHERE status IN (?,?)",
                ACTIVE_JOB_STATUSES,
            ).fetchone()[0])
            reserved_count = int(db.execute(
                "SELECT count(*) FROM jobs WHERE status='queued' AND launch_token IS NOT NULL",
            ).fetchone()[0])
            available = max(max_running - active_count - reserved_count, 0)
            reservations: list[tuple[str, str]] = []
            if available:
                ids = [str(row[0]) for row in db.execute(
                    "SELECT id FROM jobs WHERE status='queued' AND launch_token IS NULL AND cancel_requested=0 "
                    "AND (queue_deadline IS NULL OR queue_deadline>?) ORDER BY created,id LIMIT ?",
                    (now, available),
                )]
                for job_id in ids:
                    token = uuid.uuid4().hex
                    claimed = db.execute(
                        "UPDATE jobs SET launch_token=?,launch_started=?,updated=?,version=version+1 "
                        "WHERE id=? AND status='queued' AND launch_token IS NULL AND cancel_requested=0 "
                        "AND (queue_deadline IS NULL OR queue_deadline>?)",
                        (token, now, now, job_id, now),
                    ).rowcount
                    if claimed:
                        reservations.append((job_id, token))
            queued_remaining = int(db.execute(
                "SELECT count(*) FROM jobs WHERE status='queued'",
            ).fetchone()[0])
            db.commit()
            return reservations, queued_remaining
        except BaseException:
            db.rollback()
            raise

    def record_worker_launch(
        self,
        job_id: str,
        launch_token: str,
        *,
        worker_pid: int,
        worker_created: float | None,
    ) -> None:
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE jobs SET worker_pid=?,worker_created=?,updated=?,version=version+1 "
                "WHERE id=? AND status='queued' AND launch_token=?",
                (worker_pid, worker_created, time.time(), job_id, launch_token),
            )

    def fail_worker_launch(self, job_id: str, launch_token: str, error: str) -> None:
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE jobs SET status='failed',error=?,launch_token=NULL,launch_started=NULL,updated=?,"
                "version=version+1 WHERE id=? AND status='queued' AND launch_token=?",
                (error, time.time(), job_id, launch_token),
            )

    def has_queued_jobs(self) -> bool:
        with closing(self.connect()) as db:
            return bool(db.execute("SELECT 1 FROM jobs WHERE status='queued' LIMIT 1").fetchone())

    def claim_for_execution(
        self,
        job_id: str,
        *,
        worker_pid: int,
        worker_created: float,
        max_running: int,
        launch_token: str | None = None,
        db: sqlite3.Connection | None = None,
    ) -> ClaimResult:
        """Atomically claim one queued job without exceeding active-job capacity."""
        if max_running < 1:
            raise ValueError("max_running must be positive.")
        if db is None:
            with closing(self.connect()) as connection:
                return self.claim_for_execution(
                    job_id,
                    worker_pid=worker_pid,
                    worker_created=worker_created,
                    max_running=max_running,
                    launch_token=launch_token,
                    db=connection,
                )
        self._reconcile_active_rows(db)
        now = time.time()
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute(
                "SELECT status,cancel_requested,queue_deadline,launch_token FROM jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ToolError("job_not_found", "Unknown job_id.")
            if row["status"] != "queued" or row["launch_token"] != launch_token:
                db.commit()
                return "terminal"
            if row["cancel_requested"]:
                db.execute(
                    "UPDATE jobs SET status='cancelled',launch_token=NULL,launch_started=NULL,"
                    "updated=?,version=version+1 WHERE id=? AND status='queued'",
                    (now, job_id),
                )
                db.commit()
                return "terminal"
            queue_deadline = row["queue_deadline"]
            if queue_deadline is not None and now >= float(queue_deadline):
                db.execute(
                    "UPDATE jobs SET status='timed_out',error=?,launch_token=NULL,launch_started=NULL,"
                    "updated=?,version=version+1 WHERE id=? AND status='queued'",
                    (QUEUE_TIMEOUT_ERROR, now, job_id),
                )
                db.commit()
                return "terminal"
            active_count = int(db.execute(
                "SELECT count(*) FROM jobs WHERE status IN (?,?)",
                ACTIVE_JOB_STATUSES,
            ).fetchone()[0])
            if active_count >= max_running:
                db.commit()
                return "wait"
            claim_time = time.time()
            claimed = db.execute(
                "UPDATE jobs SET status='running',launch_token=NULL,launch_started=NULL,worker_pid=?,"
                "worker_created=?,updated=?,version=version+1 WHERE id=? AND status='queued' "
                "AND launch_token IS ? AND cancel_requested=0 "
                "AND (queue_deadline IS NULL OR queue_deadline>?)",
                (worker_pid, worker_created, claim_time, job_id, launch_token, claim_time),
            ).rowcount
            if claimed:
                db.commit()
                return "claimed"
            expired = db.execute(
                "UPDATE jobs SET status='timed_out',error=?,launch_token=NULL,launch_started=NULL,"
                "updated=?,version=version+1 WHERE id=? AND status='queued' AND launch_token IS ? "
                "AND queue_deadline IS NOT NULL AND queue_deadline<=?",
                (QUEUE_TIMEOUT_ERROR, claim_time, job_id, launch_token, claim_time),
            ).rowcount
            db.commit()
            return "terminal" if expired else "wait"
        except BaseException:
            db.rollback()
            raise

    def get(self, job_id: str) -> dict[str, Any]:
        return self._public(self._reconcile([self._status_raw(job_id)])[0])

    def wait(self, job_id: str, after_version: int, timeout: float) -> dict[str, Any]:
        """Long-poll one job until its authoritative version advances or timeout expires."""
        if after_version < 0:
            raise ValueError("after_version must be non-negative.")
        if not math.isfinite(timeout) or timeout < 0 or timeout > MAX_JOB_WAIT_SEC:
            raise ValueError(f"timeout must be between 0 and {MAX_JOB_WAIT_SEC:g} seconds.")
        started = time.monotonic()
        deadline = started + timeout
        with closing(self.connect()) as db:
            while True:
                row = db.execute(
                    f"SELECT {JOB_STATUS_COLUMNS} FROM jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise ToolError("job_not_found", "Unknown job_id.")
                current = self._reconcile([dict(row)], db=db)[0]
                version = int(current["version"])
                if after_version > version:
                    raise ToolError(
                        "job_version_ahead",
                        f"after_version {after_version} is newer than current job version {version}.",
                        hint="Refresh job_status and retry with the returned version.",
                    )
                if version > after_version:
                    return {
                        "ok": True,
                        "changed": True,
                        "timed_out": False,
                        "after_version": after_version,
                        **self._public(current),
                    }
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {
                        "ok": True,
                        "changed": False,
                        "timed_out": True,
                        "after_version": after_version,
                        **self._public(current),
                    }
                elapsed = time.monotonic() - started
                time.sleep(min(_job_wait_poll_interval(elapsed), remaining))

    def list(self, offset: int, limit: int) -> dict[str, Any]:
        with closing(self.connect()) as db:
            total = db.execute("SELECT count(*) FROM jobs").fetchone()[0]
            rows = [dict(row) for row in db.execute(
                f"SELECT {JOB_STATUS_COLUMNS} FROM jobs ORDER BY created DESC, id DESC LIMIT ? OFFSET ?", (limit, offset),
            )]
        return {"ok": True, "items": [self._public(row) for row in self._reconcile(rows)], "total_count": total,
                "offset": offset, "truncated": offset + len(rows) < total}

    def cancel(self, job_id: str) -> dict[str, Any]:
        orphan: tuple[int | None, float | None] | None = None
        now = time.time()
        with closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT status,pid,pid_created FROM jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise ToolError("job_not_found", "Unknown job_id.")
                status = str(row["status"])
                if status == "queued":
                    db.execute(
                        "UPDATE jobs SET status='cancelled',cancel_requested=1,updated=?,version=version+1 "
                        "WHERE id=? AND status='queued'",
                        (now, job_id),
                    )
                elif status == "running":
                    db.execute(
                        "UPDATE jobs SET cancel_requested=1,updated=?,version=version+1 "
                        "WHERE id=? AND status='running'",
                        (now, job_id),
                    )
                elif status == "orphaned":
                    db.execute(
                        "UPDATE jobs SET cancel_requested=1,updated=?,version=version+1 "
                        "WHERE id=? AND status='orphaned'",
                        (now, job_id),
                    )
                    orphan = (row["pid"], row["pid_created"])
                db.commit()
            except BaseException:
                db.rollback()
                raise

        if orphan is not None:
            pid, created = orphan
            if same_process(pid, created):
                from core.executor import terminate_process_tree
                assert pid is not None
                terminate_process_tree(pid, force=True)
            with closing(self.connect()) as db, db:
                db.execute(
                    "UPDATE jobs SET status='cancelled',updated=?,version=version+1 "
                    "WHERE id=? AND status='orphaned'",
                    (time.time(), job_id),
                )
        return {"ok": True, **self.get(job_id)}

    def output(self, job_id: str, since_byte: int = 0, stderr_since_byte: int = 0,
               delivery: Delivery = "inline") -> dict[str, Any]:
        directory = self.output_dir / job_id
        stdout = directory / "stdout.bin"
        stderr = directory / "stderr.bin"
        # Retention takes the same locks before deleting a terminal row/output
        # pair, closing the row-read -> directory-delete race for job_output.
        with RESOURCE_LOCKS.sync(stdout, stderr):
            raw = self._reconcile([self.raw(job_id)])[0]
            row = self._public(raw)
            encoding = json.loads(raw["spec"])["encoding"]
            final = row["status"] not in {"queued", "running", "orphaned"}
            return {"ok": True, **row,
                    "stdout": deliver_file(stdout, encoding=encoding,
                                           offset=since_byte, delivery=delivery, final=final),
                    "stderr": deliver_file(stderr, encoding=encoding,
                                           offset=stderr_since_byte, delivery=delivery, final=final)}
