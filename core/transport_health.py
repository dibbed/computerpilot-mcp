"""Pure classification helpers for Secure Tunnel transport health snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

TRANSPORT_SCHEMA_VERSION = 1


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _epoch(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _component(payload: dict[str, Any], name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    components = _mapping(payload.get("components"))
    component = _mapping(components.get(name))
    return component, _mapping(component.get("details"))


def _age(now: float, value: Any) -> float | None:
    timestamp = _epoch(value)
    return max(now - timestamp, 0.0) if timestamp is not None else None


def classify_transport_health(
    payload: dict[str, Any] | None,
    *,
    tool_activity: dict[str, Any] | None,
    ready: bool | None,
    tunnel_mode: bool,
    now: float,
    poll_stall_grace_sec: float,
    upstream_idle_sec: float,
    mcp_tool_stall_sec: float,
) -> dict[str, Any]:
    """Return a compact diagnosis without inventing transport state.

    Only a confirmed stale control-plane poll is marked restart-recommended.
    Queue pressure, dispatcher issues, response delivery failures, and upstream
    idleness remain distinct so a local watchdog cannot hide the real fault.
    """

    activity = _mapping(tool_activity)
    base: dict[str, Any] = {
        "schema_version": TRANSPORT_SCHEMA_VERSION,
        "observed_at": now,
        "tunnel_mode": tunnel_mode,
        "ready": ready,
        "restart_recommended": False,
        "tool_activity": activity,
    }
    if not tunnel_mode:
        return {
            **base,
            "diagnosis": "LOCAL_MODE",
            "severity": "ok",
            "reason": "Secure Tunnel transport is not active in local HTTP mode.",
        }
    if payload is None:
        return {
            **base,
            "diagnosis": "UNKNOWN",
            "severity": "warn",
            "reason": "Detailed Secure Tunnel health is unavailable; readiness alone is not enough to classify polling.",
        }

    control, control_details = _component(payload, "control-plane")
    queue, queue_details = _component(payload, "queue")
    dispatcher, dispatcher_details = _component(payload, "dispatcher")
    response, response_details = _component(payload, "response-delivery")
    runtime = _mapping(payload.get("runtime"))

    poll_age = _number(control_details.get("current_poll_age_seconds"))
    deadline = _number(control_details.get("deadline_seconds"))
    effective_wait = _number(control_details.get("effective_wait_seconds"))
    configured_wait = _number(control_details.get("configured_wait_seconds"))
    threshold_base = max(
        [value for value in (deadline, effective_wait, configured_wait) if value is not None],
        default=30.0,
    )
    poll_stall_threshold = threshold_base + max(poll_stall_grace_sec, 0.0)
    consecutive_failures = _integer(control_details.get("consecutive_failures")) or 0

    queue_depth = _integer(queue_details.get("depth")) or 0
    queue_capacity = _integer(queue_details.get("capacity"))
    queue_full = queue_capacity is not None and queue_capacity > 0 and queue_depth >= queue_capacity
    queue_state = str(queue.get("state") or "")
    queue_status = str(queue.get("status") or "")
    queue_pressured = queue_full or queue_state not in {"", "available"} or queue_status not in {"", "ok"}

    dispatcher_status = str(dispatcher.get("status") or "")
    dispatcher_state = str(dispatcher.get("state") or "")
    accepting = dispatcher_details.get("accepting")
    dispatcher_bad = (
        dispatcher_status not in {"", "ok"}
        or dispatcher_state not in {"", "accepting"}
        or accepting is False
    )

    response_status = str(response.get("status") or "")
    response_state = str(response.get("state") or "")
    response_bad = response_status not in {"", "ok"} or response_state in {
        "failed",
        "stalled",
        "rejected",
        "unavailable",
    }

    control_status = str(control.get("status") or "")
    control_state = str(control.get("state") or "")
    poll_stale = poll_age is not None and poll_age > poll_stall_threshold
    control_backoff = (
        consecutive_failures > 0
        or control_status not in {"", "ok"}
        or control_state not in {"", "polling"}
    )

    active_calls = _integer(activity.get("active_calls")) or 0
    active_age = _age(now, activity.get("oldest_active_started_at"))
    mcp_stalled = active_calls > 0 and active_age is not None and active_age > mcp_tool_stall_sec

    last_enqueue_age = _age(now, queue_details.get("last_enqueue"))
    last_tool_age = _age(now, activity.get("last_received_at"))
    idle_candidates = [value for value in (last_enqueue_age, last_tool_age) if value is not None]
    upstream_idle_age = min(idle_candidates) if idle_candidates else None
    dispatcher_active = _integer(dispatcher_details.get("active")) or 0
    response_in_progress = _integer(response_details.get("in_progress")) or 0
    upstream_idle = (
        upstream_idle_age is not None
        and upstream_idle_age >= upstream_idle_sec
        and queue_depth == 0
        and dispatcher_active == 0
        and response_in_progress == 0
        and not control_backoff
        and not poll_stale
    )

    diagnosis = "HEALTHY"
    severity = "ok"
    reason = "Control-plane polling and local transport components are healthy."
    restart_recommended = False
    if ready is False:
        diagnosis = "TUNNEL_UNAVAILABLE"
        severity = "bad"
        reason = "The Secure Tunnel readiness endpoint is not healthy."
    elif queue_pressured:
        diagnosis = "QUEUE_BACKPRESSURE"
        severity = "warn"
        reason = "The Secure Tunnel queue is applying backpressure; polling may pause intentionally."
    elif response_bad:
        diagnosis = "RESPONSE_DELIVERY_STALLED"
        severity = "bad"
        reason = "The response-delivery component is not in a healthy accepted state."
    elif dispatcher_bad:
        diagnosis = "DISPATCH_STALLED"
        severity = "bad"
        reason = "The dispatcher is not accepting work normally."
    elif poll_stale:
        diagnosis = "POLL_STALLED"
        severity = "bad"
        reason = (
            f"Control-plane poll age {poll_age:.1f}s exceeded the derived "
            f"{poll_stall_threshold:.1f}s stall threshold."
        )
        restart_recommended = True
    elif control_backoff:
        diagnosis = "CONTROL_PLANE_BACKOFF"
        severity = "warn"
        reason = "Control-plane polling reports failures/backoff but has not crossed the stall threshold."
    elif mcp_stalled:
        diagnosis = "MCP_STALLED"
        severity = "warn"
        reason = "At least one MCP tool call has remained active beyond the configured diagnostic threshold."
    elif upstream_idle:
        diagnosis = "UPSTREAM_IDLE"
        severity = "info"
        reason = "Polling is healthy, but no new upstream command has reached the local transport recently."

    return {
        **base,
        "diagnosis": diagnosis,
        "severity": severity,
        "reason": reason,
        "restart_recommended": restart_recommended,
        "runtime": {
            "instance_id": runtime.get("instance_id"),
            "version": runtime.get("version"),
            "uptime_seconds": _number(runtime.get("uptime_seconds")),
            "lifecycle": runtime.get("lifecycle"),
        },
        "control_plane": {
            "status": control_status or None,
            "state": control_state or None,
            "last_attempt": control_details.get("last_attempt"),
            "last_success": control_details.get("last_success"),
            "last_error": control_details.get("last_error"),
            "consecutive_failures": consecutive_failures,
            "current_poll_age_seconds": poll_age,
            "configured_wait_seconds": configured_wait,
            "effective_wait_seconds": effective_wait,
            "deadline_seconds": deadline,
            "stall_threshold_seconds": poll_stall_threshold,
        },
        "queue": {
            "status": queue_status or None,
            "state": queue_state or None,
            "depth": queue_depth,
            "capacity": queue_capacity,
            "last_enqueue": queue_details.get("last_enqueue"),
            "last_dequeue": queue_details.get("last_dequeue"),
            "backpressure_seconds": _number(queue_details.get("backpressure_seconds")),
        },
        "dispatcher": {
            "status": dispatcher_status or None,
            "state": dispatcher_state or None,
            "active": dispatcher_active,
            "pool_limit": _integer(dispatcher_details.get("pool_limit")),
            "last_start": dispatcher_details.get("last_start"),
            "last_completion": dispatcher_details.get("last_completion"),
            "oldest_active_age_seconds": _number(dispatcher_details.get("oldest_active_age_seconds")),
            "accepting": accepting if isinstance(accepting, bool) else None,
        },
        "response_delivery": {
            "status": response_status or None,
            "state": response_state or None,
            "in_progress": response_in_progress,
            "last_accepted": response_details.get("last_accepted"),
            "last_completed": response_details.get("last_completed"),
            "disposition": response_details.get("disposition"),
            "http_status": _integer(response_details.get("http_status")),
        },
        "diagnostic_ages": {
            "last_enqueue_seconds": last_enqueue_age,
            "last_tool_received_seconds": last_tool_age,
            "oldest_active_tool_seconds": active_age,
            "upstream_idle_seconds": upstream_idle_age,
        },
    }
