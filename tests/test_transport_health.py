from __future__ import annotations

from datetime import datetime, timezone

from core.transport_health import classify_transport_health

NOW = 2_000_000_000.0


def _iso(age_seconds: float) -> str:
    return datetime.fromtimestamp(NOW - age_seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _payload(
    *,
    poll_age: float = 1.0,
    consecutive_failures: int = 0,
    control_state: str = "polling",
    control_status: str = "ok",
    queue_depth: int = 0,
    queue_capacity: int = 20,
    queue_state: str = "available",
    queue_status: str = "ok",
    dispatcher_status: str = "ok",
    dispatcher_state: str = "accepting",
    accepting: bool = True,
    dispatcher_active: int = 0,
    response_status: str = "ok",
    response_state: str = "accepted",
    response_in_progress: int = 0,
    last_enqueue_age: float = 1.0,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "live": True,
        "ready": True,
        "runtime": {
            "instance_id": "runtime-1",
            "version": "0.0.15",
            "uptime_seconds": 7200,
            "lifecycle": "running",
        },
        "components": {
            "control-plane": {
                "status": control_status,
                "state": control_state,
                "details": {
                    "last_attempt": _iso(poll_age),
                    "last_success": _iso(poll_age),
                    "last_error": None,
                    "consecutive_failures": consecutive_failures,
                    "current_poll_age_seconds": poll_age,
                    "configured_wait_seconds": 30,
                    "effective_wait_seconds": 30,
                    "deadline_seconds": 35,
                },
            },
            "queue": {
                "status": queue_status,
                "state": queue_state,
                "details": {
                    "depth": queue_depth,
                    "capacity": queue_capacity,
                    "last_enqueue": _iso(last_enqueue_age),
                    "last_dequeue": _iso(last_enqueue_age),
                    "backpressure_seconds": 0,
                },
            },
            "dispatcher": {
                "status": dispatcher_status,
                "state": dispatcher_state,
                "details": {
                    "active": dispatcher_active,
                    "pool_limit": 10,
                    "last_start": _iso(1),
                    "last_completion": _iso(1),
                    "oldest_active_age_seconds": 0,
                    "accepting": accepting,
                },
            },
            "response-delivery": {
                "status": response_status,
                "state": response_state,
                "details": {
                    "in_progress": response_in_progress,
                    "last_accepted": _iso(1),
                    "last_completed": _iso(1),
                    "disposition": "accepted",
                    "http_status": 200,
                },
            },
        },
    }


def _classify(
    payload: dict[str, object] | None,
    *,
    activity: dict[str, object] | None = None,
    ready: bool | None = True,
    tunnel_mode: bool = True,
) -> dict[str, object]:
    return classify_transport_health(
        payload,
        tool_activity=activity,
        ready=ready,
        tunnel_mode=tunnel_mode,
        now=NOW,
        poll_stall_grace_sec=10,
        upstream_idle_sec=300,
        mcp_tool_stall_sec=3600,
    )


def test_healthy_poll_is_not_restartable() -> None:
    result = _classify(_payload())
    assert result["diagnosis"] == "HEALTHY"
    assert result["restart_recommended"] is False
    assert result["control_plane"]["stall_threshold_seconds"] == 45.0  # type: ignore[index]


def test_upstream_idle_is_distinct_from_local_failure() -> None:
    activity: dict[str, object] = {
        "active_calls": 0,
        "last_received_at": NOW - 700,
        "last_completed_at": NOW - 699,
    }
    result = _classify(_payload(last_enqueue_age=700), activity=activity)
    assert result["diagnosis"] == "UPSTREAM_IDLE"
    assert result["severity"] == "info"
    assert result["restart_recommended"] is False


def test_poll_stall_uses_deadline_plus_grace() -> None:
    result = _classify(_payload(poll_age=46))
    assert result["diagnosis"] == "POLL_STALLED"
    assert result["restart_recommended"] is True


def test_queue_backpressure_takes_precedence_over_stale_poll() -> None:
    result = _classify(
        _payload(
            poll_age=90,
            queue_depth=20,
            queue_capacity=20,
            queue_state="full",
            queue_status="warning",
        )
    )
    assert result["diagnosis"] == "QUEUE_BACKPRESSURE"
    assert result["restart_recommended"] is False


def test_control_plane_backoff_without_stall_is_not_restartable() -> None:
    result = _classify(
        _payload(
            poll_age=10,
            consecutive_failures=2,
            control_state="backoff",
            control_status="warning",
        )
    )
    assert result["diagnosis"] == "CONTROL_PLANE_BACKOFF"
    assert result["restart_recommended"] is False


def test_dispatch_and_response_failures_remain_distinct() -> None:
    dispatch = _classify(
        _payload(dispatcher_status="error", dispatcher_state="stalled", accepting=False)
    )
    assert dispatch["diagnosis"] == "DISPATCH_STALLED"

    response = _classify(_payload(response_status="error", response_state="failed"))
    assert response["diagnosis"] == "RESPONSE_DELIVERY_STALLED"


def test_long_active_mcp_call_is_diagnostic_only() -> None:
    activity: dict[str, object] = {
        "active_calls": 1,
        "oldest_active_started_at": NOW - 4000,
        "last_received_at": NOW - 4000,
    }
    result = _classify(_payload(last_enqueue_age=4000), activity=activity)
    assert result["diagnosis"] == "MCP_STALLED"
    assert result["restart_recommended"] is False


def test_local_and_unknown_modes_fail_safe() -> None:
    local = _classify(None, ready=None, tunnel_mode=False)
    assert local["diagnosis"] == "LOCAL_MODE"
    assert local["restart_recommended"] is False

    unknown = _classify(None)
    assert unknown["diagnosis"] == "UNKNOWN"
    assert unknown["restart_recommended"] is False
