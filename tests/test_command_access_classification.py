"""Terminal coordination rejects caller-declared read access as a trust claim.

Arbitrary executables and shell strings can write through arguments, environment,
configuration, plugins or descendants, so ``access="read"`` is a compatibility
hint only. Every terminal command takes the exclusive host claim and reports the
effective classification; native typed readers keep their own read claims.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest
from mcp import Client

from core import config
from core.registry import create_server

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX sh/sleep fixtures")


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, state_dir=tmp_path))
    return tmp_path


def _payload(result: object) -> dict[str, object]:
    content = getattr(result, "structured_content", None)
    assert isinstance(content, dict), content
    return content


def test_read_labeled_shell_commands_serialize_and_report_exclusive(
    tmp_path: Path, isolated_state: Path,
) -> None:
    async def scenario() -> list[dict[str, object]]:
        async with Client(create_server()) as client:
            async def call() -> dict[str, object]:
                return _payload(await client.call_tool(
                    "run_shell",
                    {
                        "command_text": "sleep 0.5",
                        "cwd": str(tmp_path),
                        "timeout_sec": 60,
                        "access": "read",
                    },
                ))

            started = time.monotonic()
            results = await asyncio.gather(*(call() for _ in range(3)))
            elapsed = time.monotonic() - started
            assert elapsed >= 1.25, f"read-labeled shell commands overlapped: {elapsed:.2f}s"
            assert elapsed < 6.0, f"unexpected admission delay: {elapsed:.2f}s"
            return results

    for payload in asyncio.run(scenario()):
        assert payload["effective_access"] == "exclusive"
        assert payload["access_reason"] == "terminal_command_read_untrusted"


def test_read_labeled_writer_cannot_bypass_an_active_exclusive_writer(
    tmp_path: Path, isolated_state: Path,
) -> None:
    async def scenario() -> None:
        async with Client(create_server()) as client:
            writer = asyncio.create_task(client.call_tool(
                "run_shell",
                {"command_text": "sleep 1.2", "cwd": str(tmp_path), "timeout_sec": 60},
            ))
            await asyncio.sleep(0.3)
            started = time.monotonic()
            result = await client.call_tool(
                "run_shell",
                {
                    "command_text": "sleep 0.2",
                    "cwd": str(tmp_path),
                    "timeout_sec": 60,
                    "access": "read",
                },
            )
            waited = time.monotonic() - started
            await asyncio.wait_for(writer, timeout=30)
            assert waited >= 0.7, f"read-labeled writer bypassed exclusive ownership: {waited:.2f}s"
            assert _payload(result)["effective_access"] == "exclusive"

    asyncio.run(scenario())


def test_read_labeled_run_process_also_takes_the_exclusive_claim(
    tmp_path: Path, isolated_state: Path,
) -> None:
    async def scenario() -> list[dict[str, object]]:
        async with Client(create_server()) as client:
            async def call() -> dict[str, object]:
                return _payload(await client.call_tool(
                    "run_process",
                    {
                        "executable": "sleep",
                        "args": ["0.4"],
                        "cwd": str(tmp_path),
                        "timeout_sec": 60,
                        "access": "read",
                    },
                ))

            started = time.monotonic()
            results = await asyncio.gather(*(call() for _ in range(2)))
            elapsed = time.monotonic() - started
            assert elapsed >= 0.7, f"read-labeled run_process calls overlapped: {elapsed:.2f}s"
            return results

    for payload in asyncio.run(scenario()):
        assert payload["effective_access"] == "exclusive"
        assert payload["access_reason"] == "terminal_command_read_untrusted"


def test_native_read_tools_keep_read_coordination(
    tmp_path: Path, isolated_state: Path,
) -> None:
    """Native readers must remain concurrent; the terminal fix is not host serialization."""

    (tmp_path / "one.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / "two.txt").write_text("two\n", encoding="utf-8")

    async def scenario() -> None:
        async with Client(create_server()) as client:
            started = time.monotonic()
            results = await asyncio.gather(
                client.call_tool("read_file", {"path": str(tmp_path / "one.txt")}),
                client.call_tool("read_file", {"path": str(tmp_path / "two.txt")}),
            )
            elapsed = time.monotonic() - started
            assert all(_payload(result)["ok"] for result in results)
            assert elapsed < 2.0, f"independent native reads did not overlap: {elapsed:.2f}s"

    asyncio.run(scenario())
