from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from mcp import Client

from core.registry import create_server


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-m", "initial")
    return tmp_path


def _structured(result: Any) -> dict[str, Any]:
    assert result.is_error is not True
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def test_operational_git_read_and_mutation_flow(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "a.txt").write_text("two\n", encoding="utf-8")

    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            changed = _structured(await client.call_tool("git_changed_files", {"path": str(repo)}))
            assert changed["items"][0]["path"] == "a.txt"
            diff = _structured(await client.call_tool("git_diff", {"path": str(repo)}))
            assert "-one" in diff["patch"] and "+two" in diff["patch"]
            _structured(await client.call_tool("git_stage", {"path": str(repo), "paths": ["a.txt"]}))
            committed = _structured(await client.call_tool("git_commit", {"path": str(repo), "message": "update value"}))
            assert committed["subject"] == "update value"
            branch = _structured(await client.call_tool("git_create_branch", {"path": str(repo), "name": "feature/test"}))
            assert branch["branch"] == "feature/test"
            branches = _structured(await client.call_tool("git_branch_list", {"path": str(repo)}))
            assert any(item["name"] == "feature/test" for item in branches["items"])
            (repo / "a.txt").write_text("dirty\n", encoding="utf-8")
            _structured(await client.call_tool("git_restore_file", {"path": str(repo), "source": "HEAD", "paths": ["a.txt"]}))
            assert (repo / "a.txt").read_text(encoding="utf-8") == "two\n"

    asyncio.run(scenario())


def test_git_safety_and_dirty_state_edges(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "a.txt").write_text("staged\n", encoding="utf-8")
    (repo / "new.txt").write_text("new\n", encoding="utf-8")

    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            rejected = _structured(await client.call_tool("git_diff", {"path": str(repo), "paths": ["--stat"]}))
            assert rejected["ok"] is False
            assert rejected["error"] == "invalid_git_pathspec"

            empty = _structured(await client.call_tool("git_commit", {"path": str(repo), "message": "empty"}))
            assert empty["ok"] is False
            assert empty["error"] == "empty_git_index"

            _structured(await client.call_tool("git_stage", {"path": str(repo), "paths": ["a.txt"]}))
            changed = _structured(await client.call_tool("git_status", {"path": str(repo)}))
            assert changed["staged"] == 1
            assert changed["untracked"] == 1

            collision = _structured(await client.call_tool("git_create_branch", {"path": str(repo), "name": "main"}))
            assert collision["ok"] is False
            assert collision["error"] == "git_failed"

    asyncio.run(scenario())
