"""MCP registration for status, diff, and history summaries."""

from __future__ import annotations

import re
from typing import Annotated, Any

from mcp.server import MCPServer
from pydantic import Field

from core.audit import audit_action
from core.errors import ToolError
from core.response import page
from core.tooling import MUTATING, READ_ONLY, compact_errors
from tools.git.service import repository as _repo
from tools.git.service import run_git as _run_git
from tools.git.service import validate_paths as safe_paths
from tools.git.service import validate_revision

GitRevision = Annotated[str | None, Field(min_length=1, max_length=500, pattern=r"^[^-]")]


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

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("git_diff")
    def git_diff(
        path: str | None = None,
        staged: bool = False,
        base: GitRevision = None,
        paths: Annotated[list[str] | None, Field(max_length=100)] = None,
        max_chars: Annotated[int, Field(ge=1, le=2_000_000)] = 200_000,
    ) -> dict[str, Any]:
        """Return a bounded unified diff for explicit repository state."""
        repo = _repo(path)
        args = ["diff", "--no-ext-diff"]
        if staged:
            args.append("--cached")
        if base:
            args.append(base)
        if paths:
            args.extend(["--", *safe_paths(paths)])
        result = _run_git(repo, args)
        patch = result["stdout"]["text"]
        return {"ok": True, "repo": str(repo), "patch": patch[:max_chars], "truncated": len(patch) > max_chars}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("git_show")
    def git_show(
        path: str | None = None, ref: GitRevision = "HEAD", max_chars: Annotated[int, Field(ge=1, le=2_000_000)] = 200_000
    ) -> dict[str, Any]:
        """Return bounded commit metadata and patch."""
        repo = _repo(path)
        text = _run_git(repo, ["show", "--no-ext-diff", "--format=fuller", ref or "HEAD"])["stdout"]["text"]
        return {"ok": True, "repo": str(repo), "text": text[:max_chars], "truncated": len(text) > max_chars}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("git_blame")
    def git_blame(
        path: str | None, file: str, start_line: Annotated[int, Field(ge=1)] = 1, end_line: Annotated[int, Field(ge=1)] = 200
    ) -> dict[str, Any]:
        """Return porcelain blame records for a bounded line range."""
        repo = _repo(path)
        safe_paths([file])
        text = _run_git(repo, ["blame", "--line-porcelain", f"-L{start_line},{end_line}", "--", file])["stdout"]["text"]
        return {"ok": True, "repo": str(repo), "text": text}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("git_merge_base")
    def git_merge_base(path: str | None = None, first: str = "HEAD", second: str = "main") -> dict[str, Any]:
        """Return the best common ancestor of two revisions."""
        validate_revision(first)
        validate_revision(second)
        repo = _repo(path)
        value = _run_git(repo, ["merge-base", first, second])["stdout"]["text"].strip()
        return {"ok": True, "repo": str(repo), "merge_base": value}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("git_changed_files")
    def git_changed_files(path: str | None = None, base: GitRevision = None) -> dict[str, Any]:
        """Return changed file statuses from a base or the working tree."""
        repo = _repo(path)
        args = ["diff", "--name-status"] + ([base] if base else [])
        rows = []
        for line in _run_git(repo, args)["stdout"]["text"].splitlines():
            parts = line.split("\t")
            rows.append({"status": parts[0], "path": parts[-1], "original_path": parts[1] if len(parts) > 2 else None})
        return {"ok": True, "repo": str(repo), "items": rows, "count": len(rows)}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("git_branch_list")
    def git_branch_list(path: str | None = None) -> dict[str, Any]:
        """List local branches with current and upstream metadata."""
        repo = _repo(path)
        text = _run_git(repo, ["for-each-ref", "--format=%(refname:short)%09%(HEAD)%09%(upstream:short)", "refs/heads"])["stdout"]["text"]
        rows = [
            {"name": p[0], "current": p[1] == "*", "upstream": p[2] or None}
            for line in text.splitlines()
            if len(p := line.split("\t")) == 3
        ]
        return {"ok": True, "repo": str(repo), "items": rows, "count": len(rows)}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("git_conflicts")
    def git_conflicts(path: str | None = None) -> dict[str, Any]:
        """List unresolved index conflicts."""
        repo = _repo(path)
        rows = [
            line.split("\t", 1)[-1] for line in _run_git(repo, ["diff", "--name-only", "--diff-filter=U"])["stdout"]["text"].splitlines()
        ]
        return {"ok": True, "repo": str(repo), "items": rows, "count": len(rows)}

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("git_create_branch")
    def git_create_branch(
        path: str | None, name: Annotated[str, Field(min_length=1, max_length=200)], start_point: GitRevision = None
    ) -> dict[str, Any]:
        """Create and switch to a new branch without force."""
        if name.startswith("-") or not re.fullmatch(r"[A-Za-z0-9._/-]+", name):
            raise ToolError("invalid_branch_name", "Invalid Git branch name.")
        repo = _repo(path)
        _run_git(repo, ["switch", "-c", name, *([start_point] if start_point else [])])
        audit_action("git_create_branch", target=repo, details={"branch": name})
        return {"ok": True, "repo": str(repo), "branch": name}

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("git_stage")
    def git_stage(path: str | None, paths: Annotated[list[str], Field(min_length=1, max_length=500)]) -> dict[str, Any]:
        """Stage only explicitly named paths."""
        repo = _repo(path)
        selected = safe_paths(paths)
        _run_git(repo, ["add", "--", *selected])
        audit_action("git_stage", target=repo, details={"path_count": len(selected)})
        return {"ok": True, "repo": str(repo), "paths": selected}

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("git_commit")
    def git_commit(path: str | None, message: Annotated[str, Field(min_length=1, max_length=10_000)]) -> dict[str, Any]:
        """Commit the currently staged index without pushing or rewriting history."""
        repo = _repo(path)
        if not _run_git(repo, ["diff", "--cached", "--name-only"])["stdout"]["text"].strip():
            raise ToolError("empty_git_index", "No staged changes to commit.")
        _run_git(repo, ["commit", "-m", message], 120)
        full = _run_git(repo, ["rev-parse", "HEAD"])["stdout"]["text"].strip()
        audit_action("git_commit", target=repo, details={"message_chars": len(message)})
        return {"ok": True, "repo": str(repo), "hash": full, "subject": message.splitlines()[0]}

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("git_restore_file")
    def git_restore_file(
        path: str | None,
        source: Annotated[str, Field(min_length=1, max_length=500)],
        paths: Annotated[list[str], Field(min_length=1, max_length=500)],
    ) -> dict[str, Any]:
        """Restore explicitly named tracked paths from an explicit revision."""
        validate_revision(source)
        repo = _repo(path)
        selected = safe_paths(paths)
        _run_git(repo, ["restore", "--source", source, "--", *selected])
        audit_action("git_restore_file", target=repo, details={"path_count": len(selected), "source": source})
        return {"ok": True, "repo": str(repo), "paths": selected, "source": source}
