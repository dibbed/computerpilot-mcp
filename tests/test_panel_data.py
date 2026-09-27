from __future__ import annotations

import json
from pathlib import Path

from scripts.panel_data import (
    doctor_snapshot,
    redact_diagnostics,
    storage_breakdown,
    summarize_transport_history,
)


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
