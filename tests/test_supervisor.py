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
from core.lifecycle import RuntimeLifecycle
from core.recovery import OperationRecoveryJournal
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
            try:
                assert error.value.code == 403
            finally:
                error.value.close()
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
        try:
            assert error.value.code == 403
        finally:
            error.value.close()
    finally:
        panel.shutdown()
        panel.server_close()
        thread.join(timeout=5)
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


def test_user_restart_drains_active_mutation_before_cleanup(tmp_path: Path) -> None:
    attempts = tmp_path / "attempts.txt"
    target = tmp_path / "mutation.txt"
    script = tmp_path / "runtime_probe.py"
    script.write_text(
        f"""
import os
import sys
import threading
import time
from pathlib import Path
sys.path.insert(0, {str(module.PROJECT_ROOT)!r})
from core.lifecycle import RUNTIME_LIFECYCLE

heartbeat = Path(os.environ["MCP_HEARTBEAT_FILE"])
attempts = Path({str(attempts)!r})
target = Path({str(target)!r})
RUNTIME_LIFECYCLE.configure_from_env()


def watch() -> None:
    while True:
        RUNTIME_LIFECYCLE.poll_control()
        time.sleep(0.01)


def pulse() -> None:
    while True:
        heartbeat.write_text(str(os.getpid()), encoding="ascii")
        time.sleep(0.03)


threading.Thread(target=watch, daemon=True).start()
threading.Thread(target=pulse, daemon=True).start()
number = int(attempts.read_text()) + 1 if attempts.exists() else 1
attempts.write_text(str(number))
if number == 1:
    with RUNTIME_LIFECYCLE.mutation("safe_refactor"):
        target.write_text("started", encoding="utf-8")
        time.sleep(0.45)
        target.write_text("complete", encoding="utf-8")
while True:
    time.sleep(1)
""",
        encoding="utf-8",
    )
    supervisor = Supervisor(
        [sys.executable, str(script)],
        readiness_url=None,
        state_dir=tmp_path,
        grace=3,
        interval=0.02,
        drain_timeout=2,
        watchdog_drain_timeout=0.2,
    )
    thread = threading.Thread(target=supervisor.run)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if target.exists() and target.read_text(encoding="utf-8") == "started":
                break
            time.sleep(0.02)
        else:
            raise AssertionError(supervisor.snapshot())

        supervisor.restart.set()
        while time.monotonic() < deadline:
            state = supervisor.snapshot()
            if state["restart_count"] >= 1 and state["state"] == "running":
                break
            time.sleep(0.02)
        else:
            raise AssertionError(supervisor.snapshot())

        assert target.read_text(encoding="utf-8") == "complete"
        drain = state["last_drain_result"]
        assert drain["reason"] == "user_restart"
        assert drain["acknowledged"] is True
        assert drain["drained"] is True
        assert drain["active_mutations"] == 0
        assert drain["elapsed_ms"] >= 250
    finally:
        supervisor.stop.set()
        thread.join(timeout=10)
        close_logger(supervisor)
    assert not thread.is_alive()


def test_lifecycle_control_write_retries_transient_windows_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "control.json"
    original_replace = module.os.replace
    calls = 0

    def flaky_replace(source: str | bytes | Path, destination: str | bytes | Path) -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise OSError("transient sharing violation")
        original_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", flaky_replace)
    Supervisor._write_lifecycle_control(path, "drain", "request-1", time.time() + 5)
    assert calls == 3
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["command"] == "drain"
    assert payload["request_id"] == "request-1"


def test_drain_ignores_transient_status_read_failure_after_runtime_was_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = tmp_path / "control.json"
    status = tmp_path / "status.json"
    process = module.subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    supervisor = Supervisor(
        [],
        readiness_url=None,
        state_dir=tmp_path,
        drain_timeout=1.0,
        watchdog_drain_timeout=0.2,
    )
    calls = 0

    def intermittent_read(path: Path) -> dict[str, object] | None:
        nonlocal calls
        calls += 1
        payload = json.loads(control.read_text(encoding="utf-8"))
        request_id = payload["request_id"]
        if calls <= 11:
            return {
                "schema_version": 1,
                "request_id": request_id,
                "state": "DRAINING",
                "active_mutations": 1,
            }
        if calls == 12:
            return None
        return {
            "schema_version": 1,
            "request_id": request_id,
            "state": "DRAINING",
            "active_mutations": 0,
        }

    monkeypatch.setattr(supervisor, "_read_lifecycle_status", intermittent_read)
    try:
        result = supervisor.drain_runtime(
            process,
            reason="user_restart",
            control_path=control,
            status_path=status,
        )
        assert calls >= 13
        assert result["acknowledged"] is True
        assert result["drained"] is True
        assert result["active_mutations"] == 0
    finally:
        process.kill()
        process.wait(timeout=5)
        close_logger(supervisor)


def test_watchdog_drain_is_bounded_when_mutation_does_not_finish(tmp_path: Path) -> None:
    control = tmp_path / "control.json"
    status = tmp_path / "status.json"
    lifecycle = RuntimeLifecycle()
    lifecycle.configure(control, status)
    entered = threading.Event()
    release = threading.Event()
    watcher_stop = threading.Event()

    def mutate() -> None:
        with lifecycle.mutation("run_process"):
            entered.set()
            release.wait(timeout=5)

    def watch() -> None:
        while not watcher_stop.is_set():
            lifecycle.poll_control()
            time.sleep(0.005)

    mutation_thread = threading.Thread(target=mutate)
    watcher_thread = threading.Thread(target=watch)
    mutation_thread.start()
    watcher_thread.start()
    assert entered.wait(timeout=5)
    process = module.subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    supervisor = Supervisor(
        [],
        readiness_url=None,
        state_dir=tmp_path,
        drain_timeout=1,
        watchdog_drain_timeout=0.12,
    )
    try:
        result = supervisor.drain_runtime(
            process,
            reason="watchdog_unhealthy",
            control_path=control,
            status_path=status,
        )
        assert result["acknowledged"] is True
        assert result["drained"] is False
        assert result["timed_out"] is True
        assert result["active_mutations"] == 1
        assert result["elapsed_ms"] < 500
    finally:
        release.set()
        mutation_thread.join(timeout=5)
        watcher_stop.set()
        watcher_thread.join(timeout=5)
        process.kill()
        process.wait(timeout=5)
        close_logger(supervisor)


def test_watchdog_restart_marks_inflight_mutation_uncertain_without_replay(tmp_path: Path) -> None:
    attempts = tmp_path / "attempts.txt"
    marker = tmp_path / "side-effect.txt"
    journal_path = tmp_path / "operation-recovery.jsonl"
    script = tmp_path / "uncertain_runtime.py"
    script.write_text(
        f"""
import os
import threading
import time
from pathlib import Path
import sys
sys.path.insert(0, {str(module.PROJECT_ROOT)!r})
from core.lifecycle import RUNTIME_LIFECYCLE
from core.recovery import OperationRecoveryJournal

heartbeat = Path(os.environ["MCP_HEARTBEAT_FILE"])
attempts = Path({str(attempts)!r})
marker = Path({str(marker)!r})
journal_path = Path({str(journal_path)!r})
RUNTIME_LIFECYCLE.configure_from_env()
journal = OperationRecoveryJournal()
journal.configure(journal_path, os.environ["MCP_RUNTIME_GENERATION_ID"])
number = int(attempts.read_text()) + 1 if attempts.exists() else 1
attempts.write_text(str(number), encoding="ascii")


def watch() -> None:
    while True:
        RUNTIME_LIFECYCLE.poll_control()
        time.sleep(0.005)


threading.Thread(target=watch, daemon=True).start()
if number == 1:
    heartbeat.write_text(str(os.getpid()), encoding="ascii")
    os.utime(heartbeat, (0, 0))
    with RUNTIME_LIFECYCLE.mutation("run_process"):
        journal.begin("run_process", "cwd=test")
        marker.write_text("once", encoding="utf-8")
        while True:
            time.sleep(1)
else:
    while True:
        heartbeat.write_text(str(os.getpid()), encoding="ascii")
        time.sleep(0.03)
""",
        encoding="utf-8",
    )
    supervisor = Supervisor(
        [sys.executable, str(script)],
        readiness_url=None,
        state_dir=tmp_path,
        grace=0.12,
        interval=0.025,
        drain_timeout=1,
        watchdog_drain_timeout=0.12,
    )
    thread = threading.Thread(target=supervisor.run)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = supervisor.snapshot()
            if state["restart_count"] >= 1 and state["state"] == "running":
                break
            time.sleep(0.03)
        else:
            raise AssertionError(supervisor.snapshot())
        assert marker.read_text(encoding="utf-8") == "once"
        assert attempts.read_text(encoding="ascii") == "2"
        journal = OperationRecoveryJournal()
        journal.configure(journal_path, "inspection-runtime")
        summary = journal.summary()
        assert summary["uncertain_count"] == 1
        assert summary["uncertain"][0]["operation_type"] == "run_process"
        assert state["last_exit_reason"] == "watchdog_unhealthy"
        assert state["last_drain_result"]["drained"] is False
    finally:
        supervisor.stop.set()
        thread.join(timeout=10)
        close_logger(supervisor)
    assert not thread.is_alive()
