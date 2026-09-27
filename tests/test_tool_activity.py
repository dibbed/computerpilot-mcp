from __future__ import annotations

from pathlib import Path

import pytest

from core.tool_activity import ToolActivityTracker


def _read_json(path: Path) -> dict[str, object]:
    import json

    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_tool_activity_publishes_start_and_completion(tmp_path: Path) -> None:
    path = tmp_path / "activity.json"
    tracker = ToolActivityTracker()
    tracker.configure(path, "generation-1")

    with tracker.track("server_health"):
        active = _read_json(path)
        assert active["generation_id"] == "generation-1"
        assert active["active_calls"] == 1
        assert active["total_calls"] == 1
        assert active["last_tool_name"] == "server_health"
        assert active["last_received_at"] is not None
        assert active["oldest_active_started_at"] is not None

    completed = _read_json(path)
    assert completed["active_calls"] == 0
    assert completed["total_calls"] == 1
    assert completed["last_completed_at"] is not None
    assert completed["last_duration_ms"] is not None
    assert completed["last_ok"] is True
    assert completed["oldest_active_started_at"] is None


def test_tool_activity_marks_exceptions_without_swallowing_them(tmp_path: Path) -> None:
    path = tmp_path / "activity.json"
    tracker = ToolActivityTracker()
    tracker.configure(path, "generation-2")

    with pytest.raises(RuntimeError, match="boom"):
        with tracker.track("failing_tool"):
            raise RuntimeError("boom")

    completed = _read_json(path)
    assert completed["active_calls"] == 0
    assert completed["last_tool_name"] == "failing_tool"
    assert completed["last_ok"] is False


def test_tool_activity_is_best_effort_when_unpublished() -> None:
    tracker = ToolActivityTracker()
    tracker.configure(None, None)

    with tracker.track("local_only"):
        snapshot = tracker.snapshot()
        assert snapshot["active_calls"] == 1

    snapshot = tracker.snapshot()
    assert snapshot["active_calls"] == 0
    assert snapshot["total_calls"] == 1
