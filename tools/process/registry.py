"""MCP registration for compact process management."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

import psutil
from mcp.server import MCPServer
from pydantic import Field

from core.artifacts import default_delivery
from core.audit import audit_action
from core.config import PROJECT_ROOT, resolve_path
from core.executor import background_output, start_background, terminate_process_tree
from core.response import page
from core.tooling import DESTRUCTIVE, MUTATING, READ_ONLY, compact_errors


def _process_row(process: psutil.Process) -> dict[str, Any] | None:
    try:
        data = process.as_dict(attrs=["pid", "name", "status", "username", "create_time"], ad_value=None)
        created = data.get("create_time")
        return {
            "pid": data["pid"],
            "name": data.get("name"),
            "status": data.get("status"),
            "username": data.get("username"),
            "started": datetime.fromtimestamp(created, timezone.utc).isoformat(timespec="seconds") if created else None,
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def register(mcp: MCPServer) -> None:
    output_default = default_delivery()

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("list_processes")
    def list_processes(
        name_filter: Annotated[str | None, Field(max_length=200)] = None,
        status_filter: Annotated[str | None, Field(max_length=50)] = None,
        offset: Annotated[int, Field(ge=0, le=1_000_000)] = 0,
        max_items: Annotated[int, Field(ge=1, le=500)] = 50,
        sort_by: Literal["pid", "name", "started"] = "pid",
    ) -> dict[str, Any]:
        """List processes with filters and pagination instead of dumping the process table."""

        needle = name_filter.casefold() if name_filter else None
        rows = []
        for process in psutil.process_iter():
            row = _process_row(process)
            if row is None:
                continue
            if needle and needle not in (row["name"] or "").casefold():
                continue
            if status_filter and row["status"] != status_filter:
                continue
            rows.append(row)
        rows.sort(key=lambda item: (item[sort_by] or "") if sort_by != "pid" else item["pid"])
        total = len(rows)
        return {"ok": True, **page(rows[offset : offset + max_items], total=total, offset=offset, limit=max_items)}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("process_info")
    def process_info(
        pid: Annotated[int, Field(gt=0)],
        include_command_line: bool = False,
        max_command_chars: Annotated[int | None, Field(ge=1)] = None,
    ) -> dict[str, Any]:
        """Return compact CPU, memory, identity, and optional command details for one PID."""

        process = psutil.Process(pid)
        with process.oneshot():
            memory = process.memory_info()
            created = process.create_time()
            result: dict[str, Any] = {
                "ok": True,
                "pid": pid,
                "name": process.name(),
                "status": process.status(),
                "username": process.username(),
                "exe": process.exe() or None,
                "cwd": process.cwd() or None,
                "started": datetime.fromtimestamp(created, timezone.utc).isoformat(timespec="seconds"),
                "cpu_percent": process.cpu_percent(interval=0.0),
                "memory_rss_mb": round(memory.rss / 1_048_576, 2),
                "threads": process.num_threads(),
                "children": len(process.children()),
            }
            if include_command_line:
                command = " ".join(process.cmdline())
                result["command_line"] = command if max_command_chars is None else command[:max_command_chars]
                result["command_line_truncated"] = max_command_chars is not None and len(command) > max_command_chars
            return result

    @mcp.tool(annotations=DESTRUCTIVE, structured_output=True)
    @compact_errors("kill_process")
    def kill_process(
        pid: Annotated[int, Field(gt=0)],
        force: bool = False,
        include_children: bool = True,
    ) -> dict[str, Any]:
        """Terminate one PID and optionally its descendants, escalating only if needed."""

        audit_action("kill_process", target=str(pid), details={"force": force, "include_children": include_children}, durable=True)
        result = terminate_process_tree(pid, force=force, include_children=include_children)
        audit_action("kill_process", target=str(pid), outcome="succeeded", details=result, durable=True)
        return {"ok": not result["alive_pids"], "pid": pid, **result}

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("run_background")
    def run_background(
        executable: Annotated[str, Field(min_length=1, max_length=32_767)],
        args: Annotated[list[str] | None, Field(max_length=200)] = None,
        cwd: Annotated[str | None, Field(max_length=32_767)] = None,
        env: Annotated[dict[str, str] | None, Field(max_length=100)] = None,
        capture_limit: Annotated[int | None, Field(ge=0)] = None,
        output_mode: Literal["head", "tail", "both"] = "tail",
    ) -> dict[str, Any]:
        """Start a background process with optional output limits and disk-backed capture."""

        working = resolve_path(cwd) if cwd else PROJECT_ROOT
        process_args = args or []
        command = [executable, *process_args]
        command_hash = hashlib.sha256("\0".join(command).encode("utf-8", errors="replace")).hexdigest()
        audit_action(
            "run_background",
            target=working,
            details={"executable": executable, "argument_count": len(process_args), "command_sha256": command_hash},
        )
        result = start_background(
            command,
            cwd=working,
            capture_limit=capture_limit,
            output_mode=output_mode,
            env=env,
        )
        audit_action("run_background", target=working, outcome="started", details={"pid": result["pid"]})
        return result

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("process_output")
    def process_output(
        pid: Annotated[int, Field(gt=0)],
        since_byte: Annotated[int | None, Field(ge=0)] = None,
        stderr_since_byte: Annotated[int | None, Field(ge=0)] = None,
        delivery: Literal["inline", "file", "auto"] = output_default,
    ) -> dict[str, Any]:
        """Read captured output for a process started by run_background in this server session."""

        return background_output(pid, since_byte=since_byte, stderr_since_byte=stderr_since_byte, delivery=delivery)
