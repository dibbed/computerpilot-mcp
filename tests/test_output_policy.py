from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from core import artifacts
from core.registry import create_server


@pytest.fixture(autouse=True)
def output_policy_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "SETTINGS", replace(artifacts.SETTINGS, state_dir=tmp_path))
    for name in (
        "MCP_OUTPUT_DEFAULT",
        "MCP_INLINE_SOFT_LIMIT_BYTES",
        "MCP_INLINE_HARD_LIMIT_BYTES",
        "MCP_PREVIEW_BYTES",
    ):
        monkeypatch.delenv(name, raising=False)


def test_feature_flag_defaults_to_legacy_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    assert artifacts.default_delivery() == "inline"
    monkeypatch.setenv("MCP_OUTPUT_DEFAULT", "auto")
    assert artifacts.default_delivery() == "auto"
    monkeypatch.setenv("MCP_OUTPUT_DEFAULT", "invalid")
    with pytest.raises(ValueError, match="legacy-inline or auto"):
        artifacts.default_delivery()


def test_auto_small_output_stays_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_INLINE_SOFT_LIMIT_BYTES", "64")
    result = artifacts.deliver_text("small output", "auto")
    assert result["delivery"] == "inline"
    assert result["text"] == "small output"
    assert "path" not in result


def test_auto_utf8_preview_cursor_roundtrips_without_losing_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_INLINE_SOFT_LIMIT_BYTES", "16")
    monkeypatch.setenv("MCP_PREVIEW_BYTES", "32")
    text = "سلام دنیا 😀 " * 20
    source = tmp_path / "unicode.txt"
    source.write_bytes(text.encode("utf-8"))

    result = artifacts.deliver_file(source, encoding="utf-8", delivery="auto")
    assert result["delivery"] == "preview"
    assert result["preview_bytes"] <= 32
    assert result["preview"]["head"]
    assert result["preview"]["tail"]
    cursor = result["cursor"]["next_byte"]
    assert cursor == result["next_byte"]
    assert 0 < cursor < result["total_bytes"]

    remainder = artifacts.deliver_file(source, encoding="utf-8", offset=cursor, delivery="inline")
    assert result["preview"]["head"] + remainder["text"] == text
    assert Path(result["artifact"]["path"]).read_bytes() == text.encode("utf-8")


def test_inline_hard_ceiling_is_explicit_and_auto_keeps_full_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_INLINE_HARD_LIMIT_BYTES", "32")
    payload = "x" * 64
    with pytest.raises(ValueError, match="HARD_LIMIT"):
        artifacts.deliver_text(payload, "inline")

    result = artifacts.deliver_text(payload, "auto")
    assert result["delivery"] == "preview"
    assert Path(result["artifact"]["path"]).read_bytes() == payload.encode()


def test_preview_budget_rejects_unsafe_tiny_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_PREVIEW_BYTES", "8")
    with pytest.raises(ValueError, match="at least 16"):
        artifacts.deliver_text("x", "auto")


def _tool_delivery_defaults() -> dict[str, str | None]:
    async def scenario() -> dict[str, str | None]:
        tools = await create_server().list_tools()
        wanted = {"run_process", "run_powershell", "run_cmd", "process_output", "job_output"}
        return {
            tool.name: tool.input_schema["properties"]["delivery"].get("default")
            for tool in tools
            if tool.name in wanted
        }

    return asyncio.run(scenario())


def test_mcp_tool_schema_migrates_only_when_feature_flag_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    assert set(_tool_delivery_defaults().values()) == {"inline"}
    monkeypatch.setenv("MCP_OUTPUT_DEFAULT", "auto")
    assert set(_tool_delivery_defaults().values()) == {"auto"}


def test_mcp_auto_default_bounds_large_response_and_keeps_full_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_OUTPUT_DEFAULT", "auto")
    monkeypatch.setenv("MCP_INLINE_SOFT_LIMIT_BYTES", "131072")
    monkeypatch.setenv("MCP_PREVIEW_BYTES", "65536")

    async def scenario() -> dict[str, object]:
        result = await create_server().call_tool(
            "run_process",
            {
                "executable": sys.executable,
                "args": ["-c", "import sys;sys.stdout.write('x'*1200000)"],
                "cwd": str(tmp_path),
                "timeout_sec": 30,
            },
        )
        structured = getattr(result, "structured_content", None)
        assert isinstance(structured, dict)
        return structured

    structured = asyncio.run(scenario())
    stdout = structured["stdout"]
    assert isinstance(stdout, dict)
    assert stdout["delivery"] == "file"
    assert stdout["preview_bytes"] <= 65_536
    assert Path(stdout["artifact"]["path"]).stat().st_size == 1_200_000
    assert len(json.dumps(structured, ensure_ascii=False).encode("utf-8")) < 100_000


def test_explicit_inline_still_returns_full_output_under_auto_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_OUTPUT_DEFAULT", "auto")

    async def scenario() -> dict[str, object]:
        result = await create_server().call_tool(
            "run_process",
            {
                "executable": sys.executable,
                "args": ["-c", "import sys;sys.stdout.write('z'*200000)"],
                "cwd": str(tmp_path),
                "timeout_sec": 30,
                "delivery": "inline",
            },
        )
        structured = getattr(result, "structured_content", None)
        assert isinstance(structured, dict)
        return structured

    stdout = asyncio.run(scenario())["stdout"]
    assert isinstance(stdout, dict)
    assert stdout["delivery"] == "inline"
    assert stdout["text"] == "z" * 200_000
