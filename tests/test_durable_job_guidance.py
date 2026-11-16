"""Durable-job guidance must respect the frozen MCP catalog contract."""

from __future__ import annotations

import asyncio
from pathlib import Path

from core.registry import create_server


def test_durable_guidance_uses_allowed_metadata_without_breaking_catalogue() -> None:
    async def scenario() -> None:
        server = create_server()
        tools = {tool.name: (tool.description or "").lower() for tool in await server.list_tools()}
        assert "start_verification" in tools
        assert tools["run_process"].startswith(
            "run an executable with a timeout and optional per-stream output limits."
        )
        assert "submit_job" in tools["run_process"]
        assert "job_wait" in tools["run_process"]
        assert "accepted is not success" in tools["submit_job"]
        assert "final" in tools["submit_job"]
        assert all(len(description) <= 180 for description in tools.values())

    asyncio.run(scenario())


def test_frozen_testing_tool_descriptions_have_durable_operational_guidance_in_docs() -> None:
    tooling = (Path(__file__).resolve().parents[1] / "docs" / "tooling.md").read_text(encoding="utf-8")
    troubleshooting = (
        Path(__file__).resolve().parents[1] / "docs" / "troubleshooting.md"
    ).read_text(encoding="utf-8")
    for name in ("start_verification", "run_pytest", "job_wait", "job_output"):
        assert name in tooling + troubleshooting
    assert "response deadline" in tooling.lower()
    assert "no later local" in troubleshooting.lower()
