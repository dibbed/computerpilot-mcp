"""Bounded safe concurrency for explicitly read-only terminal commands."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path

import pytest
from mcp import Client

from core import command_concurrency, config
from core.command_concurrency import SHARED_COMMANDS
from core.errors import ToolError
from core.registry import create_server


def test_gate_bounds_parallel_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        command_concurrency, "SETTINGS", replace(command_concurrency.SETTINGS, command_shared_concurrency=1),
    )
    with SHARED_COMMANDS.slot(5.0):
        with pytest.raises(ToolError) as raised:
            with SHARED_COMMANDS.slot(0.2):
                pass
    assert raised.value.code == "command_concurrency_timeout"
    assert SHARED_COMMANDS.active == 0


def test_read_only_commands_overlap_while_exclusive_commands_serialize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, state_dir=tmp_path))

    async def scenario() -> None:
        async with Client(create_server()) as client:
            async def shell(**arguments: object) -> None:
                await client.call_tool(
                    "run_shell",
                    {"command_text": "sleep 0.5", "cwd": str(tmp_path), "timeout_sec": 60, **arguments},
                )

            started = time.monotonic()
            await asyncio.gather(*(shell(access="read") for _ in range(3)))
            shared_elapsed = time.monotonic() - started
            assert shared_elapsed < 2.0, f"read-only commands serialized: {shared_elapsed:.2f}s"

            started = time.monotonic()
            await asyncio.gather(*(shell() for _ in range(3)))
            exclusive_elapsed = time.monotonic() - started
            assert exclusive_elapsed >= 1.4, f"exclusive commands overlapped unsafely: {exclusive_elapsed:.2f}s"

    asyncio.run(scenario())


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX shell writes via sh")
def test_read_only_command_still_conflicts_with_an_exclusive_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, state_dir=tmp_path))

    async def scenario() -> None:
        async with Client(create_server()) as client:
            reader = asyncio.create_task(
                client.call_tool(
                    "run_shell",
                    {
                        "command_text": "sleep 1.2",
                        "cwd": str(tmp_path),
                        "timeout_sec": 60,
                        "access": "read",
                    },
                )
            )
            await asyncio.sleep(0.3)
            started = time.monotonic()
            await client.call_tool(
                "run_shell",
                {"command_text": "echo writer", "cwd": str(tmp_path), "timeout_sec": 60},
            )
            waited = time.monotonic() - started
            await asyncio.wait_for(reader, timeout=30)
            assert waited >= 0.7, "exclusive writer was admitted while a read claim was active"

    asyncio.run(scenario())
