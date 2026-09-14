from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from core import windows_job
from core.executor import _creation_flags, close_background_captures, run_bounded, start_background
from core.jobs import JobStore
from core.windows_job import current_process_in_job, spawn_owned_process

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration requires Windows")


def _alive(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _wait_dead(*pids: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(not _alive(pid) for pid in pids):
            return
        time.sleep(0.02)
    assert {pid: _alive(pid) for pid in pids} == {pid: False for pid in pids}


def test_nested_job_object_kill_on_close_reaps_parent_and_grandchild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_WINDOWS_JOB_OBJECTS", "1")
    nested = current_process_in_job()
    assert nested is not None
    grandchild_file = tmp_path / "grandchild.txt"
    child_code = (
        "import subprocess,sys,time;from pathlib import Path;"
        f"p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
        f"Path({str(grandchild_file)!r}).write_text(str(p.pid));time.sleep(60)"
    )
    owned = spawn_owned_process(
        [sys.executable, "-c", child_code],
        cwd=tmp_path,
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_creation_flags(),
    )
    try:
        assert owned.ownership_backend == "windows_job"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not grandchild_file.exists():
            time.sleep(0.02)
        assert grandchild_file.exists()
        grandchild_pid = int(grandchild_file.read_text())
        assert _alive(owned.pid)
        assert _alive(grandchild_pid)
        owned.close_ownership()
        _wait_dead(owned.pid, grandchild_pid)
    finally:
        owned.close_ownership()
        if _alive(owned.pid):
            owned.kill()
        try:
            owned.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            pass


def test_run_bounded_kills_descendants_after_root_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_WINDOWS_JOB_OBJECTS", "1")
    grandchild_file = tmp_path / "grandchild.txt"
    code = (
        "import subprocess,sys;from pathlib import Path;"
        f"p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
        f"Path({str(grandchild_file)!r}).write_text(str(p.pid))"
    )
    result = run_bounded([sys.executable, "-c", code], cwd=tmp_path, timeout_sec=10)
    assert result["ok"] is True
    assert result["process_ownership"] == "windows_job"
    grandchild_pid = int(grandchild_file.read_text())
    _wait_dead(grandchild_pid)


def test_runtime_owned_background_tree_dies_when_capture_owner_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_WINDOWS_JOB_OBJECTS", "1")
    grandchild_file = tmp_path / "grandchild.txt"
    code = (
        "import subprocess,sys,time;from pathlib import Path;"
        f"p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
        f"Path({str(grandchild_file)!r}).write_text(str(p.pid));time.sleep(60)"
    )
    result = start_background(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        output_mode="tail",
    )
    pid = int(result["pid"])
    try:
        assert result["process_ownership"] == "windows_job"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not grandchild_file.exists():
            time.sleep(0.02)
        assert grandchild_file.exists()
        grandchild_pid = int(grandchild_file.read_text())
        close_background_captures()
        _wait_dead(pid, grandchild_pid)
    finally:
        close_background_captures()
        if _alive(pid):
            psutil.Process(pid).kill()


def test_background_root_exit_reaps_surviving_descendant_without_output_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_WINDOWS_JOB_OBJECTS", "1")
    grandchild_file = tmp_path / "grandchild-auto-reap.txt"
    code = (
        "import subprocess,sys;from pathlib import Path;"
        f"p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
        f"Path({str(grandchild_file)!r}).write_text(str(p.pid))"
    )
    result = start_background(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        output_mode="tail",
    )
    pid = int(result["pid"])
    try:
        assert result["process_ownership"] == "windows_job"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not grandchild_file.exists():
            time.sleep(0.02)
        assert grandchild_file.exists()
        grandchild_pid = int(grandchild_file.read_text())
        _wait_dead(pid, grandchild_pid, timeout=8)
    finally:
        close_background_captures()
        if _alive(pid):
            psutil.Process(pid).kill()


def test_job_object_unavailable_falls_back_without_double_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_WINDOWS_JOB_OBJECTS", "1")
    marker = tmp_path / "count.txt"

    def unavailable() -> windows_job.WindowsJob:
        raise OSError("job objects unavailable")

    monkeypatch.setattr(windows_job.WindowsJob, "create", staticmethod(unavailable))
    code = (
        "from pathlib import Path;"
        f"p=Path({str(marker)!r});n=int(p.read_text())+1 if p.exists() else 1;p.write_text(str(n))"
    )
    result = run_bounded([sys.executable, "-c", code], cwd=tmp_path, timeout_sec=10)
    assert result["ok"] is True
    assert result["process_ownership"] == "psutil_fallback"
    assert marker.read_text() == "1"


def test_job_object_assignment_failure_falls_back_without_running_suspended_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_WINDOWS_JOB_OBJECTS", "1")
    marker = tmp_path / "assignment-count.txt"

    def fail_assignment(self: windows_job.WindowsJob, process_handle: int) -> None:
        raise OSError(f"assignment rejected for {process_handle}")

    monkeypatch.setattr(windows_job.WindowsJob, "assign_process_handle", fail_assignment)
    code = (
        "from pathlib import Path;"
        f"p=Path({str(marker)!r});n=int(p.read_text())+1 if p.exists() else 1;p.write_text(str(n))"
    )
    result = run_bounded([sys.executable, "-c", code], cwd=tmp_path, timeout_sec=10)
    assert result["ok"] is True
    assert result["process_ownership"] == "psutil_fallback"
    assert marker.read_text() == "1"


def test_worker_crash_closes_job_and_reaps_durable_command_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_WINDOWS_JOB_OBJECTS", "1")
    path = tmp_path / "jobs.sqlite3"
    store = JobStore(path)
    tree_file = tmp_path / "tree.json"
    code = (
        "import json,os,subprocess,sys,time;from pathlib import Path;"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
        f"Path({str(tree_file)!r}).write_text(json.dumps([os.getpid(),p.pid]));time.sleep(60)"
    )
    submitted = store.submit(
        [sys.executable, "-c", code],
        tmp_path,
        120,
        "worker-crash-job-object",
    )
    job_id = submitted["job_id"]
    worker_pid: int | None = None
    command_pid: int | None = None
    grandchild_pid: int | None = None
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            raw = store.raw(job_id)
            if raw["worker_pid"] and raw["pid"] and tree_file.exists():
                worker_pid = int(raw["worker_pid"])
                command_pid, grandchild_pid = [int(value) for value in json.loads(tree_file.read_text())]
                break
            time.sleep(0.05)
        assert worker_pid and command_pid and grandchild_pid
        assert _alive(worker_pid)
        assert _alive(command_pid)
        assert _alive(grandchild_pid)

        psutil.Process(worker_pid).kill()
        _wait_dead(worker_pid, command_pid, grandchild_pid, timeout=8)
        status = store.get(job_id)
        assert status["status"] == "interrupted"
    finally:
        for pid in (worker_pid, command_pid, grandchild_pid):
            if pid and _alive(pid):
                try:
                    psutil.Process(pid).kill()
                except psutil.NoSuchProcess:
                    pass
