from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from core.jobs import JobStore
from core.process_ownership import PosixOwnedProcess
from core.resource_claims import coordination_claim, make_resource_capability, path_claim
from core.workflow_store import WorkflowStore
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
        getpgid = getattr(os, "getpgid", None)
        assert getpgid is not None
        assert getpgid(process.pid) == process.pid
    finally:
        process.process.kill()
        process.process.wait(timeout=5)
        process.close_ownership()


def test_worker_propagates_internal_resource_capability_to_child_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "jobs.sqlite3"
    store = JobStore(path)
    job_id = "c" * 32
    resources = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    resources.reserve_resource_claims(
        request_id="parent", owner_token="owner", category="test",
        claims=(coordination_claim(exclusive=True), path_claim(tmp_path, "write", scope="subtree")),
    )
    capability = make_resource_capability("parent", "owner", resources.path)
    spec = json.dumps(
        {
            "command": ["fake"],
            "cwd": str(tmp_path),
            "timeout_sec": 60.0,
            "encoding": "utf-8",
            "_resource_capability": capability,
        }
    )
    with closing(store.connect()) as db, db:
        db.execute(
            "INSERT INTO jobs (id,request_key,fingerprint,spec,status,created,updated) VALUES (?,?,?,?,?,?,?)",
            (job_id, "capability-request", "fingerprint", spec, "queued", 1.0, 1.0),
        )
    directory = store.output_dir / job_id
    directory.mkdir()
    (directory / "stdout.bin").touch()
    (directory / "stderr.bin").touch()
    captured_env: dict[str, str] = {}

    class CompletedCommand:
        pid = 4321

        def poll(self) -> int | None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def close_ownership(self) -> None:
            pass

        def terminate_tree(self, *, force: bool = True) -> dict[str, object]:
            raise AssertionError("completed command must not be terminated")

    def spawn(*args: object, **kwargs: object) -> CompletedCommand:
        del args
        env = kwargs.get("env")
        assert isinstance(env, dict)
        captured_env.update(env)
        return CompletedCommand()

    monkeypatch.setattr(job_worker, "spawn_owned_process", spawn)
    monkeypatch.setattr(
        job_worker.psutil, "Process", lambda *args: SimpleNamespace(create_time=lambda: 1.0, children=lambda **kwargs: []),
    )

    job_worker.run(path, job_id)

    assert captured_env["COMPUTERPILOT_INTERNAL_RESOURCE_CAPABILITY"] == capability
    assert store.raw(job_id)["status"] == "succeeded"
    assert resources.resource_request("parent")["state"] == "active"


@pytest.mark.parametrize("capability_state", ["forged", "scope", "released", "quarantined", "wrong_owner"])
def test_worker_rejects_invalid_capability_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capability_state: str,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = "d" * 32
    _seed(store, job_id, tmp_path)
    resources = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    claims = [coordination_claim(exclusive=True), path_claim(tmp_path, "write", scope="subtree")]
    if capability_state == "scope":
        claims = [path_claim(tmp_path, "read", scope="subtree")]
    resources.reserve_resource_claims(
        request_id="parent", owner_token="owner", category="test",
        claims=claims,
    )
    if capability_state == "released":
        resources.release_resource_request("parent", owner_token="owner")
    if capability_state == "quarantined":
        resources.quarantine_resource_request("parent", owner_token="owner", reason="unknown effect")
    capability = make_resource_capability(
        "parent", "wrong" if capability_state == "wrong_owner" else "owner", resources.path,
    )
    if capability_state == "forged":
        capability = "forged-capability"
    with closing(store.connect()) as db, db:
        spec = json.loads(store.raw(job_id)["spec"])
        spec["_resource_capability"] = capability
        db.execute("UPDATE jobs SET spec=? WHERE id=?", (json.dumps(spec), job_id))

    spawned = False

    def spawn(*args: object, **kwargs: object) -> None:
        nonlocal spawned
        spawned = True
        raise RuntimeError("Invalid capability dispatched a command")

    monkeypatch.setattr(job_worker, "spawn_owned_process", spawn)
    job_worker.run(store.path, job_id)
    assert spawned is False
    assert store.raw(job_id)["status"] == "failed"


def test_worker_reuses_one_runtime_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import config

    monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, state_dir=tmp_path / "unrelated-state"))
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
    assert (tmp_path / "workflows.sqlite3").is_file()
    assert not (tmp_path / "unrelated-state" / "workflows.sqlite3").exists()


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


def test_command_launch_survives_process_metadata_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "jobs.sqlite3"
    store = JobStore(path)
    job_id = "e" * 32
    _seed(store, job_id, tmp_path)

    class CompletedCommand:
        pid = 4321

        def poll(self) -> int | None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def close_ownership(self) -> None:
            pass

        def terminate_tree(self, *, force: bool = True) -> dict[str, object]:
            raise AssertionError("metadata lookup failure must not terminate a launched command")

    def process_metadata(pid: int | None = None) -> SimpleNamespace:
        if pid is None:
            return SimpleNamespace(create_time=lambda: 1.0)
        raise psutil.AccessDenied(pid=pid)

    monkeypatch.setattr(job_worker, "spawn_owned_process", lambda *args, **kwargs: CompletedCommand())
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
    monkeypatch.setattr(
        job_worker.psutil, "Process", lambda *args: SimpleNamespace(create_time=lambda: 1.0, children=lambda **kwargs: []),
    )
    monkeypatch.setattr(job_worker, "audit_action", lambda *args, **kwargs: None)

    job_worker.run(path, job_id)

    row = external.raw(job_id)
    assert row["status"] == "cancelled"
    assert terminated == [5432]


@pytest.mark.parametrize("failure_phase", ["terminate", "close", "alive"])
@pytest.mark.parametrize("inherited", [False, True])
def test_worker_records_failure_when_secondary_ownership_cleanup_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_phase: str, inherited: bool,
) -> None:
    path = tmp_path / "jobs.sqlite3"
    store = JobStore(path)
    job_id = "2" * 32
    _seed(store, job_id, tmp_path)
    events: list[str] = []
    capacity = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    if inherited:
        capacity.reserve_resource_claims(
            request_id="parent", owner_token="parent-owner", category="test",
            claims=(coordination_claim(exclusive=True), path_claim(tmp_path, "write", scope="subtree")),
        )
        with store.connect() as db:
            spec = json.loads(db.execute("SELECT spec FROM jobs WHERE id=?", (job_id,)).fetchone()[0])
            spec["_resource_capability"] = make_resource_capability("parent", "parent-owner", capacity.path)
            db.execute("UPDATE jobs SET spec=? WHERE id=?", (json.dumps(spec), job_id))
    capacity.reserve_capacity(
        admission_id="child-permit", category="analyzer", owner_token="pid-5432-1.000000-test", weight=1, limit=1,
    )

    class Process:
        pid = 5432

        def poll(self) -> int | None:
            return None if failure_phase in {"terminate", "alive"} else 0

        def wait(self, timeout: float | None = None) -> int:
            if failure_phase == "alive" and "terminate" in events:
                return -1
            if failure_phase in {"terminate", "alive"}:
                store.update(job_id, cancel_requested=1)
                raise subprocess.TimeoutExpired("fake", timeout or 0)
            return 0

        def terminate_tree(self, *, force: bool = True) -> dict[str, object]:
            events.append("terminate")
            if failure_phase == "alive":
                return {"alive_pids": [self.pid]}
            raise PermissionError("credential-canary-terminate")

        def close_ownership(self) -> None:
            events.append("close")
            if failure_phase == "alive":
                return
            raise RuntimeError("credential-canary-close")

    monkeypatch.setattr(job_worker, "spawn_owned_process", lambda *args, **kwargs: Process())
    monkeypatch.setattr(
        job_worker.psutil, "Process", lambda *args: SimpleNamespace(create_time=lambda: 1.0, children=lambda **kwargs: []),
    )
    monkeypatch.setattr(job_worker, "audit_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_worker, "_kick_successor", lambda *args: events.append("successor"))
    job_worker.run(path, job_id)
    row = store.raw(job_id)
    assert row["status"] == "failed"
    assert "credential-canary" not in row["error"]
    assert "cleanup" in row["error"].casefold()
    assert "close" in events and events[-1] == "successor"
    assert capacity.health_summary()["active_analyzer_capacity_weight"] == 1
    conflicting = capacity.reserve_resource_claims(
        request_id="after-unknown-cleanup", owner_token="other", category="test",
        claims=(path_claim(tmp_path, "write", scope="subtree"),),
    )
    assert conflicting["admitted"] is False
    assert conflicting["reason"] == "resource_quarantined"
    if failure_phase in {"terminate", "alive"}:
        assert events.count("terminate") == 2
