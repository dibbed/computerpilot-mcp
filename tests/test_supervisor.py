from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from core.jobs import JobStore
from core.lifecycle import RuntimeLifecycle
from core.recovery import OperationRecoveryJournal
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowStore
from scripts import supervisor as module
from scripts.supervisor import Supervisor, make_panel, restart_delay


def close_logger(supervisor: Supervisor) -> None:
    for logger in (supervisor.logger, supervisor.transport_logger):
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


def _file_text_is(path: Path, expected: str) -> bool:
    try:
        return path.is_file() and path.read_text(encoding="utf-8") == expected
    except (OSError, UnicodeError):
        return False


def test_restart_backoff() -> None:
    assert [restart_delay(i) for i in range(1, 7)] == [5, 10, 30, 60, 60, 60]


def test_supervisor_snapshot_exposes_timing_events_and_errors(tmp_path: Path) -> None:
    supervisor = Supervisor([], readiness_url=None, state_dir=tmp_path)
    try:
        started_at = supervisor.state_snapshot()["supervisor_started_at"]
        supervisor.event("Runtime started pid=123 restart_count=0")
        supervisor.event(json.dumps({
            "level": "ERROR",
            "component": "runtime",
            "msg": "startup failed",
            "error": "boom",
        }))
        runtime_started_at = time.time() - 3
        supervisor.set_state(
            state="running",
            runtime_started_at=runtime_started_at,
            last_runtime_started_at=runtime_started_at,
            runtime_generation=1,
        )

        snapshot = supervisor.state_snapshot()

        assert snapshot["supervisor_started_at"] == started_at
        assert snapshot["supervisor_uptime_seconds"] >= 0
        assert snapshot["runtime_uptime_seconds"] >= 3
        assert snapshot["last_state_change_at"] >= started_at
        assert snapshot["last_event_at"] is not None
        assert snapshot["event_count"] == 2
        assert snapshot["error_count"] == 1
        assert len(snapshot["recent_events"]) == 2
        assert snapshot["recent_events"][-1]["level"] == "ERROR"
        assert snapshot["recent_events"][-1]["component"] == "runtime"
        assert snapshot["recent_errors"][-1]["message"].endswith("startup failed | boom")
        assert snapshot["last_error"].endswith("startup failed | boom")
    finally:
        close_logger(supervisor)


def test_supervisor_treats_canceled_controlplane_response_retry_as_warning(tmp_path: Path) -> None:
    supervisor = Supervisor([], readiness_url=None, state_dir=tmp_path)
    try:
        supervisor.event(json.dumps({
            "level": "ERROR",
            "component": "dispatcher",
            "msg": "failed to post error response to control plane",
            "error": "controlplane responder: retry wait: context canceled",
        }))

        snapshot = supervisor.state_snapshot()

        assert snapshot["event_count"] == 1
        assert snapshot["error_count"] == 0
        assert snapshot["recent_errors"] == []
        assert snapshot["last_error"] is None
        assert snapshot["recent_events"][-1]["level"] == "WARN"
        assert snapshot["recent_events"][-1]["component"] == "dispatcher"
        assert snapshot["recent_events"][-1]["message"] == (
            "WARN dispatcher: failed to post error response to control plane"
            " | controlplane responder: retry wait: context canceled"
        )
    finally:
        close_logger(supervisor)


def test_supervisor_keeps_other_controlplane_post_failures_as_errors(tmp_path: Path) -> None:
    supervisor = Supervisor([], readiness_url=None, state_dir=tmp_path)
    try:
        supervisor.event(json.dumps({
            "level": "ERROR",
            "component": "dispatcher",
            "msg": "failed to post error response to control plane",
            "error": "controlplane responder: unexpected status 503",
        }))

        snapshot = supervisor.state_snapshot()

        assert snapshot["error_count"] == 1
        assert snapshot["recent_events"][-1]["level"] == "ERROR"
        assert snapshot["recent_errors"][-1]["message"].endswith("unexpected status 503")
    finally:
        close_logger(supervisor)


def test_process_snapshot_tolerates_process_table_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor = Supervisor([], readiness_url=None, state_dir=tmp_path)
    supervisor.set_state(pid=12345)

    class BrokenParent:
        pid = 12345

        def children(self, *, recursive: bool = False) -> list[object]:
            raise OSError(1455, "The paging file is too small for this operation to complete")

        def name(self) -> str:
            return "runtime.exe"

        def status(self) -> str:
            return "running"

    monkeypatch.setattr(module.psutil, "Process", lambda pid: BrokenParent())
    try:
        snapshot = supervisor.process_snapshot()
        assert snapshot == {
            "active_processes": [{"pid": 12345, "name": "runtime.exe", "status": "running", "role": "runtime"}],
            "process_count": 1,
        }
        assert any("Process-tree inspection degraded" in line for line in supervisor.state_snapshot()["logs"])
    finally:
        close_logger(supervisor)


def test_cleanup_falls_back_to_popen_when_psutil_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor = Supervisor([], readiness_url=None, state_dir=tmp_path)

    class FakeProcess:
        pid = 54321

        def __init__(self) -> None:
            self.alive = True
            self.kill_calls = 0
            self.returncode: int | None = None

        def poll(self) -> int | None:
            return None if self.alive else self.returncode

        def kill(self) -> None:
            self.kill_calls += 1
            self.alive = False
            self.returncode = 1

        def wait(self, timeout: float | None = None) -> int:
            self.alive = False
            self.returncode = 1
            return 1

    def broken_process(pid: int) -> object:
        raise OSError(1455, "The paging file is too small for this operation to complete")

    monkeypatch.setattr(module.psutil, "Process", broken_process)
    process = FakeProcess()
    try:
        supervisor.cleanup(process)  # type: ignore[arg-type]
        assert process.kill_calls == 1
        assert process.poll() == 1
        assert supervisor.owned == {}
        assert any("Could not inspect runtime root" in line for line in supervisor.state_snapshot()["logs"])
    finally:
        close_logger(supervisor)


def test_cleanup_fallback_releases_runtime_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor = Supervisor([], readiness_url=None, state_dir=tmp_path)
    ready = tmp_path / "ready.txt"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])

    code = (
        "import socket,time;from pathlib import Path;"
        f"s=socket.socket();s.bind(('127.0.0.1',{port}));s.listen();"
        f"Path({str(ready)!r}).write_text('ready');time.sleep(30)"
    )
    process = subprocess.Popen([sys.executable, "-c", code], cwd=tmp_path)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.02)
        assert ready.exists()

        def broken_process(pid: int) -> object:
            raise OSError(1455, "The paging file is too small for this operation to complete")

        monkeypatch.setattr(module.psutil, "Process", broken_process)
        supervisor.cleanup(process)
        assert process.poll() is not None

        bind_deadline = time.monotonic() + 5
        while True:
            try:
                with socket.socket() as rebound:
                    rebound.bind(("127.0.0.1", port))
                break
            except OSError:
                if time.monotonic() >= bind_deadline:
                    raise
                time.sleep(0.05)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        close_logger(supervisor)


def test_supervisor_survives_transient_children_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_children = module.psutil.Process.children
    failures = 0

    def flaky_children(self: object, recursive: bool = False) -> list[object]:
        nonlocal failures
        if failures == 0:
            failures += 1
            raise OSError(1455, "The paging file is too small for this operation to complete")
        return original_children(self, recursive=recursive)

    monkeypatch.setattr(module.psutil.Process, "children", flaky_children)
    code = (
        "import os,time;from pathlib import Path;"
        "h=Path(os.environ['MCP_HEARTBEAT_FILE']);"
        "h.write_text(str(os.getpid()));"
        "time.sleep(30)"
    )
    supervisor = Supervisor([sys.executable, "-c", code], readiness_url=None, state_dir=tmp_path, grace=2, interval=0.03)
    thread = threading.Thread(target=supervisor.run)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            snapshot = supervisor.snapshot()
            if snapshot["state"] == "running":
                break
            time.sleep(0.03)
        else:
            raise AssertionError(supervisor.snapshot())
        assert failures == 1
        assert snapshot["restart_count"] == 0
        assert any("Could not enumerate runtime children" in line for line in snapshot["logs"])
    finally:
        supervisor.stop.set()
        thread.join(timeout=10)
        close_logger(supervisor)
    assert not thread.is_alive()


def test_watchdog_recovers_hung_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "restart_delay", lambda failures: 0)
    counter = tmp_path / "attempts"
    code = f"""
import os
import time
from pathlib import Path

p = Path({str(counter)!r})
n = int(p.read_text()) + 1 if p.exists() else 1
p.write_text(str(n))
h = Path(os.environ["MCP_HEARTBEAT_FILE"])
h.write_text(str(os.getpid()))
if n == 1:
    os.utime(h, (0, 0))
    time.sleep(60)
while True:
    h.write_text(str(os.getpid()))
    time.sleep(0.03)
"""
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
        assert '<html lang="en" dir="ltr">' in html
        assert "ComputerPilot MCP Control Panel" in html
        assert "System & Build Identity" in html
        assert "Resource Budgets" in html
        assert "Transport History" in html
        assert "Recovery Center" in html
        assert "Workflow Dashboard" in html
        assert "Recent Operations" in html
        assert "Runtime Health" in html
        assert "Tool Activity" in html
        assert "Browser Runtime" in html
        assert "Slowest Tools" in html
        assert "System Doctor" in html
        assert "Session Budget" in html
        assert "Operation Recovery" in html
        assert "Response Deadline Drops" in html
        assert "Load more stdout" in html
        assert "o.has_more" in html
        assert "chunk.has_more" in html
        assert 'id="storageCategories"' in html
        assert 'id="processDetail" class="meta-grid"' in html
        assert "Timing & Lifecycle" in html
        assert "Last Exit / Drain" in html
        assert "Recent Events" in html
        assert "Runtime Errors" in html
        assert "Raw Logs" in html
        assert "Job details" in html
        assert "Copy command" in html
        assert "Download stdout" in html
        assert "/api/jobs/" in html
        assert re.search(r"[\u0600-\u06FF]", html) is None
        with urllib.request.urlopen(base + "/api/status") as response:
            status = json.load(response)
        assert status["state"] == "starting"
        assert status["supervisor_started_at"] > 0
        assert status["supervisor_uptime_seconds"] >= 0
        assert status["runtime_uptime_seconds"] is None
        assert status["event_count"] == 0
        assert status["error_count"] == 0
        assert status["recent_events"] == []
        assert status["recent_errors"] == []
        assert status["jobs"]["counts"] == {}
        assert status["jobs"]["active_count"] == 0
        assert status["jobs"]["problem_count"] == 0
        assert status["storage_bytes"]["total"] >= 0

        job_store = JobStore(tmp_path / "jobs.sqlite3")
        job_id = "a" * 32
        created = time.time() - 5
        updated = time.time() - 1
        spec = json.dumps({
            "command": [sys.executable, "-c", "print('panel detail')"],
            "cwd": str(tmp_path),
            "timeout_sec": 15,
            "queue_timeout_sec": 4,
            "encoding": "utf-8",
        }, sort_keys=True)
        db = job_store.connect()
        try:
            with db:
                db.execute(
                    "INSERT INTO jobs "
                    "(id,request_key,fingerprint,spec,status,created,updated,version,worker_pid,pid,exit_code,"
                    "cancel_requested,error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        "panel-detail",
                        "fingerprint",
                        spec,
                        "succeeded",
                        created,
                        updated,
                        2,
                        111,
                        222,
                        0,
                        0,
                        None,
                    ),
                )
        finally:
            db.close()
        output_dir = job_store.output_dir / job_id
        output_dir.mkdir(exist_ok=True)
        (output_dir / "stdout.bin").write_bytes(b"hello stdout\n")
        (output_dir / "stderr.bin").write_bytes(b"warning stderr\n")

        missing_token = urllib.request.Request(base + f"/api/jobs/{job_id}")
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(missing_token)
        try:
            assert error.value.code == 403
        finally:
            error.value.close()

        detail_request = urllib.request.Request(
            base + f"/api/jobs/{job_id}",
            headers={"X-Control-Token": token[1]},
        )
        with urllib.request.urlopen(detail_request) as response:
            detail = json.load(response)
        assert detail["job_id"] == job_id
        assert detail["status"] == "succeeded"
        assert detail["command"][-1] == "print('panel detail')"
        assert detail["command_line"]
        assert detail["cwd"] == str(tmp_path)
        assert detail["timeout_sec"] == 15
        assert detail["queue_timeout_sec"] == 4
        assert detail["worker_pid"] == 111
        assert detail["pid"] == 222
        assert detail["stdout"]["text"] == "hello stdout\n"
        assert detail["stderr"]["text"] == "warning stderr\n"
        assert detail["output_preview_limit_bytes"] == module.PANEL_JOB_OUTPUT_PREVIEW_BYTES

        output_request = urllib.request.Request(
            base + f"/api/jobs/{job_id}/output?stdout_since=6&stderr_since=8&max_bytes=1024",
            headers={"X-Control-Token": token[1]},
        )
        with urllib.request.urlopen(output_request) as response:
            output = json.load(response)
        assert output["stdout"]["text"] == "stdout\n"
        assert output["stderr"]["text"] == "stderr\n"

        download_request = urllib.request.Request(
            base + f"/api/jobs/{job_id}/output/download?stream=stdout",
            headers={"X-Control-Token": token[1]},
        )
        with urllib.request.urlopen(download_request) as response:
            assert response.read() == b"hello stdout\n"
            assert response.headers["Content-Disposition"].startswith("attachment;")

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


def test_panel_observability_endpoints_are_guarded_and_bounded(tmp_path: Path) -> None:
    supervisor = Supervisor([], readiness_url=None, state_dir=tmp_path)
    workflow_store = WorkflowStore(tmp_path / "workflows.sqlite3")
    workflow = workflow_store.create(
        WorkflowDefinition(
            "panel-observability",
            (StepDefinition("inspect", "verify_changes", {"cwd": str(tmp_path)}),),
        ),
        {"api_token": "must-not-leak", "safe": "visible"},
    )
    health_path = tmp_path / "runtime-health-test.json"
    supervisor.current_runtime_health_path = health_path
    health_path.write_text(json.dumps({
        "ok": True,
        "server": "ali_windows_agent_mcp",
        "version": module.SETTINGS.version,
        "tool_profile": "full",
        "tool_count": 112,
        "tool_domains": ["filesystem", "jobs", "recovery", "workflows"],
        "platform_key": "windows-amd64",
        "capabilities": {"secure_tunnel": True, "browser": True},
        "health_status": "healthy",
        "degraded_reasons": [],
        "resource_pressure": "normal",
        "resource_usage": {
            "ast_cache_bytes": {"value": 1024, "max": 4096, "unit": "bytes", "usage_ratio": 0.25},
            "running_jobs": {"value": 0, "max": 4, "unit": "jobs", "usage_ratio": 0.0},
        },
        "rss_mb": 64.0,
        "workflow_total": 0,
        "queued_workflows": 0,
        "running_workflows": 0,
        "uncertain_workflows": 0,
        "unresolved_workflow_operations": 0,
        "operation_recovery": {"uncertain_count": 0, "pending_count": 0, "uncertain": []},
    }), encoding="utf-8")
    (tmp_path / "transport-health.jsonl").write_text(
        json.dumps({"time": time.time(), "diagnosis": "HEALTHY", "severity": "ok"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "audit.jsonl").write_text(
        json.dumps({
            "time": "2026-09-27T22:00:00+00:00",
            "operation": "run_process",
            "outcome": "completed",
            "pid": 123,
            "target": str(tmp_path),
            "details": {"exit_code": 0},
        }) + "\n",
        encoding="utf-8",
    )
    panel = make_panel(supervisor, 0)
    thread = threading.Thread(target=panel.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{panel.server_port}"
    try:
        with urllib.request.urlopen(base) as response:
            html = response.read().decode()
        token_match = re.search(r"const token='([^']+)'", html)
        assert token_match
        token = token_match[1]

        with urllib.request.urlopen(base + "/api/summary") as response:
            summary = json.load(response)
        assert summary["state"] == "starting"
        assert "recent_events" not in summary
        assert "recent_errors" not in summary
        assert "logs" not in summary
        assert "items" not in summary["jobs"]

        guarded = (
            "/api/insights",
            "/api/transport/history",
            "/api/audit",
            "/api/workflows",
            "/api/recovery",
            "/api/config",
            "/api/doctor",
            "/api/diagnostics",
        )
        for endpoint in guarded:
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + endpoint)
            try:
                assert error.value.code == 403
            finally:
                error.value.close()

        def get_json(endpoint: str) -> dict[str, Any]:
            request = urllib.request.Request(
                base + endpoint,
                headers={"X-Control-Token": token},
            )
            with urllib.request.urlopen(request) as response:
                return json.load(response)

        insights = get_json("/api/insights")
        assert insights["identity"]["product"] == "ComputerPilot MCP"
        assert insights["identity"]["version"] == module.SETTINGS.version
        assert insights["runtime_health"]["tool_count"] == 112
        assert insights["runtime_health"]["resource_pressure"] == "normal"
        assert insights["tunnel"]["mode"] == "local-http"
        assert insights["storage"]["total"] >= 0
        assert "cpu_percent" in insights["system"]
        assert "memory" in insights["system"]

        transport = get_json("/api/transport/history")
        assert transport["items"][0]["diagnosis"] == "HEALTHY"
        audit = get_json("/api/audit")
        assert audit["items"][0]["operation"] == "run_process"
        assert get_json("/api/workflows")["items"] == []
        workflow_detail = get_json(f"/api/workflows/{workflow['workflow_id']}")
        assert workflow_detail["workflow_id"] == workflow["workflow_id"]
        assert workflow_detail["steps"][0]["step_index"] == 0
        serialized_workflow = json.dumps(workflow_detail).casefold()
        assert "must-not-leak" not in serialized_workflow
        assert "inputs" not in workflow_detail
        assert "definition" not in workflow_detail
        assert "evidence" not in serialized_workflow
        assert get_json("/api/recovery")["items"] == []
        config = get_json("/api/config")
        assert config["tunnel_poll_watchdog_enabled"] is True
        doctor = get_json("/api/doctor")
        assert doctor["status"] in {"healthy", "warning", "critical", "pending"}
        diagnostics = get_json("/api/diagnostics")
        assert diagnostics["identity"]["product"] == "ComputerPilot MCP"
        assert "secrets" not in json.dumps(diagnostics).casefold()
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
    started = tmp_path / "job-started"
    release = tmp_path / "job-release"
    job_code = (
        "import time;from pathlib import Path;"
        f"started=Path({str(started)!r});release=Path({str(release)!r});output=Path({str(output)!r});"
        "started.write_text('ready');"
        "deadline=time.monotonic()+15;"
        "exec(\"while not release.exists():\\n"
        "    if time.monotonic() >= deadline: raise TimeoutError('release marker timeout')\\n"
        "    time.sleep(0.02)\");"
        "output.write_text('once')"
    )
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
        deadline = time.monotonic() + 30
        requested = False
        while time.monotonic() < deadline:
            state = supervisor.snapshot()
            if state["state"] == "running" and _file_text_is(started, "ready") and not requested:
                supervisor.restart.set()
                requested = True
            if state["state"] == "running" and state["restart_count"] == 1:
                release.write_text("go", encoding="ascii")
            if state["state"] == "running" and state["restart_count"] == 1 and _file_text_is(output, "once"):
                break
            time.sleep(0.05)
        else:
            raise AssertionError(supervisor.snapshot())
        assert output.read_text() == "once"
        assert JobStore(database).list(0, 10)["total_count"] == 1
    finally:
        supervisor.stop.set()
        thread.join(timeout=10)
        close_logger(supervisor)
    assert not thread.is_alive()


def test_consecutive_runtime_restarts_preserve_one_durable_job(tmp_path: Path) -> None:
    database = tmp_path / "jobs.sqlite3"
    output = tmp_path / "completed-twice"
    started = tmp_path / "job-started-twice"
    release = tmp_path / "job-release-twice"
    job_code = (
        "import time;from pathlib import Path;"
        f"started=Path({str(started)!r});release=Path({str(release)!r});output=Path({str(output)!r});"
        "started.write_text('ready');"
        "deadline=time.monotonic()+15;"
        "exec(\"while not release.exists():\\n"
        "    if time.monotonic() >= deadline: raise TimeoutError('release marker timeout')\\n"
        "    time.sleep(0.02)\");"
        "output.write_text('once')"
    )
    code = (
        "import os,sys,time;from pathlib import Path;from core.jobs import JobStore;"
        f"s=JobStore(Path({str(database)!r}));"
        f"s.submit([sys.executable,'-c',{job_code!r}],Path({str(tmp_path)!r}),15,'persistent-twice');"
        "Path(os.environ['MCP_HEARTBEAT_FILE']).write_text(str(os.getpid()));time.sleep(60)"
    )
    supervisor = Supervisor(
        [sys.executable, "-c", code],
        readiness_url=None,
        state_dir=tmp_path,
        grace=10,
        interval=0.05,
    )
    thread = threading.Thread(target=supervisor.run)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        requested_restarts = 0
        while time.monotonic() < deadline:
            state = supervisor.snapshot()
            if (
                state["state"] == "running"
                and _file_text_is(started, "ready")
                and state["restart_count"] == requested_restarts
                and requested_restarts < 2
            ):
                supervisor.restart.set()
                requested_restarts += 1
            if state["state"] == "running" and state["restart_count"] == 2:
                release.write_text("go", encoding="ascii")
            if state["state"] == "running" and state["restart_count"] == 2 and _file_text_is(output, "once"):
                break
            time.sleep(0.05)
        else:
            raise AssertionError(supervisor.snapshot())
        assert requested_restarts == 2
        assert output.read_text() == "once"
        jobs = JobStore(database).list(0, 10)
        assert jobs["total_count"] == 1
    finally:
        supervisor.stop.set()
        thread.join(timeout=10)
        close_logger(supervisor)
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
    monkeypatch.setattr(module, "LIFECYCLE_POLL_SEC", 0.001)
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
        watchdog_drain_timeout=0.5,
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
        assert result["elapsed_ms"] < 1_000
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
    second_ready = tmp_path / "second-runtime-ready.txt"
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
second_ready = Path({str(second_ready)!r})
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
    heartbeat.write_text(str(os.getpid()), encoding="ascii")
    second_ready.write_text("ready", encoding="ascii")
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
        grace=0.2,
        interval=0.025,
        drain_timeout=1,
        watchdog_drain_timeout=0.12,
    )
    thread = threading.Thread(target=supervisor.run)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = supervisor.snapshot()
            if (
                state["restart_count"] >= 1
                and state["state"] == "running"
                and _file_text_is(second_ready, "ready")
            ):
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
