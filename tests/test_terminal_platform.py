from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from core.registry import create_server


def _tool_names() -> set[str]:
    return {tool.name for tool in asyncio.run(create_server().list_tools())}


def test_platform_shell_registration_matches_host() -> None:
    names = _tool_names()
    if os.name == "nt":
        assert "run_cmd" in names
        assert "run_shell" not in names
    else:
        assert "run_shell" in names
        assert "run_cmd" not in names


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell test requires POSIX")
def test_run_shell_executes_with_explicit_shell(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Client(create_server()) as client:
            result = await client.call_tool(
                "run_shell",
                {"command_text": "printf 'portable-shell'", "cwd": str(tmp_path)},
            )
            assert result.structured_content
            payload = result.structured_content
            assert payload["ok"] is True
            assert payload["stdout"]["text"] == "portable-shell"
            assert payload["process_ownership"] == "posix_process_group"

    asyncio.run(scenario())


def test_run_process_remains_shell_free(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Client(create_server()) as client:
            result = await client.call_tool(
                "run_process",
                {
                    "executable": sys.executable,
                    "args": ["-c", "print('direct')"],
                    "cwd": str(tmp_path),
                },
            )
            assert result.structured_content
            assert result.structured_content["stdout"]["text"].strip() == "direct"

    asyncio.run(scenario())


def test_public_command_cleanup_denial_keeps_durable_fence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import config
    from core.errors import ToolError
    from core.resource_claims import coordination_claim, path_claim
    from core.workflow_store import WorkflowStore
    from tools.terminal import registry

    monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, state_dir=tmp_path))

    def failed(*args: object, **kwargs: object) -> None:
        raise ToolError("process_cleanup_incomplete", "owned tree cleanup denied")

    monkeypatch.setattr(registry, "run_bounded", failed)

    async def scenario() -> None:
        async with Client(create_server()) as client:
            result = await client.call_tool("run_process", {"executable": sys.executable, "cwd": str(tmp_path)})
            assert result.structured_content
            assert result.structured_content["error"] == "process_cleanup_incomplete"

    asyncio.run(scenario())
    resources = WorkflowStore(config.SETTINGS.workflow_db, recover=False)
    conflict = resources.reserve_resource_claims(
        request_id="conflict", owner_token="other", category="test",
        claims=(coordination_claim(), path_claim(tmp_path / "target", "write")),
    )
    assert conflict["admitted"] is False
    assert conflict["reason"] == "resource_quarantined"


@pytest.mark.parametrize("tool", ["run_process", "run_powershell", "run_cmd" if os.name == "nt" else "run_shell"])
def test_command_timeout_bounds_resource_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str,
) -> None:
    from core import config
    from core.resource_claims import coordination_claim, managed_resource_claims
    from core.workflow_store import WorkflowStore
    from tools.terminal import registry

    monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, state_dir=tmp_path))
    store = WorkflowStore(config.SETTINGS.workflow_db, recover=False)
    store.reserve_resource_claims(
        request_id="busy", owner_token="owner", category="test", claims=(coordination_claim(),),
    )

    @contextmanager
    def bounded_claims(*args: Any, **kwargs: Any) -> Iterator[str]:
        assert kwargs["timeout_sec"] <= 0.1
        with managed_resource_claims(*args, **kwargs) as capability:
            yield capability

    monkeypatch.setattr(registry, "managed_resource_claims", bounded_claims)
    monkeypatch.setattr(registry, "run_bounded", lambda *a, **k: pytest.fail("spawned while admission blocked"))

    async def scenario() -> None:
        async with Client(create_server()) as client:
            arguments = {"cwd": str(tmp_path), "timeout_sec": 0.1}
            if tool == "run_process":
                arguments["executable"] = sys.executable
            else:
                arguments["script" if tool == "run_powershell" else "command_text"] = "echo probe"
            result = await client.call_tool(tool, arguments)
            assert result.structured_content
            assert result.structured_content["error"] == "resource_admission_timeout"

    started = time.monotonic()
    asyncio.run(scenario())
    assert time.monotonic() - started < 5
    assert store.health_summary()["pending_resource_request_count"] == 0


def test_command_execution_uses_remaining_admission_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from tools.terminal import registry

    clock = [0.0]

    @contextmanager
    def delayed_claims(*args: Any, **kwargs: Any) -> Iterator[str]:
        assert kwargs["timeout_sec"] == 10
        clock[0] += 4
        yield "admitted"

    monkeypatch.setattr(registry, "managed_resource_claims", delayed_claims)
    monkeypatch.setattr(registry, "time", SimpleNamespace(monotonic=lambda: clock[0]), raising=False)
    with registry._managed_command_claims(tmp_path, 10) as remaining:
        assert remaining == 6
