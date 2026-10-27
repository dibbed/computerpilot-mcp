"""No-network guards for the optional real-model profile evaluation runner."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.profile_eval_live import (
    PERMISSION_RULES,
    READ_ONLY_TOOLS,
    parse_model_events,
    read_runtime_tool_events,
    scoped_read_only_permissions,
    validate_isolated_mcp,
)


def test_permission_allowlist_is_read_only() -> None:
    assert len(READ_ONLY_TOOLS) == 7
    assert not {"write_file", "apply_patch", "git_commit", "run_process", "run_powershell"} & READ_ONLY_TOOLS
    assert len(PERMISSION_RULES) == len(READ_ONLY_TOOLS)
    assert not any("*" in rule for rule in PERMISSION_RULES)


def test_temporary_settings_restore_byte_for_byte_even_on_failure(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    original = b'{  "model": "test", "permissions": {"deny": ["unsafe"]}}\n'
    settings.write_bytes(original)
    with pytest.raises(RuntimeError, match="inside"):
        with scoped_read_only_permissions(settings):
            changed = json.loads(settings.read_bytes())
            assert changed["permissions"]["allow"] == list(PERMISSION_RULES)
            assert changed["permissions"]["deny"] == ["unsafe"]
            raise RuntimeError("inside")
    assert settings.read_bytes() == original


def test_temporary_settings_preserve_existing_grant(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    prior = json.dumps({"permissions": {"allow": [PERMISSION_RULES[0], "existing"]}})
    settings.write_text(prior, encoding="utf8")
    with scoped_read_only_permissions(settings):
        assert all(rule in json.loads(settings.read_bytes())["permissions"]["allow"] for rule in PERMISSION_RULES)
    assert settings.read_text(encoding="utf8") == prior


def test_tool_identity_must_be_explicit_not_inferred_from_response() -> None:
    lines: list[dict[str, Any]] = [
        {"event": "step", "step_update": {"tool_name": "call_mcp_tool", "duration_seconds": 0.5,
                                           "tool_info": {"name": "call_mcp_tool"}}},
        {"event": "result", "result": {"status": "SUCCESS", "response": "I used project_summary",
                                        "usage": {"input_tokens": 120, "output_tokens": 8}}},
    ]
    observed = parse_model_events("\n".join(json.dumps(x) for x in lines))
    assert observed["observed_tool_names"] == []
    assert observed["ambiguous_tool_calls"] == 1
    assert observed["input_tokens"] == 120
    assert observed["terminal_status"] == "SUCCESS"
    lines[0]["step_update"]["tool_info"]["name"] = "project_summary"
    observed = parse_model_events("\n".join(json.dumps(x) for x in lines))
    assert observed["observed_tool_names"] == []  # Bare AGY name is not MCP identity.
    lines[0]["step_update"]["tool_info"]["server_name"] = "computerpilot"
    observed = parse_model_events("\n".join(json.dumps(x) for x in lines))
    assert observed["observed_tool_names"] == ["project_summary"]




def test_runtime_trace_proves_actual_mcp_dispatch_without_agy_name(tmp_path: Path) -> None:
    timings = tmp_path / ".agent_state" / "timings.jsonl"
    timings.parent.mkdir()
    old = {"phase": "request_pipeline", "method": "tools/call", "tool": "read_file", "ok": True}
    timings.write_text(json.dumps(old) + "\n", encoding="utf8")
    baseline = timings.stat().st_size
    lines = [
        {"phase": "validation", "tool": "project_summary", "ok": True},
        {"phase": "request_pipeline", "method": "tools/call", "tool": "project_summary",
         "ok": True, "correlation_id": "test-1"},
        {"phase": "serialization", "method": "tools/call", "tool": "project_summary", "ok": True},
    ]
    with timings.open("a", encoding="utf8") as out:
        for row in lines:
            out.write(json.dumps(row) + "\n")
    observed = read_runtime_tool_events(tmp_path, baseline)
    assert observed == ["project_summary"]
    assert read_runtime_tool_events(tmp_path, timings.stat().st_size) == []


def test_untrusted_snapshot_config_is_denied(tmp_path: Path) -> None:
    ws = tmp_path / "full"
    (ws / ".agents").mkdir(parents=True)
    (ws / ".agents" / "mcp_config.json").write_text(
        json.dumps({"mcpServers": {"computerpilot": {"command": "malware.exe"}}}), encoding="utf8",
    )
    with pytest.raises(ValueError, match="untrusted_mcp_command"):
        validate_isolated_mcp(ws, "full")
