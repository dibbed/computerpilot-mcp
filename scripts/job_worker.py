"""One durable job worker; never replays an already claimed job."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import psutil

from core.audit import audit_action
from core.executor import _creation_flags, terminate_process_tree
from core.jobs import JobStore


def _poll_interval(elapsed: float) -> float:
    """Poll cancellation frequently at first, then back off for long jobs."""
    if elapsed < 2.0:
        return 0.2
    if elapsed < 30.0:
        return 0.5
    return 1.0


def _row(db: sqlite3.Connection, job_id: str) -> dict[str, Any]:
    row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise RuntimeError("Durable job disappeared while its worker was running.")
    return dict(row)


def _update(db: sqlite3.Connection, job_id: str, **values: Any) -> None:
    values["updated"] = time.time()
    assignment = ",".join(f"{key}=?" for key in values)
    with db:
        db.execute(f"UPDATE jobs SET {assignment} WHERE id=?", (*values.values(), job_id))


def run(path: Path, job_id: str) -> None:
    store = JobStore(path, initialize=False)
    process: subprocess.Popen[bytes] | None = None
    # The worker owns one SQLite connection for its lifetime. Short transactions
    # keep WAL readers/writers independent while avoiding a connection per poll.
    with closing(store.connect()) as db:
        with db:
            claimed = db.execute(
                "UPDATE jobs SET status='running',worker_pid=?,worker_created=?,updated=? WHERE id=? AND status='queued'",
                (os.getpid(), psutil.Process().create_time(), time.time(), job_id),
            ).rowcount
        if not claimed:
            return
        try:
            row = _row(db, job_id)
            if row["cancel_requested"]:
                _update(db, job_id, status="cancelled")
                return
            spec = json.loads(row["spec"])
            directory = store.output_dir / job_id
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = spec["encoding"]
            with (directory / "stdout.bin").open("ab") as stdout, (directory / "stderr.bin").open("ab") as stderr:
                process = subprocess.Popen(
                    spec["command"],
                    cwd=spec["cwd"],
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    creationflags=_creation_flags(),
                )
                try:
                    created = psutil.Process(process.pid).create_time()
                except psutil.NoSuchProcess:
                    created = None
                _update(db, job_id, pid=process.pid, pid_created=created)
                started = time.monotonic()
                deadline = started + spec["timeout_sec"]
                status = "succeeded"
                code: int | None = None
                while code is None:
                    now = time.monotonic()
                    row = db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
                    if row is None:
                        raise RuntimeError("Durable job disappeared while its worker was running.")
                    cancelled = bool(row[0])
                    if cancelled or now >= deadline:
                        status = "cancelled" if cancelled else "timed_out"
                        audit_action(
                            "job_terminate_process_tree",
                            target=str(process.pid),
                            details={"job_id": job_id, "reason": status},
                        )
                        terminate_process_tree(process.pid, force=True)
                        code = process.wait(timeout=5)
                        break
                    wait_for = min(_poll_interval(now - started), max(deadline - now, 0.001))
                    try:
                        code = process.wait(timeout=wait_for)
                    except subprocess.TimeoutExpired:
                        code = None
                if status == "succeeded" and code != 0:
                    status = "failed"
            _update(db, job_id, status=status, exit_code=code)
        except BaseException as exc:
            if process is not None and process.poll() is None:
                audit_action(
                    "job_terminate_process_tree",
                    target=str(process.pid),
                    details={"job_id": job_id, "reason": "worker_error"},
                )
                terminate_process_tree(process.pid, force=True)
            try:
                _update(db, job_id, status="failed", error=str(exc))
            except sqlite3.Error:
                # Recovery path only: a broken worker connection must still make
                # the terminal state visible if the database itself is reachable.
                store.update(job_id, status="failed", error=str(exc))


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2])
