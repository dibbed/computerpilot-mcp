from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import cast

import psutil
import pytest

from core import job_scheduler
from core import jobs as jobs_module
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


def test_submit_tolerates_scheduler_precreated_output_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = "f" * 32
    directory = store.output_dir / job_id
    directory.mkdir()
    stdout = directory / "stdout.bin"
    stderr = directory / "stderr.bin"
    stdout.write_bytes(b"existing-output")
    stderr.write_bytes(b"existing-error")
    monkeypatch.setattr(jobs_module.uuid, "uuid4", lambda: type("FixedUUID", (), {"hex": job_id})())
    monkeypatch.setattr(job_scheduler, "ensure_job_scheduler", lambda store: None)

    result = store.submit(["fake"], tmp_path, 10.0, "precreated-output")

    assert result["job_id"] == job_id
    assert result["status"] == "queued"
    assert stdout.read_bytes() == b"existing-output"
    assert stderr.read_bytes() == b"existing-error"


def test_concurrent_scheduler_ensure_reuses_single_coordinator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStore:
        path = tmp_path / "jobs.sqlite3"

        @staticmethod
        def has_queued_jobs() -> bool:
            return True

    created: list[FakeScheduler] = []
    barrier = threading.Barrier(2)

    class FakeScheduler:
        def __init__(self, store: object) -> None:
            self.store = store
            self.key = str(FakeStore.path)
            self.started = False
            created.append(self)

        @property
        def alive(self) -> bool:
            return self.started

        def launch_once(self) -> int:
            barrier.wait(timeout=5)
            return 1

        def start(self) -> None:
            self.started = True

        def wake(self) -> None:
            pass

    job_scheduler._SCHEDULERS.clear()
    monkeypatch.setattr(job_scheduler, "JobScheduler", FakeScheduler)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(lambda _: job_scheduler.ensure_job_scheduler(cast(JobStore, FakeStore())), range(2))
            )
        assert len(created) == 1
        assert results[0] is results[1] is created[0]
    finally:
        job_scheduler._SCHEDULERS.clear()


def test_scheduler_start_failure_does_not_poison_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStore:
        path = tmp_path / "jobs.sqlite3"

        @staticmethod
        def has_queued_jobs() -> bool:
            return True

    class FailingScheduler:
        def __init__(self, store: object) -> None:
            self.store = store

        def launch_once(self) -> int:
            return 1

        def start(self) -> None:
            raise RuntimeError("cannot start thread")

        def wake(self) -> None:
            pass

    job_scheduler._SCHEDULERS.clear()
    monkeypatch.setattr(job_scheduler, "JobScheduler", FailingScheduler)
    with pytest.raises(RuntimeError, match="cannot start thread"):
        job_scheduler.ensure_job_scheduler(cast(JobStore, FakeStore()))
    assert job_scheduler._SCHEDULERS == {}


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
