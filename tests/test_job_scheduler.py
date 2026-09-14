from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import psutil

from core.jobs import JobStore


def _seed(store: JobStore, count: int) -> list[str]:
    now = time.time()
    spec = json.dumps(
        {"command": ["fake"], "cwd": str(store.path.parent), "timeout_sec": 60.0, "encoding": "utf-8"},
        sort_keys=True,
    )
    ids = [f"{index + 500:032x}" for index in range(count)]
    with closing(store.connect()) as db, db:
        db.executemany(
            "INSERT INTO jobs (id,request_key,fingerprint,spec,status,created,updated,version) "
            "VALUES (?,?,?,?,?,?,?,1)",
            [(job_id, f"scheduler-{job_id}", f"fp-{job_id}", spec, "queued", now, now) for job_id in ids],
        )
    return ids


def test_concurrent_reservation_never_launches_above_capacity(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    _seed(store, 20)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.reserve_worker_launches(max_running=4), range(8)))

    reservations = [reservation for batch, _ in results for reservation in batch]
    assert len(reservations) == 4
    assert len({job_id for job_id, _ in reservations}) == 4
    with closing(store.connect()) as db:
        reserved = int(
            db.execute(
                "SELECT count(*) FROM jobs WHERE status='queued' AND launch_token IS NOT NULL"
            ).fetchone()[0]
        )
        running = int(db.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0])
    assert reserved == 4
    assert running == 0


def test_live_launch_reservation_is_not_duplicated(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = _seed(store, 1)[0]
    pid = os.getpid()
    created = psutil.Process(pid).create_time()
    with closing(store.connect()) as db, db:
        db.execute(
            "UPDATE jobs SET launch_token='live',launch_started=?,worker_pid=?,worker_created=? WHERE id=?",
            (time.time() - 60, pid, created, job_id),
        )

    reservations, queued = store.reserve_worker_launches(max_running=1)
    assert reservations == []
    assert queued == 1
    row = store.raw(job_id)
    assert row["launch_token"] == "live"


def test_stale_launch_reservation_is_recovered_and_replaced(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = _seed(store, 1)[0]
    with closing(store.connect()) as db, db:
        db.execute(
            "UPDATE jobs SET launch_token='stale',launch_started=?,worker_pid=99999999,worker_created=0 WHERE id=?",
            (time.time() - 60, job_id),
        )

    reservations, queued = store.reserve_worker_launches(max_running=1)
    assert queued == 1
    assert len(reservations) == 1
    assert reservations[0][0] == job_id
    assert reservations[0][1] != "stale"
    assert store.raw(job_id)["launch_token"] == reservations[0][1]


def test_reserved_worker_claim_requires_matching_launch_token(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = _seed(store, 1)[0]
    reservations, _ = store.reserve_worker_launches(max_running=1)
    token = reservations[0][1]
    pid = os.getpid()
    created = psutil.Process(pid).create_time()

    assert store.claim_for_execution(
        job_id,
        worker_pid=pid,
        worker_created=created,
        max_running=1,
        launch_token="wrong",
    ) == "terminal"
    assert store.raw(job_id)["status"] == "queued"
    assert store.claim_for_execution(
        job_id,
        worker_pid=pid,
        worker_created=created,
        max_running=1,
        launch_token=token,
    ) == "claimed"
    row = store.raw(job_id)
    assert row["status"] == "running"
    assert row["launch_token"] is None
    assert row["launch_started"] is None
