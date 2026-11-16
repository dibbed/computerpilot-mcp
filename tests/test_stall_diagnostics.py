"""Behavior-first tests for observable MCP/ChatGPT continuation boundaries."""

from __future__ import annotations

from copy import deepcopy

import pytest

from core.stall_diagnostics import classify_continuation_state

NOW = 2_000_000_000.0
GEN = "test-generation"


def _case(*, idle: float = 1_200) -> tuple[dict[str, object], dict[str, object]]:
    activity: dict[str, object] = {
        "generation_id": GEN,
        "active_calls": 0,
        "last_received_at": NOW - idle,
        "last_completed_at": NOW - idle + 1,
    }
    transport: dict[str, object] = {
        "observed_at": NOW,
        "diagnosis": "UPSTREAM_IDLE",
        "ready": True,
        "tunnel_mode": True,
        "restart_recommended": False,
        "tool_activity": deepcopy(activity),
        "control_plane": {"status": "ok", "state": "polling", "consecutive_failures": 0},
        "queue": {"depth": 0},
        "dispatcher": {"active": 0, "accepting": True},
        "response_delivery": {
            "status": "ok", "state": "accepted", "disposition": "accepted",
            "http_status": 200, "in_progress": 0, "last_accepted": NOW - idle + 2,
        },
        "diagnostic_ages": {"upstream_idle_seconds": idle},
    }
    return transport, activity


def _classify(
    transport: dict[str, object] | None, activity: dict[str, object] | None,
    job: dict[str, object] | None = None,
) -> dict[str, object]:
    return classify_continuation_state(
        transport=transport, tool_activity=activity, job_summary=job,
        now=NOW, idle_threshold_sec=300,
    )


def test_accepted_response_and_twenty_minute_gap_is_upstream_idle_not_transport_failure() -> None:
    transport, activity = _case()
    result = _classify(transport, activity)
    assert result["state"] == "UPSTREAM_IDLE_AFTER_ACCEPTED_RESPONSE"
    assert result["restart_recommended"] is False
    assert result["next_poll_observed"] is False
    assert "consumed" not in str(result).lower()


def test_finished_failed_job_without_followup_is_observed_not_replayed() -> None:
    transport, activity = _case()
    result = _classify(transport, activity, {"status": "failed", "updated": NOW - 900, "exit_code": 1})
    assert result["state"] == "JOB_FINISHED_NO_NEXT_POLL_OBSERVED"
    assert result["job_status"] == "failed"
    assert result["job_exit_code"] == 1
    assert result["next_poll_observed"] is False


@pytest.mark.parametrize("status", ["running", "orphaned"])
def test_active_job_takes_precedence_over_upstream_idle(status: str) -> None:
    transport, activity = _case()
    result = _classify(transport, activity, {"status": status, "updated": NOW - 400})
    assert result["state"] == "JOB_ACTIVE"


def test_queued_without_proven_resource_phase_is_not_classified_as_hosted_stall() -> None:
    transport, activity = _case()
    result = _classify(transport, activity, {"status": "queued", "updated": NOW - 400})
    assert result["state"] == "UNKNOWN"


def test_resource_blocked_job_is_distinct_from_hosted_stall() -> None:
    transport, activity = _case()
    result = _classify(
        transport, activity, {"status": "queued", "queue_phase": "waiting_resources", "updated": NOW - 30},
    )
    assert result["state"] == "JOB_WAITING_RESOURCES"


def test_live_local_tool_wins_even_when_gap_is_long() -> None:
    transport, activity = _case(idle=4_200)
    activity["active_calls"] = 1
    activity["oldest_active_started_at"] = NOW - 4_200
    result = _classify(transport, activity)
    assert result["state"] == "LOCAL_CALL_ACTIVE"
    assert result["restart_recommended"] is False


@pytest.mark.parametrize("disposition,status", [("failed", 503), ("deadline_dropped", None)])
def test_delivery_failure_is_uncertain_not_safe_to_replay(disposition: str, status: int | None) -> None:
    transport, activity = _case()
    transport["response_delivery"] = {"status": "error", "disposition": disposition, "http_status": status}
    result = _classify(transport, activity)
    assert result["state"] == "TRANSPORT_RESPONSE_UNCERTAIN"
    assert result["safe_to_replay"] is False


@pytest.mark.parametrize("idle", [305, 1_800, 4_200])
def test_gap_length_not_fixed_to_one_hour(idle: float) -> None:
    transport, activity = _case(idle=idle)
    assert _classify(transport, activity)["state"] == "UPSTREAM_IDLE_AFTER_ACCEPTED_RESPONSE"


def test_generation_mismatch_never_correlates_two_sessions() -> None:
    transport, activity = _case()
    activity["generation_id"] = "replacement-generation"
    assert _classify(transport, activity)["state"] == "UNKNOWN"


def test_not_observed_delivery_is_not_accepted() -> None:
    transport, activity = _case()
    transport["response_delivery"] = {"status": "unknown", "disposition": "not_observed", "http_status": None}
    assert _classify(transport, activity)["state"] == "UNKNOWN"


@pytest.mark.parametrize("bad_time", [float("nan"), NOW + 900, -1])
def test_invalid_or_future_tool_timestamp_is_unknown(bad_time: float) -> None:
    transport, activity = _case()
    activity["last_received_at"] = bad_time
    assert _classify(transport, activity)["state"] == "UNKNOWN"


def test_recent_followup_prevents_false_no_poll_label() -> None:
    transport, activity = _case()
    activity["last_received_at"] = NOW - 400
    transport["diagnostic_ages"] = {"upstream_idle_seconds": 400}
    job = {"status": "succeeded", "updated": NOW - 900, "exit_code": 0}
    assert _classify(transport, activity, job)["state"] != "JOB_FINISHED_NO_NEXT_POLL_OBSERVED"


def test_missing_transport_or_fresh_request_remains_unknown() -> None:
    transport, activity = _case()
    assert _classify(None, activity)["state"] == "UNKNOWN"
    activity["last_received_at"] = NOW - 10
    assert _classify(transport, activity)["state"] == "UNKNOWN"



@pytest.mark.parametrize("updated", [float("nan"), -1.0])
def test_corrupt_terminal_job_timestamp_does_not_yield_upstream_claim(updated: float) -> None:
    transport, activity = _case()
    result = _classify(transport, activity, {"status": "failed", "updated": updated, "exit_code": 1})
    assert result["state"] == "UNKNOWN"


def test_timeline_metadata_is_bounded_and_temporal_only() -> None:
    transport, activity = _case()
    job = {"status": "failed", "updated": NOW - 900, "exit_code": 1, "spec": "private-password"}
    result = _classify(transport, activity, job)
    assert result["last_tool_received_at"] == NOW - 1200
    assert result["job_terminal_at"] == NOW - 900
    assert result["terminal_response_accepted_at"] == NOW - 1198
    assert result["correlation"] == "temporal_only"
    assert "private-password" not in str(result)
