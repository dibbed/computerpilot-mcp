from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from core.registry import create_server


def _git(repo: Path, *args: str) -> None:
    executable = shutil.which("git")
    if executable is None:
        pytest.skip("Git is not installed")
    subprocess.run([executable, *args], cwd=repo, check=True, capture_output=True)


def _structured(result: Any) -> dict[str, Any]:
    assert result.is_error is not True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["ok"] is True
    return result.structured_content


def test_git_status_diff_and_log_are_compact(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.email", "mcp@example.invalid")
    _git(tmp_path, "config", "user.name", "MCP Test")
    target = tmp_path / "file.txt"
    target.write_text("one\n", encoding="utf-8")
    _git(tmp_path, "add", "file.txt")
    _git(tmp_path, "commit", "--quiet", "-m", "initial")
    target.write_text("one\ntwo\n", encoding="utf-8")

    async def scenario() -> None:
        server = create_server()
        async with Client(server, raise_exceptions=True) as client:
            status = _structured(await client.call_tool("git_status", {"path": str(tmp_path)}))
            assert status["clean"] is False
            assert status["total_count"] == 1
            diff = _structured(await client.call_tool("git_diff_summary", {"path": str(tmp_path)}))
            assert diff["additions"] == 1
            assert diff["items"][0]["path"] == "file.txt"
            log = _structured(await client.call_tool("git_log_summary", {"path": str(tmp_path), "max_items": 1}))
            assert log["count"] == 1
            assert log["items"][0]["subject"] == "initial"

    asyncio.run(scenario())


def test_git_status_parses_rename_records(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.email", "mcp@example.invalid")
    _git(tmp_path, "config", "user.name", "MCP Test")
    (tmp_path / "before.txt").write_text("value\n", encoding="utf-8")
    _git(tmp_path, "add", "before.txt")
    _git(tmp_path, "commit", "--quiet", "-m", "initial")
    _git(tmp_path, "mv", "before.txt", "after.txt")

    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            status = _structured(await client.call_tool("git_status", {"path": str(tmp_path)}))
            assert status["total_count"] == 1
            assert status["items"][0]["status"].startswith("R")
            assert status["items"][0]["path"] == "after.txt"
            assert status["items"][0]["original_path"] == "before.txt"

    asyncio.run(scenario())
