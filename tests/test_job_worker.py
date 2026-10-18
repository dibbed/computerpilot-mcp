from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psutil
import pytest

from core.jobs import JobStore
from core.process_ownership import PosixOwnedProcess
from core.resource_claims import coordination_claim, make_resource_capability, path_claim
from core.windows_job import NativeOwnershipRequiredError
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


def test_worker_settles_resource_context_before_publishing_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.resource_claims import RESOURCE_CAPABILITY_ENV, _parse_resource_capability

    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = "6" * 32
    _seed(store, job_id, tmp_path)

    def spawn(*args: object, **kwargs: object) -> SimpleNamespace:
        env = kwargs["env"]
        assert isinstance(env, dict)
        request, owner, path = _parse_resource_capability(env[RESOURCE_CAPABILITY_ENV])
        authority = WorkflowStore(path, recover=False)
        assert authority.reserve_capacity(
            admission_id="unknown-child", category="analyzer", owner_token="unknown-owner", weight=1, limit=1,
            resource_request_id=request, resource_owner_token=owner,
        )["admitted"] is True
        return SimpleNamespace(pid=os.getpid(), poll=lambda: 0, wait=lambda timeout: 0, close_ownership=lambda: None)

    monkeypatch.setattr(job_worker, "spawn_owned_process", spawn)
    job_worker.run(store.path, job_id)
    assert store.raw(job_id)["status"] == "failed"
    authority = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    assert authority.health_summary()["quarantined_resource_request_count"] == 1
    assert authority.health_summary()["active_analyzer_capacity_weight"] == 1


def test_cancel_winning_during_resource_wait_prevents_child_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = "7" * 32
    _seed(store, job_id, tmp_path)
    original = job_worker.validate_resource_capability
    dispatched = []

    def cancel_before_dispatch(*args: object, **kwargs: object) -> WorkflowStore:
        authority = original(*args, **kwargs)  # type: ignore[arg-type]
        store.cancel(job_id)
        return authority

    def spawn(*args: object, **kwargs: object) -> None:
        dispatched.append(True)
        raise AssertionError("Cancelled job dispatched")

    monkeypatch.setattr(job_worker, "validate_resource_capability", cancel_before_dispatch)
    monkeypatch.setattr(job_worker, "spawn_owned_process", spawn)
    job_worker.run(store.path, job_id)
    assert dispatched == []
    assert store.raw(job_id)["status"] == "cancelled"
    assert WorkflowStore(tmp_path / "workflows.sqlite3", recover=False).health_summary()["quarantined_resource_request_count"] == 0


def test_successful_root_exit_settles_dead_descendant_capacity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = tmp_path / "capacity_child.py"
    fixture.write_text('''
import json,os,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import psutil
from core.runtime_capacity import acquire_runtime_capacity
root=Path(sys.argv[2])
if len(sys.argv)>3:
 permit=acquire_runtime_capacity(root/'workflows.sqlite3',category='analyzer',limit=1,deadline=time.monotonic()+5)
 assert permit is not None
 (root/'ready.tmp').write_text(json.dumps({'pid':os.getpid(),'created':psutil.Process().create_time()}))
 (root/'ready.tmp').replace(root/'ready')
 time.sleep(60)
else:
 subprocess.Popen([sys.executable,__file__,sys.argv[1],sys.argv[2],'child'])
 deadline=time.monotonic()+10
 while not (root/'ready').exists() and time.monotonic()<deadline:time.sleep(.01)
 assert (root/'ready').exists()
''', encoding="utf-8")
    store = JobStore(tmp_path / "jobs.sqlite3")
    probe_worker = tmp_path / "probe_worker.py"
    probe_worker.write_text('''
import json,sqlite3,sys
from contextlib import ExitStack,closing
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
import psutil
from core import windows_job
from core.workflow_store import WorkflowStore
original=WorkflowStore.release_dead_resource_capacity
def cleanup(self,*args,**kwargs):
 trace=[]
 def record(name,function):
  def call(*a,**kw):
   item={'stage':name}
   if a and hasattr(a[0],'pid'):item['pid']=a[0].pid
   elif name=='probe':item['pid']=a[0]
   try:
    value=function(*a,**kw)
   except (psutil.Error,OSError) as error:
    item.update(error=type(error).__name__,winerror=getattr(error,'winerror',None));trace.append(item);raise
   item['value']=bool(value) if name=='open' else value;trace.append(item);return value
  return call
 try:
  with ExitStack() as stack:
   for name in ('create_time','is_running','status'):
    stack.enter_context(patch.object(psutil.Process,name,record(name,getattr(psutil.Process,name))))
   stack.enter_context(patch.object(windows_job,'process_identity_exited',record('probe',windows_job.process_identity_exited)))
   if sys.platform=='win32':
    import _winapi
    for name,label in (('OpenProcess','open'),('WaitForSingleObject','wait')):
     stack.enter_context(patch.object(_winapi,name,record(label,getattr(_winapi,name))))
   result=original(self,*args,**kwargs)
   trace.append({'released':result})
   return result
 finally:
  with closing(sqlite3.connect(self.path)) as db:
   trace.append({'capacity':db.execute('SELECT state,count(*) FROM runtime_capacity_requests GROUP BY state').fetchall(),
                 'claims':db.execute('SELECT state,count(*) FROM workflow_resource_requests GROUP BY state').fetchall()})
  (self.path.parent/'ownership_probe.json').write_text(json.dumps(trace[:64]),encoding='utf-8')
WorkflowStore.release_dead_resource_capacity=cleanup
from scripts.job_worker import run
run(Path(sys.argv[1]),sys.argv[2],sys.argv[3] if len(sys.argv)>3 else None)
''', encoding="utf-8")
    original_popen = subprocess.Popen

    def launch(command: list[str], *args: Any, **kwargs: Any) -> Any:
        if command[1:3] == ["-m", "scripts.job_worker"] and Path(command[3]) == store.path.resolve():
            command = [command[0], str(probe_worker), *command[3:]]
        return original_popen(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)
    job = store.submit([sys.executable, str(fixture), str(Path(__file__).resolve().parents[1]), str(tmp_path)], tmp_path, 15, "descendant")
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            result = store.get(job["job_id"])
            if result["status"] not in {"queued", "running"}:
                break
            time.sleep(0.05)
        if result["status"] != "succeeded":
            probe_path = tmp_path / "ownership_probe.json"
            trace = json.loads(probe_path.read_text(encoding="utf-8")) if probe_path.exists() else []
            pytest.fail(json.dumps({"error": result.get("error"), "ownership_probe": trace}, separators=(",", ":")))
        identity = json.loads((tmp_path / "ready").read_text())
        from core.jobs import same_process

        assert not same_process(identity["pid"], identity["created"])
        health = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False).health_summary()
        assert health["active_analyzer_capacity_weight"] == 0
        assert health["quarantined_resource_request_count"] == 0
    finally:
        store.cancel(job["job_id"])


@pytest.mark.parametrize("owner", ["live", "denied", "reused", "zombie", "unknown", "exited_visible", "wait_denied"])
def test_child_capacity_reclaim_requires_positive_owner_death(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: str,
) -> None:
    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    pid, created = os.getpid(), psutil.Process().create_time()
    store.reserve_resource_claims(
        request_id="parent", owner_token="owner", category="test", claims=(path_claim(tmp_path, "write", scope="subtree"),),
    )
    capacity_owner = "unknown-owner" if owner == "unknown" else f"pid-{pid}-{created:.6f}-fixture"
    assert store.reserve_capacity(
        admission_id="child", category="analyzer", owner_token=capacity_owner,
        weight=1, limit=1, resource_request_id="parent", resource_owner_token="owner",
    )["admitted"] is True
    if owner == "denied":
        def denied(*args: object) -> None:
            raise psutil.AccessDenied(pid)

        monkeypatch.setattr(psutil, "Process", denied)
    elif owner in {"reused", "zombie", "exited_visible", "wait_denied"}:
        process = SimpleNamespace(
            create_time=lambda: created + 10 if owner == "reused" else created,
            is_running=lambda: True, status=lambda: psutil.STATUS_ZOMBIE if owner == "zombie" else psutil.STATUS_RUNNING,
        )
        monkeypatch.setattr(psutil, "Process", lambda *args: process)
        if owner in {"exited_visible", "wait_denied"}:
            from core import windows_job, workflow_store

            monkeypatch.setattr(workflow_store, "os", SimpleNamespace(name="nt"))
            monkeypatch.setattr(windows_job, "process_identity_exited", lambda *args: True if owner == "exited_visible" else None)
    released = store.release_dead_resource_capacity("parent", owner_token="owner")
    assert released == (1 if owner in {"reused", "zombie", "exited_visible"} else 0)
    assert store.health_summary()["active_analyzer_capacity_weight"] == (0 if released else 1)



def test_dead_child_capacity_waits_for_confirmed_windows_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import windows_job, workflow_store

    store = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False)
    pid, created = os.getpid(), psutil.Process().create_time()
    store.reserve_resource_claims(
        request_id="parent", owner_token="owner", category="test",
        claims=(path_claim(tmp_path, "write", scope="subtree"),),
    )
    assert store.reserve_capacity(
        admission_id="child", category="analyzer",
        owner_token=f"pid-{pid}-{created:.6f}-fixture",
        weight=1, limit=1, resource_request_id="parent", resource_owner_token="owner",
    )["admitted"] is True
    # A process may be visible briefly after native tree termination was confirmed.
    # Never release on the first live probe; require a later positive exit signal.
    monkeypatch.setattr(workflow_store, "os", SimpleNamespace(name="nt"))
    observations = iter((False, True))
    monkeypatch.setattr(windows_job, "process_identity_exited", lambda *_: next(observations))
    assert store.release_dead_resource_capacity(
        "parent", owner_token="owner", settle_timeout_sec=0.5,
    ) == 1
    assert store.health_summary()["active_analyzer_capacity_weight"] == 0


def test_worker_releases_fresh_claim_when_native_ownership_denies_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "jobs.sqlite3"
    store = JobStore(path)
    job_id = "f" * 32
    _seed(store, job_id, tmp_path)

    def denied(*args: object, **kwargs: object) -> None:
        raise NativeOwnershipRequiredError("disabled before activation")

    monkeypatch.setattr(job_worker, "spawn_owned_process", denied)
    job_worker.run(path, job_id)

    assert store.raw(job_id)["status"] == "failed"
    health = WorkflowStore(tmp_path / "workflows.sqlite3", recover=False).health_summary()
    assert health["active_resource_request_count"] == 0
    assert health["quarantined_resource_request_count"] == 0


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
