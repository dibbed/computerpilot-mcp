from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, cast

import pytest

from scripts.panel_data import extensions_snapshot
from scripts.supervisor import Supervisor, make_panel


def close_logger(supervisor: Supervisor) -> None:
    for logger in (supervisor.logger, supervisor.transport_logger):
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


def test_extensions_snapshot_pure_empty_and_disabled() -> None:
    """Validate empty/None health dictionary returns clean neutral projection."""
    none_result = extensions_snapshot(None)
    assert none_result["enabled"] is False
    assert none_result["status"] == "unavailable"
    assert none_result["items"] == []
    assert none_result["configured_count"] == 0

    empty_result = extensions_snapshot({})
    assert empty_result["enabled"] is False
    assert empty_result["status"] == "unavailable"
    assert empty_result["items"] == []
    assert empty_result["configured_count"] == 0


def test_extensions_snapshot_projects_bounded_data() -> None:
    """Validate bounded transformation of raw extension health."""
    sample_health: dict[str, Any] = {
        "ok": True,
        "health_status": "healthy",
        "snapshot_status": "fresh",
        "extensions": {
            "enabled": True,
            "configured_count": 1,
            "enabled_count": 1,
            "healthy_count": 1,
            "degraded_count": 0,
            "failed_count": 0,
            "unreachable_count": 0,
            "disabled_count": 0,
            "blocked_count": 0,
            "live_count": 1,
            "discovered_tool_count": 5,
            "approved_tool_count": 5,
            "eligible_tool_count": 5,
            "catalog_generation": 2,
            "observed_at": "2026-10-13T12:00:00Z",
            "unresolved_external_count": 0,
            "truncated": False,
            "items": [
                {
                    "extension_id": "test_ext",
                    "display_name": "Test Extension",
                    "provider": "test_provider",
                    "enabled": True,
                    "state": "healthy",
                    "trust_class": "trusted",
                    "live": True,
                    "routable": True,
                    "blocked": False,
                    "discovered_tool_count": 5,
                    "approved_tool_count": 5,
                    "eligible_tool_count": 5,
                    "owner_generation": 1,
                    "catalog_generation": 2,
                    "last_changed_at": "2026-10-13T11:59:00Z",
                    "last_success_at": "2026-10-13T12:00:00Z",
                    "state_age_sec": 60.0,
                    "last_error_code": None,
                    "last_error_at": None,
                    "restart_count": 0,
                    "calls": {"succeeded": 10, "failed": 0, "uncertain": 0, "not_dispatched": 0},
                    "call_window_size": 10,
                    "call_duration_mean_sec": 0.05,
                    "call_duration_max_sec": 0.12,
                }
            ],
        },
    }

    projected = extensions_snapshot(sample_health)
    assert projected["enabled"] is True
    assert projected["status"] == "healthy"
    assert projected["snapshot_status"] == "fresh"
    assert projected["configured_count"] == 1
    assert len(projected["items"]) == 1

    item = projected["items"][0]
    assert item["extension_id"] == "test_ext"
    assert item["display_name"] == "Test Extension"
    assert item["provider"] == "test_provider"
    assert item["live"] is True
    assert item["routable"] is True
    assert item["state"] == "healthy"
    assert item["calls"]["succeeded"] == 10


def test_api_extensions_endpoint_auth_and_host_validation(tmp_path: Path) -> None:
    """T13: Verify /api/extensions enforces host validation and authentication."""
    supervisor = Supervisor([], readiness_url=None, state_dir=tmp_path)
    panel = make_panel(supervisor, 0)
    thread = threading.Thread(target=panel.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{panel.server_port}"
    token = cast(str, panel.token)  # type: ignore[attr-defined]

    try:
        # 1. Invalid Host header -> 403
        req_bad_host = urllib.request.Request(
            base + "/api/extensions",
            headers={"Host": "evil.com", "X-Control-Token": token},
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req_bad_host, timeout=2)
        assert exc_info.value.code == 403

        # 2. Missing token -> 403
        req_no_token = urllib.request.Request(base + "/api/extensions")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req_no_token, timeout=2)
        assert exc_info.value.code == 403

        # 3. Wrong token -> 403
        req_wrong_token = urllib.request.Request(
            base + "/api/extensions",
            headers={"X-Control-Token": "invalid-token"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req_wrong_token, timeout=2)
        assert exc_info.value.code == 403

        # 4. Valid token -> 200
        req_valid = urllib.request.Request(
            base + "/api/extensions",
            headers={"X-Control-Token": token},
        )
        with urllib.request.urlopen(req_valid, timeout=2) as response:
            assert response.status == 200
            data = json.load(response)
        assert "enabled" in data
        assert "items" in data
        assert isinstance(data["items"], list)
    finally:
        panel.shutdown()
        thread.join(timeout=5)
        panel.server_close()
        close_logger(supervisor)


def test_api_status_and_summary_do_not_leak_extensions_details(tmp_path: Path) -> None:
    """T13: Verify legacy unauthenticated routes do not leak extension details/identities."""
    supervisor = Supervisor([], readiness_url=None, state_dir=tmp_path)
    panel = make_panel(supervisor, 0)
    thread = threading.Thread(target=panel.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{panel.server_port}"

    try:
        # /api/status without token -> 200
        with urllib.request.urlopen(base + "/api/status", timeout=2) as response:
            status = json.load(response)
        assert response.status == 200
        assert "extensions" not in status
        assert "items" not in status

        # /api/summary without token -> 200
        with urllib.request.urlopen(base + "/api/summary", timeout=2) as response:
            summary = json.load(response)
        assert response.status == 200
        assert "extensions" not in summary
        assert "items" not in summary

        # If extension_counts is present, it must ONLY contain numeric counts, no IDs/errors/schemas
        ext_counts = summary.get("extension_counts")
        if ext_counts is not None:
            assert isinstance(ext_counts, dict)
            for k, v in ext_counts.items():
                assert isinstance(v, (int, bool))
                assert k not in {"items", "extension_id", "schema", "provider", "last_error_code"}
    finally:
        panel.shutdown()
        thread.join(timeout=5)
        panel.server_close()
        close_logger(supervisor)


def test_panel_html_contains_extensions_section_and_is_readonly(tmp_path: Path) -> None:
    """T13: Verify panel HTML contains Extensions section, polling hook, and no management buttons."""
    supervisor = Supervisor([], readiness_url=None, state_dir=tmp_path)
    panel = make_panel(supervisor, 0)
    thread = threading.Thread(target=panel.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{panel.server_port}"

    try:
        with urllib.request.urlopen(base, timeout=2) as response:
            html = response.read().decode("utf-8")

        # Navigation and structure
        assert '<a href="#extensions">Extensions</a>' in html
        assert '<div id="extensions" class="section-anchor"><span>Extensions</span></div>' in html
        assert "Extensions / External MCPs" in html
        assert 'id="extensionsPill"' in html
        assert 'id="extStatus"' in html
        assert 'id="extConfigured"' in html
        assert 'id="extLiveRoutable"' in html
        assert 'id="extEligibleTools"' in html
        assert 'id="extCatalogGen"' in html
        assert 'id="extensionRows"' in html
        assert 'id="extensionEmpty"' in html

        # Script functions and polling
        assert "renderExtensions" in html
        assert "/api/extensions" in html

        # Section must be read-only: no buttons inside extensions panel
        start_idx = html.index('id="extensions"')
        end_idx = html.index('id="work"', start_idx)
        ext_section = html[start_idx:end_idx]
        assert "<button" not in ext_section
        assert "start" not in ext_section.lower() or "started" in ext_section.lower()
        assert "delete" not in ext_section.lower()
    finally:
        panel.shutdown()
        thread.join(timeout=5)
        panel.server_close()
        close_logger(supervisor)


def test_extensions_snapshot_sanitizes_html_and_script_injection() -> None:
    """T13: Ensure malicious strings in provider metadata are safely bounded and neutralized."""
    adversarial_health: dict[str, Any] = {
        "ok": True,
        "health_status": "healthy",
        "snapshot_status": "fresh",
        "extensions": {
            "enabled": True,
            "configured_count": 1,
            "items": [
                {
                    "extension_id": "<script>alert('xss')</script>",
                    "display_name": "<img src=x onerror=alert(1)>" * 20,
                    "provider": "<svg/onload=alert('mcp')>",
                    "enabled": True,
                    "state": "healthy",
                    "trust_class": "trusted",
                    "live": True,
                    "routable": True,
                    "blocked": False,
                    "last_error_code": "<b>danger</b>",
                }
            ],
        },
    }

    result = extensions_snapshot(adversarial_health)
    assert result["enabled"] is True
    assert len(result["items"]) == 1
    item = result["items"][0]
    # Lengths must be bounded
    assert len(item["extension_id"]) <= 64
    assert len(item["display_name"]) <= 180
    assert len(item["provider"]) <= 64
    assert item["state"] == "healthy"
    assert item["last_error_code"] == "<b>danger</b>"
