from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import psutil
import pytest

from core.jobs import ACTIVE_JOB_STATUSES, JobStore, same_process


def _seed_queued(store: JobStore, count: int) -> list[str]:
    now = time.time()
    job_ids = [f"{index + 1:032x}" for index in range(count)]
    spec = json.dumps(
        {"command": ["fake"], "cwd": str(store.path.parent), "timeout_sec": 60.0, "encoding": "utf-8"},
        sort_keys=True,
    )
    with closing(store.connect()) as db, db:
        db.executemany(
            "INSERT INTO jobs (id,request_key,fingerprint,spec,status,created,updated,version) "
            "VALUES (?,?,?,?,?,?,?,1)",
            [(job_id, f"key-{job_id}", f"fingerprint-{job_id}", spec, "queued", now, now) for job_id in job_ids],
        )
    return job_ids


def _active_count(store: JobStore) -> int:
    with closing(store.connect()) as db:
        return int(
            db.execute(
                "SELECT count(*) FROM jobs WHERE status IN (?,?)",
                ACTIVE_JOB_STATUSES,
            ).fetchone()[0]
        )


def _wait_jobs(store: JobStore, job_ids: list[str], timeout: float = 20.0) -> list[dict[str, object]]:
    deadline = time.monotonic() + timeout
    rows: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        rows = [store.get(job_id) for job_id in job_ids]
        if all(str(row["status"]) in {"succeeded", "failed", "cancelled", "timed_out", "interrupted"} for row in rows):
            return rows
        time.sleep(0.02)
    raise AssertionError(rows)


def _wait_workers_exit(store: JobStore, job_ids: list[str], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = [store.raw(job_id) for job_id in job_ids]
        if all(not same_process(row["worker_pid"], row["worker_created"]) for row in rows):
            return
        time.sleep(0.02)
    raise AssertionError("Durable workers did not exit after terminal job states.")


def test_concurrent_claims_never_exceed_capacity(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_ids = _seed_queued(store, 20)
    pid = os.getpid()
    created = psutil.Process(pid).create_time()

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(
            pool.map(
                lambda job_id: store.claim_for_execution(
                    job_id,
                    worker_pid=pid,
                    worker_created=created,
                    max_running=4,
                ),
                job_ids,
            )
        )

    assert results.count("claimed") == 4
    assert results.count("wait") == 16
    assert _active_count(store) == 4


def test_same_job_is_claimed_at_most_once_under_contention(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = _seed_queued(store, 1)[0]
    pid = os.getpid()
    created = psutil.Process(pid).create_time()

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(
            pool.map(
                lambda _: store.claim_for_execution(
                    job_id,
                    worker_pid=pid,
                    worker_created=created,
                    max_running=4,
                ),
                range(32),
            )
        )

    assert results.count("claimed") == 1
    assert store.raw(job_id)["status"] == "running"


def test_terminal_transition_releases_capacity_without_slot_counter(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    first, second = _seed_queued(store, 2)
    pid = os.getpid()
    created = psutil.Process(pid).create_time()

    assert store.claim_for_execution(first, worker_pid=pid, worker_created=created, max_running=1) == "claimed"
    assert store.claim_for_execution(second, worker_pid=pid, worker_created=created, max_running=1) == "wait"
    store.update(first, status="succeeded", exit_code=0)
    assert store.claim_for_execution(second, worker_pid=pid, worker_created=created, max_running=1) == "claimed"
    assert _active_count(store) == 1


def test_orphaned_command_consumes_capacity_until_process_is_gone(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    first, second = _seed_queued(store, 2)
    pid = os.getpid()
    created = psutil.Process(pid).create_time()
    now = time.time()
    with closing(store.connect()) as db, db:
        db.execute(
            "UPDATE jobs SET status='orphaned',pid=?,pid_created=?,updated=?,version=version+1 WHERE id=?",
            (pid, created, now, first),
        )

    assert store.claim_for_execution(second, worker_pid=pid, worker_created=created, max_running=1) == "wait"
    with closing(store.connect()) as db, db:
        db.execute(
            "UPDATE jobs SET pid=99999999,pid_created=0,updated=?,version=version+1 WHERE id=?",
            (time.time(), first),
        )
    assert store.claim_for_execution(second, worker_pid=pid, worker_created=created, max_running=1) == "claimed"
    assert store.get(first)["status"] == "interrupted"


def test_cancelled_queued_job_cannot_race_into_running(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    pid = os.getpid()
    created = psutil.Process(pid).create_time()

    for index in range(20):
        job_id = _seed_queued(store, 1)[0] if index == 0 else f"{index + 100:032x}"
        if index:
            now = time.time()
            spec = json.dumps(
                {"command": ["fake"], "cwd": str(tmp_path), "timeout_sec": 60.0, "encoding": "utf-8"},
                sort_keys=True,
            )
            with closing(store.connect()) as db, db:
                db.execute(
                    "INSERT INTO jobs (id,request_key,fingerprint,spec,status,created,updated,version) "
                    "VALUES (?,?,?,?,?,?,?,1)",
                    (job_id, f"race-{index}", f"race-fp-{index}", spec, "queued", now, now),
                )
        with ThreadPoolExecutor(max_workers=2) as pool:
            claim_future = pool.submit(
                store.claim_for_execution,
                job_id,
                worker_pid=pid,
                worker_created=created,
                max_running=256,
            )
            cancel_future = pool.submit(store.cancel, job_id)
            claim_future.result()
            cancel_future.result()
        row = store.raw(job_id)
        assert not (row["status"] == "running" and not row["cancel_requested"])
        if row["status"] == "cancelled":
            assert row["cancel_requested"] == 1
        elif row["status"] == "running":
            assert row["cancel_requested"] == 1
            store.update(job_id, status="cancelled")


def test_real_workers_respect_max_running_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_MAX_RUNNING_JOBS", "2")
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_ids: list[str] = []
    for index in range(8):
        result = store.submit(
            [sys.executable, "-c", "import time; time.sleep(0.35)"],
            tmp_path,
            10.0,
            f"real-admission-{index}",
        )
        job_ids.append(str(result["job_id"]))

    peak_active = 0
    saw_queued = False
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        rows = [store.get(job_id) for job_id in job_ids]
        statuses = [str(row["status"]) for row in rows]
        peak_active = max(peak_active, _active_count(store))
        saw_queued = saw_queued or "queued" in statuses
        if all(status == "succeeded" for status in statuses):
            break
        time.sleep(0.02)
    else:
        raise AssertionError(statuses)

    assert saw_queued is True
    assert peak_active <= 2
    assert peak_active == 2
    _wait_workers_exit(store, job_ids)


def test_real_queue_deadline_expires_while_capacity_is_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_MAX_RUNNING_JOBS", "1")
    store = JobStore(tmp_path / "jobs.sqlite3")
    marker = tmp_path / "should-not-run.txt"
    first = store.submit(
        [sys.executable, "-c", "import time; time.sleep(0.6)"],
        tmp_path,
        10.0,
        "holds-slot",
    )
    second_code = f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"
    second = store.submit(
        [sys.executable, "-c", second_code],
        tmp_path,
        10.0,
        "expires-in-queue",
        queue_timeout_sec=0.15,
    )
    rows = _wait_jobs(store, [str(first["job_id"]), str(second["job_id"])])
    by_id = {str(row["job_id"]): row for row in rows}
    assert by_id[str(first["job_id"])]["status"] == "succeeded"
    assert by_id[str(second["job_id"])]["status"] == "timed_out"
    assert not marker.exists()
    _wait_workers_exit(store, [str(first["job_id"]), str(second["job_id"])])
