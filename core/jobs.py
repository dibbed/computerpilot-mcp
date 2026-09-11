"""Durable command jobs with atomic request deduplication and explicit recovery."""

from __future__ import annotations

import codecs
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

import psutil

from core.artifacts import Delivery, deliver_file
from core.config import PROJECT_ROOT, SETTINGS
from core.errors import ToolError


def same_process(pid: int | None, created: float | None) -> bool:
    if not pid or created is None:
        return False
    try:
        process = psutil.Process(pid)
        return abs(process.create_time() - created) < 0.01 and process.is_running()
    except psutil.NoSuchProcess:
        return False


class JobStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or SETTINGS.state_dir / "jobs.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.output_dir = self.path.parent / "jobs"
        self.output_dir.mkdir(exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                spec TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                worker_pid INTEGER, worker_created REAL, pid INTEGER, pid_created REAL,
                exit_code INTEGER, cancel_requested INTEGER NOT NULL DEFAULT 0, error TEXT
            )""")

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def submit(self, command: list[str], cwd: Path, timeout_sec: float, request_key: str,
               encoding: str = "utf-8") -> dict[str, Any]:
        if not command or not command[0] or not cwd.is_dir():
            raise ValueError("An executable and existing working directory are required.")
        codecs.lookup(encoding)
        spec = json.dumps({"command": command, "cwd": str(cwd.resolve()), "timeout_sec": timeout_sec,
                           "encoding": encoding}, sort_keys=True)
        fingerprint = hashlib.sha256(spec.encode()).hexdigest()
        job_id = uuid.uuid4().hex
        now = time.time()
        with closing(self.connect()) as db, db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO jobs (id,request_key,fingerprint,spec,status,created,updated) VALUES (?,?,?,?,?,?,?)",
                (job_id, request_key, fingerprint, spec, "queued", now, now),
            ).rowcount
            existing = db.execute("SELECT id,fingerprint FROM jobs WHERE request_key=?", (request_key,)).fetchone()
        if not inserted:
            if existing["fingerprint"] != fingerprint:
                raise ToolError("idempotency_conflict", "This request key already belongs to a different command.")
            return {"ok": True, "deduplicated": True, **self.get(existing["id"])}
        directory = self.output_dir / job_id
        directory.mkdir()
        (directory / "stdout.bin").touch()
        (directory / "stderr.bin").touch()
        try:
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            # No inherited output pipes: results survive an MCP/tunnel reconnect or restart.
            with (directory / "worker.log").open("ab") as log:
                subprocess.Popen(
                    [sys.executable, "-m", "scripts.job_worker", str(self.path.resolve()), job_id],
                    cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    creationflags=flags, start_new_session=os.name != "nt",
                )
        except Exception as exc:
            self.update(job_id, status="failed", error=str(exc))
            raise
        return {"ok": True, "deduplicated": False, **self.get(job_id)}

    def update(self, job_id: str, **values: Any) -> None:
        allowed = {"status", "worker_pid", "worker_created", "pid", "pid_created", "exit_code", "cancel_requested", "error"}
        if not values.keys() <= allowed:
            raise ValueError("Unsupported job update.")
        values["updated"] = time.time()
        assignment = ",".join(f"{key}=?" for key in values)
        with closing(self.connect()) as db, db:
            db.execute(f"UPDATE jobs SET {assignment} WHERE id=?", (*values.values(), job_id))

    def raw(self, job_id: str) -> dict[str, Any]:
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ToolError("job_not_found", "Unknown job_id.")
        return dict(row)

    def get(self, job_id: str) -> dict[str, Any]:
        row = self.raw(job_id)
        if row["status"] == "running" and not same_process(row["worker_pid"], row["worker_created"]):
            status = "orphaned" if same_process(row["pid"], row["pid_created"]) else "interrupted"
            # Do not overwrite a final result committed while this process checked liveness.
            with closing(self.connect()) as db, db:
                db.execute("UPDATE jobs SET status=?,updated=? WHERE id=? AND status='running'",
                           (status, time.time(), job_id))
            row = self.raw(job_id)
        if row["status"] == "queued" and time.time() - row["created"] > 60:
            with closing(self.connect()) as db, db:
                db.execute("UPDATE jobs SET status='interrupted',updated=? WHERE id=? AND status='queued'",
                           (time.time(), job_id))
            row = self.raw(job_id)
        return {"job_id": row["id"], **{key: row[key] for key in
                ("status", "created", "updated", "pid", "exit_code", "cancel_requested", "error")}}

    def list(self, offset: int, limit: int) -> dict[str, Any]:
        with closing(self.connect()) as db:
            total = db.execute("SELECT count(*) FROM jobs").fetchone()[0]
            rows = db.execute("SELECT id FROM jobs ORDER BY created DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return {"ok": True, "items": [self.get(row["id"]) for row in rows], "total_count": total,
                "offset": offset, "truncated": offset + len(rows) < total}

    def cancel(self, job_id: str) -> dict[str, Any]:
        row = self.get(job_id)
        if row["status"] == "orphaned":
            raw = self.raw(job_id)
            if same_process(raw["pid"], raw["pid_created"]):
                from core.executor import terminate_process_tree
                terminate_process_tree(raw["pid"], force=True)
            self.update(job_id, status="cancelled", cancel_requested=1)
        elif row["status"] in {"queued", "running"}:
            self.update(job_id, cancel_requested=1)
        return {"ok": True, **self.get(job_id)}

    def output(self, job_id: str, since_byte: int = 0, stderr_since_byte: int = 0,
               delivery: Delivery = "inline") -> dict[str, Any]:
        row = self.get(job_id)
        directory = self.output_dir / row["job_id"]
        encoding = json.loads(self.raw(job_id)["spec"])["encoding"]
        final = row["status"] not in {"queued", "running", "orphaned"}
        return {"ok": True, **row,
                "stdout": deliver_file(directory / "stdout.bin", encoding=encoding,
                                       offset=since_byte, delivery=delivery, final=final),
                "stderr": deliver_file(directory / "stderr.bin", encoding=encoding,
                                       offset=stderr_since_byte, delivery=delivery, final=final)}
