from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from scripts.supervisor import Supervisor


def _close(supervisor: Supervisor) -> None:
    for logger in (supervisor.logger, supervisor.transport_logger):
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


def _payload(*, poll_age: float, last_enqueue_age: float = 1.0) -> dict[str, object]:
    now = time.time()
    return {
        "runtime": {
            "instance_id": "runtime-test",
            "version": "0.0.15",
            "uptime_seconds": 123,
            "lifecycle": "running",
        },
        "components": {
            "control-plane": {
                "status": "ok",
                "state": "polling",
                "details": {
                    "last_attempt": now - poll_age,
                    "last_success": now - poll_age,
                    "consecutive_failures": 0,
                    "current_poll_age_seconds": poll_age,
                    "configured_wait_seconds": 30,
                    "effective_wait_seconds": 30,
                    "deadline_seconds": 35,
                },
            },
            "queue": {
                "status": "ok",
                "state": "available",
                "details": {
                    "depth": 0,
                    "capacity": 20,
                    "last_enqueue": now - last_enqueue_age,
                    "last_dequeue": now - last_enqueue_age,
                    "backpressure_seconds": 0,
                },
            },
            "dispatcher": {
                "status": "ok",
                "state": "accepting",
                "details": {
                    "active": 0,
                    "pool_limit": 10,
                    "accepting": True,
                },
            },
            "response-delivery": {
                "status": "ok",
                "state": "accepted",
                "details": {
                    "in_progress": 0,
                    "disposition": "accepted",
                    "http_status": 200,
                },
            },
        },
    }


def test_transport_watchdog_requires_consecutive_confirmations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = Supervisor(
        [],
        readiness_url="http://127.0.0.1:8080/readyz",
        state_dir=tmp_path,
    )
    monkeypatch.setattr(supervisor, "_fetch_tunnel_health", lambda: _payload(poll_age=120))
    try:
        first = supervisor.transport_snapshot(True)
        second = supervisor.transport_snapshot(True)
        third = supervisor.transport_snapshot(True)

        assert first["diagnosis"] == "POLL_STALLED"
        assert first["stall_observations"] == 1
        assert first["restart_confirmed"] is False
        assert second["stall_observations"] == 2
        assert second["restart_confirmed"] is False
        assert third["stall_observations"] == 3
        assert third["restart_confirmed"] is True

        snapshot = supervisor.state_snapshot()
        assert snapshot["transport_diagnosis"] == "POLL_STALLED"
        assert snapshot["transport_restart_confirmed"] is True
        assert snapshot["transport_stall_observations"] == 3

        history = tmp_path / "transport-health.jsonl"
        assert history.is_file()
        records = [
            json.loads(line)
            for line in history.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert records
        assert records[-1]["diagnosis"] == "POLL_STALLED"
    finally:
        _close(supervisor)


def test_healthy_observation_resets_poll_stall_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = Supervisor(
        [],
        readiness_url="http://127.0.0.1:8080/readyz",
        state_dir=tmp_path,
    )
    payloads = iter([
        _payload(poll_age=120),
        _payload(poll_age=120),
        _payload(poll_age=1),
        _payload(poll_age=120),
    ])
    monkeypatch.setattr(supervisor, "_fetch_tunnel_health", lambda: next(payloads))
    try:
        assert supervisor.transport_snapshot(True)["stall_observations"] == 1
        assert supervisor.transport_snapshot(True)["stall_observations"] == 2
        healthy = supervisor.transport_snapshot(True)
        assert healthy["diagnosis"] == "HEALTHY"
        assert healthy["stall_observations"] == 0
        again = supervisor.transport_snapshot(True)
        assert again["stall_observations"] == 1
        assert again["restart_confirmed"] is False
    finally:
        _close(supervisor)


def test_upstream_idle_never_counts_toward_transport_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = Supervisor(
        [],
        readiness_url="http://127.0.0.1:8080/readyz",
        state_dir=tmp_path,
    )
    activity = tmp_path / "activity.json"
    activity.write_text(
        json.dumps({
            "schema_version": 1,
            "active_calls": 0,
            "last_tool_name": "server_health",
            "last_received_at": time.time() - 900,
            "last_completed_at": time.time() - 899,
        }),
        encoding="utf-8",
    )
    supervisor.current_tool_activity_path = activity
    monkeypatch.setattr(
        supervisor,
        "_fetch_tunnel_health",
        lambda: _payload(poll_age=1, last_enqueue_age=900),
    )
    try:
        for _ in range(5):
            result = supervisor.transport_snapshot(True)
            assert result["diagnosis"] == "UPSTREAM_IDLE"
            assert result["restart_recommended"] is False
            assert result["restart_confirmed"] is False
            assert result["stall_observations"] == 0
        assert supervisor.state_snapshot()["transport_restart_confirmed"] is False
    finally:
        _close(supervisor)


def test_supervisor_observes_terminal_job_without_next_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3

    now = time.time()
    with sqlite3.connect(tmp_path / "jobs.sqlite3") as db:
        db.execute("CREATE TABLE jobs (status TEXT, updated REAL, exit_code INTEGER)")
        db.execute("INSERT INTO jobs VALUES ('failed', ?, 1)", (now - 900,))
    activity = tmp_path / "activity.json"
    activity.write_text(json.dumps({
        "generation_id": "test-generation", "active_calls": 0,
        "last_received_at": now - 1200, "last_completed_at": now - 1199,
    }), encoding="utf-8")
    tunnel = _payload(poll_age=1, last_enqueue_age=1200)
    components = tunnel["components"]
    assert isinstance(components, dict)
    response = components["response-delivery"]
    assert isinstance(response, dict)
    details = response["details"]
    assert isinstance(details, dict)
    details["last_accepted"] = now - 1198

    supervisor = Supervisor([], readiness_url="http://127.0.0.1:8080/readyz", state_dir=tmp_path)
    supervisor.current_tool_activity_path = activity
    monkeypatch.setattr(supervisor, "_fetch_tunnel_health", lambda: tunnel)
    try:
        result = supervisor.transport_snapshot(True)
        continuation = result["continuation"]
        assert continuation["state"] == "JOB_FINISHED_NO_NEXT_POLL_OBSERVED"
        assert continuation["job_exit_code"] == 1
        assert result["restart_recommended"] is False
        history = (tmp_path / "transport-health.jsonl").read_text(encoding="utf-8")
        assert "JOB_FINISHED_NO_NEXT_POLL_OBSERVED" in history
    finally:
        _close(supervisor)


def test_supervisor_marks_failed_terminal_delivery_uncertain_without_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    activity = tmp_path / "activity.json"
    activity.write_text(json.dumps({
        "active_calls": 0, "last_received_at": now - 900, "last_completed_at": now - 899,
    }), encoding="utf-8")
    tunnel = _payload(poll_age=1, last_enqueue_age=900)
    components = tunnel["components"]
    assert isinstance(components, dict)
    response = components["response-delivery"]
    assert isinstance(response, dict)
    response["status"] = "error"
    response["state"] = "failed"
    response["details"] = {"disposition": "failed", "http_status": 503, "in_progress": 0}
    supervisor = Supervisor([], readiness_url="http://127.0.0.1:8080/readyz", state_dir=tmp_path)
    supervisor.current_tool_activity_path = activity
    monkeypatch.setattr(supervisor, "_fetch_tunnel_health", lambda: tunnel)
    try:
        result = supervisor.transport_snapshot(True)
        assert result["diagnosis"] == "RESPONSE_DELIVERY_STALLED"
        assert result["continuation"]["state"] == "TRANSPORT_RESPONSE_UNCERTAIN"
        assert result["restart_recommended"] is False
        assert result["restart_confirmed"] is False
    finally:
        _close(supervisor)
