"""MCP registration for status, diff, and history summaries."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from pydantic import Field

from core.config import PROJECT_ROOT, resolve_path
from core.errors import ToolError
from core.executor import run_bounded
from core.response import page
from core.tooling import READ_ONLY, compact_errors

GitRevision = Annotated[str | None, Field(min_length=1, max_length=500, pattern=r"^[^-]")]


def _git() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise ToolError("git_not_found", "Git is not installed or not on PATH.")
    return executable


def _repo(value: str | None) -> Path:
    path = resolve_path(value) if value else PROJECT_ROOT
    if not path.is_dir():
        raise NotADirectoryError(f"Repository directory not found: {path}")
    return path


def _run_git(repo: Path, args: list[str], timeout_sec: float = 30) -> dict[str, Any]:
    result = run_bounded(
        [_git(), "-c", "core.quotepath=false", *args],
        cwd=repo,
        timeout_sec=timeout_sec,
        stdout_limit=None,
        stderr_limit=None,
        output_mode="head",
        encoding="utf-8",
    )
    if result["exit_code"] != 0:
        message = result["stderr"]["text"].strip() or result["stdout"]["text"].strip()
        raise ToolError("git_failed", message or f"Git exited {result['exit_code']}.")
    return result


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("git_status")
    def git_status(
        path: Annotated[str | None, Field(max_length=32_767)] = None,
        offset: Annotated[int, Field(ge=0, le=100_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> dict[str, Any]:
        """Return branch and paginated working-tree changes without full status output."""

        repo = _repo(path)
        result = _run_git(repo, ["status", "--porcelain=v1", "-z", "--branch"])
        records = result["stdout"]["text"].split("\0")
        header = records[0] if records and records[0].startswith("## ") else ""
        changes = []
        staged = unstaged = untracked = conflicts = 0
        index = 1 if header else 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record or len(record) < 3:
                continue
            status = record[:2]
            file_path = record[3:]
            original_path = None
            if ("R" in status or "C" in status) and index < len(records):
                original_path = records[index] or None
                index += 1
            if status == "??":
                untracked += 1
            else:
                if status[0] not in {" ", "?"}:
                    staged += 1
                if status[1] not in {" ", "?"}:
                    unstaged += 1
                if "U" in status or status in {"AA", "DD"}:
                    conflicts += 1
            change = {"status": status, "path": file_path}
            if original_path:
                change["original_path"] = original_path
            changes.append(change)
        branch = header[3:] if header else None
        total = len(changes)
        return {
            "ok": True,
            "repo": str(repo),
            "branch": branch,
            "clean": total == 0,
            "staged": staged,
            "unstaged": unstaged,
            "untracked": untracked,
            "conflicts": conflicts,
            "scan_truncated": result["stdout"]["truncated"],
            **page(changes[offset : offset + max_items], total=total, offset=offset, limit=max_items),
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("git_diff_summary")
    def git_diff_summary(
        path: Annotated[str | None, Field(max_length=32_767)] = None,
        staged: bool = False,
        base: GitRevision = None,
        pathspec: Annotated[list[str] | None, Field(max_length=100)] = None,
        offset: Annotated[int, Field(ge=0, le=100_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> dict[str, Any]:
        """Summarize changed files and line counts without returning patch contents."""

        repo = _repo(path)
        args = ["diff", "--numstat"]
        if staged:
            args.append("--cached")
        if base:
            args.append(base)
        if pathspec:
            args.extend(["--", *pathspec])
        result = _run_git(repo, args)
        rows = []
        total_additions = total_deletions = 0
        for line in result["stdout"]["text"].splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            added_raw, deleted_raw, file_path = parts
            binary = added_raw == "-" or deleted_raw == "-"
            added = None if binary else int(added_raw)
            deleted = None if binary else int(deleted_raw)
            if added is not None:
                total_additions += added
            if deleted is not None:
                total_deletions += deleted
            rows.append({"path": file_path, "added": added, "deleted": deleted, "binary": binary})
        total = len(rows)
        return {
            "ok": True,
            "repo": str(repo),
            "staged": staged,
            "base": base,
            "additions": total_additions,
            "deletions": total_deletions,
            **page(rows[offset : offset + max_items], total=total, offset=offset, limit=max_items),
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("git_log_summary")
    def git_log_summary(
        path: Annotated[str | None, Field(max_length=32_767)] = None,
        ref: GitRevision = None,
        max_items: Annotated[int, Field(ge=1, le=200)] = 20,
        skip: Annotated[int, Field(ge=0, le=100_000)] = 0,
        author: Annotated[str | None, Field(max_length=500)] = None,
        since: Annotated[str | None, Field(max_length=200)] = None,
    ) -> dict[str, Any]:
        """Return compact commit metadata with a hard item limit."""

        repo = _repo(path)
        args = ["log", f"--max-count={max_items + 1}", f"--skip={skip}", "--format=%H%x1f%h%x1f%an%x1f%aI%x1f%s%x1e"]
        if author:
            args.append(f"--author={author}")
        if since:
            args.append(f"--since={since}")
        if ref:
            args.append(ref)
        result = _run_git(repo, args)
        commits = []
        for record in result["stdout"]["text"].split("\x1e"):
            fields = record.strip().split("\x1f")
            if len(fields) != 5:
                continue
            full_hash, short_hash, commit_author, date, subject = fields
            commits.append({"hash": full_hash, "short_hash": short_hash, "author": commit_author, "date": date, "subject": subject})
        has_more = len(commits) > max_items
        commits = commits[:max_items]
        return {
            "ok": True,
            "repo": str(repo),
            "items": commits,
            "count": len(commits),
            "offset": skip,
            "has_more": has_more,
            "next_offset": skip + len(commits) if has_more else None,
            "truncated": has_more,
        }
