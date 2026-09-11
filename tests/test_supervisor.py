from __future__ import annotations

import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from core.jobs import JobStore
from scripts import supervisor as module
from scripts.supervisor import Supervisor, make_panel, restart_delay


def close_logger(supervisor: Supervisor) -> None:
    for handler in list(supervisor.logger.handlers):
        handler.close()
        supervisor.logger.removeHandler(handler)


def test_restart_backoff() -> None:
    assert [restart_delay(i) for i in range(1, 7)] == [5, 10, 30, 60, 60, 60]


def test_watchdog_recovers_hung_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "restart_delay", lambda failures: 0)
    counter = tmp_path / "attempts"
    code = (
        "import os,time;from pathlib import Path;"
        f"p=Path({str(counter)!r}); n=int(p.read_text())+1 if p.exists() else 1;p.write_text(str(n));"
        "h=Path(os.environ['MCP_HEARTBEAT_FILE']);h.write_text(str(os.getpid()));"
        "os.utime(h,(0,0)) if n==1 else None;time.sleep(60)"
    )
    supervisor = Supervisor([sys.executable, "-c", code], readiness_url=None, state_dir=tmp_path, grace=0.15, interval=0.03)
    thread = threading.Thread(target=supervisor.run)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            snapshot = supervisor.snapshot()
            if snapshot["restart_count"] >= 1 and snapshot["state"] == "running":
                break
            time.sleep(0.03)
        else:
            raise AssertionError(supervisor.snapshot())
        assert counter.read_text() == "2"
        assert any("watchdog_unhealthy" in message for message in snapshot["logs"])
    finally:
        supervisor.stop.set()
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert not supervisor.heartbeat.exists()


def test_panel_status_controls_and_cross_origin_rejection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor = Supervisor([], readiness_url=None, state_dir=tmp_path)
    panel = make_panel(supervisor, 0)
    thread = threading.Thread(target=panel.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{panel.server_port}"
    try:
        with urllib.request.urlopen(base) as response:
            html = response.read().decode()
        token = re.search(r"const token='([^']+)'", html)
        assert token
        with urllib.request.urlopen(base + "/api/status") as response:
            assert json.load(response)["state"] == "starting"
        for origin, key in [("https://evil.example", token[1]), (base, "wrong")]:
            request = urllib.request.Request(base + "/api/restart", data=b"", headers={"Origin": origin, "X-Control-Token": key})
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            assert error.value.code == 403
        assert not supervisor.restart.is_set()
        for action, event in [("restart", supervisor.restart), ("stop", supervisor.stop)]:
            request = urllib.request.Request(base + "/api/" + action, data=b"",
                                             headers={"Origin": base, "X-Control-Token": token[1]})
            with urllib.request.urlopen(request) as response:
                assert json.load(response)["ok"]
            assert event.is_set()
        request = urllib.request.Request(base, headers={"Host": "evil.example"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 403
    finally:
        panel.shutdown()
        panel.server_close()
        close_logger(supervisor)


def test_rotating_log_and_secret_masking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_PLANE_API_KEY", "test-sensitive-secret")
    supervisor = Supervisor([], readiness_url=None, state_dir=tmp_path)
    supervisor.event("error test-sensitive-secret")
    for _ in range(600):
        supervisor.event("x" * 4096)
    close_logger(supervisor)
    assert (tmp_path / "supervisor.log.1").exists()
    for path in tmp_path.glob("supervisor.log*"):
        assert "test-sensitive-secret" not in path.read_text()
    assert len(supervisor.snapshot()["logs"]) == 100


def test_runtime_restart_preserves_durable_job(tmp_path: Path) -> None:
    database = tmp_path / "jobs.sqlite3"
    output = tmp_path / "completed"
    job_code = f"import time;from pathlib import Path;time.sleep(3);Path({str(output)!r}).write_text('once')"
    code = (
        "import os,sys,time;from pathlib import Path;from core.jobs import JobStore;"
        f"s=JobStore(Path({str(database)!r}));"
        f"s.submit([sys.executable,'-c',{job_code!r}],Path({str(tmp_path)!r}),15,'persistent');"
        "Path(os.environ['MCP_HEARTBEAT_FILE']).write_text(str(os.getpid()));time.sleep(60)"
    )
    supervisor = Supervisor([sys.executable, "-c", code], readiness_url=None, state_dir=tmp_path,
                            grace=10, interval=0.05)
    thread = threading.Thread(target=supervisor.run)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        requested = False
        while time.monotonic() < deadline:
            state = supervisor.snapshot()
            if state["state"] == "running" and not requested:
                supervisor.restart.set()
                requested = True
            if state["state"] == "running" and state["restart_count"] == 1 and output.exists():
                break
            time.sleep(0.05)
        else:
            raise AssertionError(supervisor.snapshot())
        assert output.read_text() == "once"
        assert JobStore(database).list(0, 10)["total_count"] == 1
    finally:
        supervisor.stop.set()
        thread.join(timeout=10)
    assert not thread.is_alive()
