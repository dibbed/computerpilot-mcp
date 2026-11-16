"""Conservative, read-only diagnosis of MCP job and hosted-turn continuation.

The local server cannot observe whether ChatGPT consumed a response.  A
healthy Tunnel with no subsequent request is an observation, not a claim
about the hosted model's root cause.  This module never retries work.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

CONTINUATION_SCHEMA_VERSION = 1
_TERMINAL_JOB_STATES = frozenset({"succeeded", "failed", "cancelled", "timed_out", "interrupted"})
_ACTIVE_JOB_STATES = frozenset({"running", "orphaned"})
_UNCERTAIN_DISPOSITIONS = frozenset({
    "failed", "rejected", "deadline_dropped", "dropped", "post_failed", "timed_out",
})


def _record(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _stamp(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        number = date.timestamp()
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, OverflowError, OSError):
        return None


def _count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    return None


def classify_continuation_state(
    *,
    transport: Mapping[str, Any] | None,
    tool_activity: Mapping[str, Any] | None,
    job_summary: Mapping[str, Any] | None,
    now: float,
    idle_threshold_sec: float,
) -> dict[str, Any]:
    """Classify what is locally observable, without restarting or executing.

    job_summary is the latest durable job observation, not proof of which
    conversation submitted it.  Correlation with hosted turns is heuristic.
    """

    result: dict[str, Any] = {
        "schema_version": CONTINUATION_SCHEMA_VERSION,
        "state": "UNKNOWN",
        "reason": "Insufficient local evidence to classify continuation.",
        "restart_recommended": False,
        "safe_to_replay": False,
        "next_poll_observed": None,
        "idle_seconds": None,
        "job_status": None,
        "job_exit_code": None,
        "last_tool_received_at": None,
        "job_terminal_at": None,
        "terminal_response_accepted_at": None,
        "correlation": "temporal_only",
    }

    def finish(state: str, reason: str, *, followup: bool | None = None) -> dict[str, Any]:
        result.update(state=state, reason=reason, next_poll_observed=followup)
        return result

    t = _record(transport)
    a = _record(tool_activity)
    j = _record(job_summary)
    if (
        not t or not a
        or not math.isfinite(now) or now < 0
        or not math.isfinite(idle_threshold_sec) or idle_threshold_sec <= 0
        or t.get("tunnel_mode") is not True
    ):
        return result

    observed = _stamp(t.get("observed_at"))
    received = _stamp(a.get("last_received_at"))
    if (
        observed is None or received is None
        or observed > now + 1 or received > now + 1
        or now - observed > max(60.0, idle_threshold_sec)
    ):
        return result

    embedded = _record(t.get("tool_activity"))
    if (
        embedded.get("generation_id") is not None
        and a.get("generation_id") is not None
        and embedded["generation_id"] != a["generation_id"]
    ):
        return result

    idle = max(0.0, now - received)
    result["idle_seconds"] = round(idle, 3)
    result["last_tool_received_at"] = received
    status = j.get("status")
    if isinstance(status, str):
        result["job_status"] = status
    if isinstance(j.get("exit_code"), int) and not isinstance(j["exit_code"], bool):
        result["job_exit_code"] = j["exit_code"]
    if status in _TERMINAL_JOB_STATES:
        terminal_at = _stamp(j.get("updated"))
        if terminal_at is None or terminal_at > now + 1:
            return result
        result["job_terminal_at"] = terminal_at

    active = _count(a.get("active_calls"))
    if active is None:
        return result
    if active > 0:
        return finish("LOCAL_CALL_ACTIVE", "An MCP tool is still executing locally.")
    if status in _ACTIVE_JOB_STATES:
        return finish("JOB_ACTIVE", "A durable job remains active; no hosted stall is inferred.")
    if status == "queued":
        if j.get("queue_phase") == "waiting_resources":
            return finish("JOB_WAITING_RESOURCES", "The job is queued behind managed resource claims.")
        return result  # Queued but blocker unknown: never assert hosted continuation failure.

    delivery = _record(t.get("response_delivery"))
    disposition = delivery.get("disposition")
    http = delivery.get("http_status")
    if disposition in _UNCERTAIN_DISPOSITIONS or (
        http is not None and http != 200
    ):
        return finish("TRANSPORT_RESPONSE_UNCERTAIN", "The last terminal response delivery is not confirmed.")
    if (
        t.get("ready") is not True
        or t.get("diagnosis") not in {"UPSTREAM_IDLE", "HEALTHY"}
        or _count(_record(t.get("queue")).get("depth")) != 0
        or _count(_record(t.get("dispatcher")).get("active")) != 0
        or _count(delivery.get("in_progress")) != 0
        or _record(t.get("control_plane")).get("consecutive_failures") != 0
        or delivery.get("status") != "ok"
        or disposition != "accepted"
        or http != 200
    ):
        return result

    accepted = _stamp(delivery.get("last_accepted"))
    if accepted is None or accepted > now + 1 or accepted < received - 2:
        return result
    result["terminal_response_accepted_at"] = accepted
    if idle < idle_threshold_sec or now - accepted < idle_threshold_sec:
        return result

    terminal_at = _stamp(j.get("updated"))
    if status in _TERMINAL_JOB_STATES and terminal_at is not None:
        if terminal_at > now + 1:
            return result
        if terminal_at > received and now - terminal_at >= idle_threshold_sec:
            return finish(
                "JOB_FINISHED_NO_NEXT_POLL_OBSERVED",
                "A durable job became terminal, with no later local MCP request observed.",
                followup=False,
            )

    return finish(
        "UPSTREAM_IDLE_AFTER_ACCEPTED_RESPONSE",
        "Tunnel delivery was accepted, but no later local MCP request was observed; hosted cause is unknown.",
        followup=False,
    )
