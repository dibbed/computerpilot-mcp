from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import job_scheduler
from core.jobs import JOB_SCHEMA_VERSION, QUEUE_TIMEOUT_ERROR, JobStore
from scripts import job_worker


def _old_schema(path: Path) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""CREATE TABLE jobs (
            id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
            spec TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
            worker_pid INTEGER, worker_created REAL, pid INTEGER, pid_created REAL,
            exit_code INTEGER, cancel_requested INTEGER NOT NULL DEFAULT 0, error TEXT
        )""")


def _insert_job(
    store: JobStore,
    job_id: str,
    *,
    status: str = "queued",
    created: float | None = None,
    queue_deadline: float | None = None,
    timeout_sec: float = 60.0,
) -> None:
    now = time.time() if created is None else created
    spec = json.dumps(
        {"command": ["fake"], "cwd": str(store.path.parent), "timeout_sec": timeout_sec, "encoding": "utf-8"},
        sort_keys=True,
    )
    with closing(store.connect()) as db, db:
        db.execute(
            "INSERT INTO jobs "
            "(id,request_key,fingerprint,spec,status,created,updated,version,queue_deadline) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, f"request-{job_id}", hashlib.sha256(spec.encode()).hexdigest(), spec, status, now, now, 1, queue_deadline),
        )
    directory = store.output_dir / job_id
    directory.mkdir()
    (directory / "stdout.bin").touch()
    (directory / "stderr.bin").touch()


def test_old_database_migrates_in_place_and_preserves_default_idempotency(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    _old_schema(path)
    command = ["python", "-c", "print('done')"]
    spec = json.dumps(
        {"command": command, "cwd": str(tmp_path.resolve()), "timeout_sec": 10, "encoding": "utf-8"},
        sort_keys=True,
    )
    fingerprint = hashlib.sha256(spec.encode()).hexdigest()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "INSERT INTO jobs (id,request_key,fingerprint,spec,status,created,updated) VALUES (?,?,?,?,?,?,?)",
            ("0" * 32, "legacy-key", fingerprint, spec, "succeeded", 1.0, 2.0),
        )

    store = JobStore(path)

    with closing(store.connect()) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
        schema_version = int(db.execute("PRAGMA user_version").fetchone()[0])
    assert schema_version == JOB_SCHEMA_VERSION
    assert {"version", "queue_deadline", "launch_token", "launch_started"} <= columns
    row = store.raw("0" * 32)
    assert row["version"] == 1
    assert row["queue_deadline"] is None
    deduplicated = store.submit(command, tmp_path, 10, "legacy-key")
    assert deduplicated["deduplicated"] is True
    assert deduplicated["job_id"] == "0" * 32


def test_e1_schema_migrates_to_e2_without_resetting_job_state(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    deadline = time.time() + 120
    spec = json.dumps(
        {"command": ["fake"], "cwd": str(tmp_path), "timeout_sec": 60.0, "encoding": "utf-8"},
        sort_keys=True,
    )
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""CREATE TABLE jobs (
            id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
            spec TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
            version INTEGER NOT NULL DEFAULT 1, queue_deadline REAL,
            worker_pid INTEGER, worker_created REAL, pid INTEGER, pid_created REAL,
            exit_code INTEGER, cancel_requested INTEGER NOT NULL DEFAULT 0, error TEXT
        )""")
        db.execute("PRAGMA user_version=2")
        db.execute(
            "INSERT INTO jobs (id,request_key,fingerprint,spec,status,created,updated,version,queue_deadline) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("e" * 32, "e1-key", "e1-fingerprint", spec, "queued", 1.0, 2.0, 7, deadline),
        )

    store = JobStore(path)
    row = store.raw("e" * 32)
    with closing(store.connect()) as db:
        columns = {str(item[1]) for item in db.execute("PRAGMA table_info(jobs)")}
        schema_version = int(db.execute("PRAGMA user_version").fetchone()[0])

    assert schema_version == JOB_SCHEMA_VERSION == 3
    assert {"launch_token", "launch_started"} <= columns
    assert row["version"] == 7
    assert row["queue_deadline"] == deadline
    assert row["status"] == "queued"
    assert row["launch_token"] is None
    assert row["launch_started"] is None


def test_schema_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    JobStore(path)
    second = JobStore(path)
    with closing(second.connect()) as db:
        assert int(db.execute("PRAGMA user_version").fetchone()[0]) == JOB_SCHEMA_VERSION
        columns = [row[1] for row in db.execute("PRAGMA table_info(jobs)")]
    assert columns.count("version") == 1
    assert columns.count("queue_deadline") == 1
    assert columns.count("launch_token") == 1
    assert columns.count("launch_started") == 1


def test_concurrent_old_schema_migration_is_serialized(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    _old_schema(path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        stores = list(pool.map(lambda _: JobStore(path), range(16)))
    assert len(stores) == 16
    with closing(stores[0].connect()) as db:
        assert int(db.execute("PRAGMA user_version").fetchone()[0]) == JOB_SCHEMA_VERSION
        columns = [row[1] for row in db.execute("PRAGMA table_info(jobs)")]
    assert columns.count("version") == 1
    assert columns.count("queue_deadline") == 1
    assert columns.count("launch_token") == 1
    assert columns.count("launch_started") == 1


def test_newer_job_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(f"PRAGMA user_version={JOB_SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="newer than supported"):
        JobStore(path)


def test_submit_queue_timeout_is_optional_and_part_of_new_request_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    monkeypatch.setattr(job_scheduler, "ensure_job_scheduler", lambda store: None)
    before = time.time()
    result = store.submit(["fake"], tmp_path, 60, "queue-key", queue_timeout_sec=5)
    after = time.time()
    assert result["status"] == "queued"
    assert result["version"] == 1
    assert before + 5 <= result["queue_deadline"] <= after + 5
    raw = store.raw(result["job_id"])
    assert json.loads(raw["spec"])["queue_timeout_sec"] == 5


def test_state_updates_increment_version_monotonically(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = "1" * 32
    _insert_job(store, job_id)
    assert store.get(job_id)["version"] == 1
    store.update(job_id, cancel_requested=1)
    assert store.get(job_id)["version"] == 2
    store.update(job_id, status="cancelled")
    row = store.get(job_id)
    assert row["version"] == 3
    assert row["status"] == "cancelled"


def test_queued_job_without_deadline_does_not_age_out(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = "2" * 32
    _insert_job(store, job_id, created=time.time() - 86_400)
    row = store.get(job_id)
    assert row["status"] == "queued"
    assert row["version"] == 1
    assert row["queue_deadline"] is None


def test_expired_queue_deadline_is_terminal_and_versioned(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = "3" * 32
    _insert_job(store, job_id, queue_deadline=time.time() - 1)
    row = store.get(job_id)
    assert row["status"] == "timed_out"
    assert row["version"] == 2
    assert row["error"] == QUEUE_TIMEOUT_ERROR


def test_worker_never_claims_job_after_queue_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = "4" * 32
    _insert_job(store, job_id, queue_deadline=time.time() - 1)
    monkeypatch.setattr(job_worker.psutil, "Process", lambda *args: SimpleNamespace(create_time=lambda: 1.0))

    def unexpected_spawn(*args: object, **kwargs: object) -> None:
        raise AssertionError("expired queued job must not spawn a command")

    monkeypatch.setattr(job_worker.subprocess, "Popen", unexpected_spawn)
    job_worker.run(store.path, job_id)
    row = store.raw(job_id)
    assert row["status"] == "timed_out"
    assert row["version"] == 2
    assert row["error"] == QUEUE_TIMEOUT_ERROR


def test_old_queued_age_does_not_reduce_execution_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = "5" * 32
    _insert_job(store, job_id, created=time.time() - 86_400, timeout_sec=60)

    class FakeProcess:
        pid = 4321

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def poll(self) -> int | None:
            return 0

    monkeypatch.setattr(job_worker.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(job_worker.psutil, "Process", lambda *args: SimpleNamespace(create_time=lambda: 1.0))
    monkeypatch.setattr(job_worker, "audit_action", lambda *args, **kwargs: None)
    job_worker.run(store.path, job_id)
    row = store.raw(job_id)
    assert row["status"] == "succeeded"
    assert row["version"] == 4
