from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts import panel_data
from scripts.panel_data import (
    doctor_snapshot,
    redact_diagnostics,
    storage_breakdown,
    summarize_transport_history,
)


def test_config_snapshot_exposes_backup_count_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(panel_data, "SETTINGS", replace(panel_data.SETTINGS, backup_max_count=123))

    assert panel_data.config_snapshot()["backup_max_count"] == 123


def test_storage_breakdown_groups_operational_state(tmp_path: Path) -> None:
    (tmp_path / "tunnel-runtime").mkdir()
    (tmp_path / "tunnel-runtime" / "client.exe").write_bytes(b"x" * 10)
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / "a.bak").write_bytes(b"x" * 20)
    (tmp_path / "jobs").mkdir()
    (tmp_path / "jobs" / "stdout.bin").write_bytes(b"x" * 30)
    (tmp_path / "release-v0.3.0-local").mkdir()
    (tmp_path / "release-v0.3.0-local" / "bundle.zip").write_bytes(b"x" * 40)
    (tmp_path / "audit.jsonl").write_bytes(b"x" * 50)

    result = storage_breakdown(tmp_path)

    categories = {item["category"]: item["bytes"] for item in result["categories"]}
    assert categories["Tunnel Runtimes"] == 10
    assert categories["Backups"] == 20
    assert categories["Job Outputs"] == 30
    assert categories["Release Builds"] == 40
    assert categories["Audit"] == 50
    assert result["total"] == 150
    assert result["items"][0]["category"] == "Audit"


def test_transport_history_summary_counts_incidents_and_deadline_drops(tmp_path: Path) -> None:
    path = tmp_path / "transport-health.jsonl"
    records = [
        {
            "time": 1,
            "diagnosis": "HEALTHY",
            "severity": "ok",
            "control_plane": {"current_poll_age_seconds": 3},
        },
        {
            "time": 2,
            "diagnosis": "POLL_STALLED",
            "severity": "warning",
            "restart_recommended": True,
            "restart_confirmed": True,
            "control_plane": {"current_poll_age_seconds": 61},
        },
        {
            "time": 3,
            "diagnosis": "UPSTREAM_IDLE",
            "severity": "warning",
            "control_plane": {"current_poll_age_seconds": 12},
        },
    ]
    path.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
    events = [
        {"message": "command response deadline reached; dropping without posting a response"},
        {"message": "ordinary event"},
    ]

    result = summarize_transport_history(path, recent_events=events)

    assert result["count"] == 3
    assert result["incident_count"] == 2
    assert result["poll_stall_count"] == 1
    assert result["watchdog_recovery_count"] == 1
    assert result["restart_recommended_count"] == 1
    assert result["response_deadline_drop_count"] == 1
    assert result["longest_poll_age_seconds"] == 61
    assert result["latest_incident"]["diagnosis"] == "UPSTREAM_IDLE"


def test_doctor_snapshot_reports_specific_runtime_findings() -> None:
    result = doctor_snapshot(
        runtime_health={
            "health_status": "degraded",
            "degraded_reasons": ["resource_pressure"],
            "resource_pressure": "warning",
            "unique_tool_names": True,
            "unresolved_workflow_operations": 2,
            "operation_recovery": {"uncertain_count": 1, "pending_count": 0},
        },
        transport={"diagnosis": "POLL_STALLED", "severity": "warning"},
        system={"memory": {"percent": 55}, "disk": {"percent": 42}},
        tunnel={"mode": "tunnel", "available": True},
    )

    assert result["status"] == "warning"
    names = {item["name"] for item in result["checks"] if item["status"] != "ok"}
    assert "Runtime health" in names
    assert "Transport" in names
    assert "Workflow recovery" in names
    assert "Operation recovery" in names


def test_doctor_snapshot_reports_pending_when_runtime_snapshot_is_missing() -> None:
    result = doctor_snapshot(
        runtime_health={},
        transport={},
        system={"memory": {"percent": 20}, "disk": {"percent": 20}},
        tunnel={"mode": "local-http"},
    )

    assert result["status"] == "pending"
    assert result["pending_count"] > 0


def test_doctor_snapshot_reports_stale_runtime_as_warning() -> None:
    result = doctor_snapshot(runtime_health={"health_status": "stale"}, transport={},
                             system={"memory": {"percent": 20}, "disk": {"percent": 20}}, tunnel={"mode": "local-http"})
    runtime = next(item for item in result["checks"] if item["name"] == "Runtime health")
    assert runtime["status"] == "warning" and "stale" in runtime["summary"]


def test_doctor_reports_continuation_observation_without_claiming_hosted_root_cause() -> None:
    result = doctor_snapshot(
        runtime_health={"health_status": "healthy", "resource_pressure": "normal", "unique_tool_names": True},
        transport={
            "diagnosis": "UPSTREAM_IDLE", "severity": "info",
            "continuation": {"state": "JOB_FINISHED_NO_NEXT_POLL_OBSERVED"},
        },
        system={"memory": {"percent": 20}, "disk": {"percent": 20}},
        tunnel={"mode": "tunnel"},
    )
    observation = next(item for item in result["checks"] if item["name"] == "Continuation")
    assert observation["status"] == "warning"
    assert "not observed" in observation["summary"].lower()
    assert "chatgpt bug" not in str(observation).lower()


def test_redact_diagnostics_removes_sensitive_keys_and_literal_secrets() -> None:
    result = redact_diagnostics(
        {
            "api_key": "top-secret",
            "nested": {"authorization": "Bearer top-secret"},
            "message": "failed with top-secret",
            "safe": "visible",
        },
        secret_values=("top-secret",),
    )

    assert result["api_key"] == "[redacted]"
    assert result["nested"]["authorization"] == "[redacted]"
    assert result["message"] == "failed with [redacted]"
    assert result["safe"] == "visible"


def test_latest_job_observation_reads_metadata_without_mutating_db(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "jobs.sqlite3"
    assert panel_data.latest_job_observation(path) is None
    assert not path.exists()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE jobs (status TEXT, updated REAL, exit_code INTEGER, spec TEXT)")
        db.execute(
            "INSERT INTO jobs VALUES ('succeeded', 100, 0, 'private-command-and-secret')"
        )
        db.execute(
            "INSERT INTO jobs VALUES ('failed', 300, 1, 'private-prompt-and-token')"
        )
    result = panel_data.latest_job_observation(path)
    assert result == {"status": "failed", "updated": 300.0, "exit_code": 1}
    assert "secret" not in str(result)
    assert "token" not in str(result)


def test_latest_job_observation_prioritizes_live_worker_over_newer_terminal_row(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE jobs (status TEXT, updated REAL, exit_code INTEGER)")
        db.execute("INSERT INTO jobs VALUES ('running', 100, NULL)")
        db.execute("INSERT INTO jobs VALUES ('failed', 200, 1)")
    assert panel_data.latest_job_observation(path) == {
        "status": "running", "updated": 100.0, "exit_code": None,
    }




def test_latest_job_observation_closes_sqlite_connection_even_after_repeated_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3
    from types import TracebackType

    path = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE jobs (status TEXT, updated REAL, exit_code INTEGER)")
        db.execute("INSERT INTO jobs VALUES ('succeeded', 1.0, 0)")

    raw_connect = sqlite3.connect

    class ConnectionSpy:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection
            self.closed = False

        def __getattr__(self, name: str) -> object:
            return getattr(self.connection, name)

        def __enter__(self) -> sqlite3.Connection:
            return self.connection.__enter__()

        def __exit__(
            self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None,
        ) -> bool | None:
            return self.connection.__exit__(exc_type, exc, tb)

        def close(self) -> None:
            self.closed = True
            self.connection.close()

    spies: list[ConnectionSpy] = []

    def connect(database: str, *, uri: bool = False, timeout: float = 5.0) -> ConnectionSpy:
        spy = ConnectionSpy(raw_connect(database, uri=uri, timeout=timeout))
        spies.append(spy)
        return spy

    monkeypatch.setattr(panel_data.sqlite3, "connect", connect)
    try:
        for _ in range(12):
            result = panel_data.latest_job_observation(path)
            assert result is not None and result["status"] == "succeeded"
        assert len(spies) == 12
        assert all(spy.closed for spy in spies)
    finally:
        for spy in spies:
            spy.close()

def test_latest_job_observation_invalid_schema_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    path.write_text("not a sqlite database", encoding="utf-8")
    assert panel_data.latest_job_observation(path) is None
