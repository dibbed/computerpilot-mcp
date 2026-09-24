from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from core.executor import run_bounded, start_background, close_background_captures

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process-group integration requires POSIX")


def _alive(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _wait_dead(*pids: int, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(not _alive(pid) for pid in pids):
            return
        time.sleep(0.02)
    assert {pid: _alive(pid) for pid in pids} == {pid: False for pid in pids}


def test_run_bounded_reaps_descendant_after_root_exit(tmp_path: Path) -> None:
    grandchild_file = tmp_path / "grandchild.txt"
    code = (
        "import subprocess,sys;from pathlib import Path;"
        f"p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
        f"Path({str(grandchild_file)!r}).write_text(str(p.pid))"
    )
    result = run_bounded([sys.executable, "-c", code], cwd=tmp_path, timeout_sec=10)
    assert result["ok"] is True
    assert result["process_ownership"] == "posix_process_group"
    grandchild_pid = int(grandchild_file.read_text())
    _wait_dead(grandchild_pid)


def test_background_owner_close_reaps_process_group(tmp_path: Path) -> None:
    child_file = tmp_path / "child.txt"
    code = (
        "import subprocess,sys,time;from pathlib import Path;"
        f"p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
        f"Path({str(child_file)!r}).write_text(str(p.pid));time.sleep(60)"
    )
    result = start_background([sys.executable, "-c", code], cwd=tmp_path, output_mode="tail")
    root_pid = int(result["pid"])
    try:
        assert result["process_ownership"] == "posix_process_group"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not child_file.exists():
            time.sleep(0.02)
        assert child_file.exists()
        child_pid = int(child_file.read_text())
        close_background_captures()
        _wait_dead(root_pid, child_pid)
    finally:
        close_background_captures()
        for pid in (root_pid, int(child_file.read_text()) if child_file.exists() else None):
            if pid and _alive(pid):
                try:
                    psutil.Process(pid).kill()
                except psutil.NoSuchProcess:
                    pass


def test_posix_process_groups_can_be_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_POSIX_PROCESS_GROUPS", "0")
    result = run_bounded([sys.executable, "-c", "print('ok')"], cwd=tmp_path, timeout_sec=10)
    assert result["ok"] is True
    assert result["process_ownership"] == "psutil"
