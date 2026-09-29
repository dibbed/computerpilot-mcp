from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.jobs import JobStore
from core.process_ownership import PosixOwnedProcess
from scripts import job_worker


def _seed(store: JobStore, job_id: str, cwd: Path) -> None:
    spec = json.dumps({"command": ["fake"], "cwd": str(cwd), "timeout_sec": 60.0, "encoding": "utf-8"})
    with closing(store.connect()) as db, db:
        db.execute(
            "INSERT INTO jobs (id,request_key,fingerprint,spec,status,created,updated) VALUES (?,?,?,?,?,?,?)",
            (job_id, "request", "fingerprint", spec, "queued", 1.0, 1.0),
        )
    directory = store.output_dir / job_id
    directory.mkdir()
    (directory / "stdout.bin").touch()
    (directory / "stderr.bin").touch()


def test_poll_interval_backs_off_for_long_jobs() -> None:
    assert job_worker._poll_interval(0.0) == 0.2
    assert job_worker._poll_interval(1.99) == 0.2
    assert job_worker._poll_interval(2.0) == 0.5
    assert job_worker._poll_interval(29.99) == 0.5
    assert job_worker._poll_interval(30.0) == 1.0


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group ownership only")
def test_worker_spawn_owns_descendants_through_posix_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_POSIX_PROCESS_GROUPS", "1")
    process = job_worker.spawn_owned_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path, env=None, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0,
    )
    try:
        assert isinstance(process, PosixOwnedProcess)
        assert os.getpgid(process.pid) == process.pid
    finally:
        process.process.kill()
        process.process.wait(timeout=5)
        process.close_ownership()


def test_worker_reuses_one_runtime_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "jobs.sqlite3"
    seed_store = JobStore(path)
    job_id = "0" * 32
    _seed(seed_store, job_id, tmp_path)
    opened = 0

    class CountingStore(JobStore):
        def connect(self) -> sqlite3.Connection:
            nonlocal opened
            opened += 1
            return super().connect()

    class FakeProcess:
        pid = 4321

        def __init__(self) -> None:
            self.wait_calls = 0
            self.finished = False

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls += 1
            if self.wait_calls < 3:
                raise subprocess.TimeoutExpired("fake", timeout or 0)
            self.finished = True
            return 0

        def poll(self) -> int | None:
            return 0 if self.finished else None

        def close_ownership(self) -> None:
            pass

        def terminate_tree(self, *, force: bool = True) -> dict[str, object]:
            self.finished = True
            return {"targeted_pids": [self.pid], "terminated_pids": [self.pid], "alive_pids": []}

    process = FakeProcess()
    monkeypatch.setattr(job_worker, "JobStore", CountingStore)
    monkeypatch.setattr(job_worker, "spawn_owned_process", lambda *args, **kwargs: process)
    monkeypatch.setattr(job_worker.psutil, "Process", lambda *args: SimpleNamespace(create_time=lambda: 1.0))
    monkeypatch.setattr(job_worker, "audit_action", lambda *args, **kwargs: None)

    job_worker.run(path, job_id)

    # Submit already initialized the database; the worker keeps exactly one runtime connection.
    assert opened == 1
    assert process.wait_calls == 3
    assert seed_store.raw(job_id)["status"] == "succeeded"


def test_exited_command_cannot_record_reused_pid_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "jobs.sqlite3"
    store = JobStore(path)
    job_id = "d" * 32
    _seed(store, job_id, tmp_path)

    class ExitedCommand:
        pid = 4321

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def close_ownership(self) -> None:
            pass

    def process_metadata(pid: int | None = None) -> SimpleNamespace:
        return SimpleNamespace(create_time=lambda: 999.0 if pid is not None else 1.0)

    monkeypatch.setattr(job_worker, "spawn_owned_process", lambda *args, **kwargs: ExitedCommand())
    monkeypatch.setattr(job_worker.psutil, "Process", process_metadata)

    job_worker.run(path, job_id)

    row = store.raw(job_id)
    assert row["status"] == "succeeded"
    assert row["pid"] == 4321
    assert row["pid_created"] is None


def test_worker_sees_external_cancel_on_persistent_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "jobs.sqlite3"
    external = JobStore(path)
    job_id = "1" * 32
    _seed(external, job_id, tmp_path)
    terminated: list[int] = []

    class FakeProcess:
        pid = 5432

        def __init__(self) -> None:
            self.wait_calls = 0
            self.finished = False

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls += 1
            if self.finished:
                return -1
            if self.wait_calls == 1:
                external.update(job_id, cancel_requested=1)
                raise subprocess.TimeoutExpired("fake", timeout or 0)
            return -1

        def poll(self) -> int | None:
            return -1 if self.finished else None

        def close_ownership(self) -> None:
            pass

        def terminate_tree(self, *, force: bool = True) -> dict[str, object]:
            terminated.append(self.pid)
            self.finished = True
            return {"targeted_pids": [self.pid], "terminated_pids": [self.pid], "alive_pids": []}

    process = FakeProcess()

    monkeypatch.setattr(job_worker, "spawn_owned_process", lambda *args, **kwargs: process)
    monkeypatch.setattr(job_worker.psutil, "Process", lambda *args: SimpleNamespace(create_time=lambda: 1.0))
    monkeypatch.setattr(job_worker, "audit_action", lambda *args, **kwargs: None)

    job_worker.run(path, job_id)

    row = external.raw(job_id)
    assert row["status"] == "cancelled"
    assert terminated == [5432]
