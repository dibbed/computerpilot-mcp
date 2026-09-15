from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import core.job_retention as retention
from core.artifacts import Delivery
from core.job_retention import JobHistoryPolicy, cleanup_job_history
from core.jobs import JobStore


def _policy(*, age: float = 0, count: int = 100, max_bytes: int = 1_000_000, grace: float = 0) -> JobHistoryPolicy:
    return JobHistoryPolicy(
        max_age_sec=age,
        max_count=count,
        max_bytes=max_bytes,
        cleanup_interval_sec=0,
        orphan_grace_sec=grace,
    )


def _insert_job(
    store: JobStore,
    job_id: str,
    status: str,
    updated: float,
    *,
    output_bytes: int = 0,
) -> None:
    spec = json.dumps({"command": ["python"], "cwd": str(store.path.parent), "timeout_sec": 1, "encoding": "utf-8"})
    with store.connect() as db, db:
        db.execute(
            "INSERT INTO jobs (id,request_key,fingerprint,spec,status,created,updated,version) VALUES (?,?,?,?,?,?,?,1)",
            (job_id, f"key-{job_id}", f"fp-{job_id}", spec, status, updated, updated),
        )
    directory = store.output_dir / job_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "stdout.bin").write_bytes(b"x" * output_bytes)
    (directory / "stderr.bin").write_bytes(b"")
    os.utime(directory, (updated, updated))


def _ids(store: JobStore) -> set[str]:
    with store.connect() as db:
        return {str(row[0]) for row in db.execute("SELECT id FROM jobs")}


def test_job_retention_prunes_terminal_only_by_age_count_and_bytes(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    now = 10_000.0
    active = {
        "a" * 32: "queued",
        "b" * 32: "running",
        "c" * 32: "orphaned",
    }
    for job_id, status in active.items():
        _insert_job(store, job_id, status, now - 5_000, output_bytes=1_000)
    for index in range(8):
        _insert_job(store, f"{index + 1:032x}", "succeeded", now - 1_000 + index, output_bytes=100)
    for index in range(8, 14):
        _insert_job(store, f"{index + 1:032x}", "failed", now - 10 + index, output_bytes=100)

    result = cleanup_job_history(
        store.path,
        store.output_dir,
        _policy(age=100, count=4, max_bytes=300),
        now=now,
    )

    assert result.terminal_rows_scanned == 14
    assert result.removed_for_age == 8
    assert result.removed_for_count == 2
    assert result.removed_for_quota == 1
    assert result.remaining_terminal_rows == 3
    assert result.remaining_output_bytes == 300
    assert result.quota_satisfied is True
    assert set(active) <= _ids(store)
    for job_id in active:
        assert (store.output_dir / job_id).is_dir()


def test_job_retention_preserves_newest_terminal_as_safety_floor(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    now = time.time()
    old = "1" * 32
    newest = "2" * 32
    _insert_job(store, old, "succeeded", now - 100, output_bytes=100)
    _insert_job(store, newest, "succeeded", now - 1, output_bytes=2_000)

    result = cleanup_job_history(store.path, store.output_dir, _policy(age=1, count=1, max_bytes=10), now=now)

    assert old not in _ids(store)
    assert newest in _ids(store)
    assert result.remaining_terminal_rows == 1
    assert result.remaining_output_bytes == 2_000
    assert result.quota_satisfied is False
    assert result.safety_floor_preserved is True


def test_job_retention_crash_order_leaves_recoverable_orphan_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    now = time.time()
    old = "3" * 32
    newest = "4" * 32
    _insert_job(store, old, "succeeded", now - 100, output_bytes=100)
    _insert_job(store, newest, "succeeded", now - 1, output_bytes=100)
    directory = store.output_dir / old
    os.utime(directory, (now - 100, now - 100))

    real_rmtree = retention.shutil.rmtree
    monkeypatch.setattr(retention.shutil, "rmtree", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("simulated crash")))
    first = cleanup_job_history(store.path, store.output_dir, _policy(age=1, grace=0), now=now)

    assert old not in _ids(store)
    assert directory.is_dir()
    assert first.errors >= 1

    monkeypatch.setattr(retention.shutil, "rmtree", real_rmtree)
    second = cleanup_job_history(store.path, store.output_dir, _policy(age=1, grace=0), now=now + 1)
    assert directory.exists() is False
    assert second.orphan_dirs_removed == 1


def test_job_output_lock_blocks_retention_until_read_completes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    now = time.time()
    old = "5" * 32
    newest = "6" * 32
    _insert_job(store, old, "succeeded", now - 100, output_bytes=20)
    _insert_job(store, newest, "succeeded", now - 1, output_bytes=20)

    import core.jobs as jobs_module

    entered = threading.Event()
    release = threading.Event()
    original = jobs_module.deliver_file

    def blocked_deliver(
        path: Path,
        *,
        encoding: str = "utf-8",
        offset: int = 0,
        delivery: Delivery = "inline",
        final: bool = True,
    ) -> dict[str, Any]:
        entered.set()
        assert release.wait(2)
        return original(path, encoding=encoding, offset=offset, delivery=delivery, final=final)

    monkeypatch.setattr(jobs_module, "deliver_file", blocked_deliver)
    output_result: list[dict[str, object]] = []
    reader = threading.Thread(target=lambda: output_result.append(store.output(old)))
    reader.start()
    assert entered.wait(2)

    cleanup_done = threading.Event()

    def run_cleanup() -> None:
        cleanup_job_history(store.path, store.output_dir, _policy(age=1, grace=0), now=now)
        cleanup_done.set()

    cleaner = threading.Thread(target=run_cleanup)
    cleaner.start()
    time.sleep(0.05)
    assert cleanup_done.is_set() is False
    assert old in _ids(store)

    release.set()
    reader.join(3)
    cleaner.join(3)
    assert output_result
    assert cleanup_done.is_set()
    assert old not in _ids(store)


def test_orphan_cleanup_rechecks_database_before_deleting_directory(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    now = time.time()
    active = "7" * 32
    _insert_job(store, active, "queued", now - 100, output_bytes=10)
    directory = store.output_dir / active
    os.utime(directory, (now - 100, now - 100))

    result = cleanup_job_history(store.path, store.output_dir, _policy(grace=0), now=now)

    assert active in _ids(store)
    assert directory.is_dir()
    assert result.orphan_dirs_removed == 0
