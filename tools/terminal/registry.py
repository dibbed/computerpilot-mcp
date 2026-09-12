"""MCP registration for direct, PowerShell, and CMD execution."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from core.artifacts import default_delivery
from core.audit import audit_action
from core.config import PROJECT_ROOT, resolve_path
from core.errors import ToolError
from core.executor import run_bounded
from core.tooling import OPEN_WORLD_WRITE, compact_errors


def _cwd(value: str | None) -> Path:
    return resolve_path(value) if value else PROJECT_ROOT


def _audit_command(operation: str, command: list[str], cwd: Path, *, script_chars: int | None = None) -> None:
    digest = hashlib.sha256("\0".join(command).encode("utf-8", errors="replace")).hexdigest()
    details: dict[str, Any] = {
        "executable": command[0],
        "argument_count": max(len(command) - 1, 0),
        "command_sha256": digest,
    }
    if script_chars is not None:
        details["script_chars"] = script_chars
    audit_action(operation, target=cwd, details=details)


def register(mcp: MCPServer) -> None:
    output_default = default_delivery()

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("run_process")
    def run_process(
        executable: Annotated[str, Field(min_length=1, max_length=32_767)],
        args: Annotated[list[str] | None, Field(max_length=200)] = None,
        cwd: Annotated[str | None, Field(max_length=32_767)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=3_600)] = 60,
        stdout_limit: Annotated[int | None, Field(ge=0)] = None,
        stderr_limit: Annotated[int | None, Field(ge=0)] = None,
        output_mode: Literal["head", "tail", "both"] = "tail",
        env: Annotated[dict[str, str] | None, Field(max_length=100)] = None,
        stdin_text: Annotated[str | None, Field(max_length=100_000)] = None,
        delivery: Literal["inline", "file", "auto"] = output_default,
    ) -> dict[str, Any]:
        """Run an executable with a timeout and optional per-stream output limits."""

        working = _cwd(cwd)
        process_args = args or []
        command = [executable, *process_args]
        _audit_command("run_process", command, working)
        result = run_bounded(
            command,
            cwd=working,
            timeout_sec=timeout_sec,
            stdout_limit=stdout_limit,
            stderr_limit=stderr_limit,
            output_mode=output_mode,
            env=env,
            stdin_text=stdin_text,
            delivery=delivery,
        )
        result["command"] = {"executable": executable, "argument_count": len(process_args)}
        audit_action("run_process", target=working, outcome="completed", details={"pid": result["pid"], "exit_code": result["exit_code"]})
        return result

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("run_powershell")
    def run_powershell(
        script: Annotated[str, Field(min_length=1, max_length=100_000)],
        cwd: Annotated[str | None, Field(max_length=32_767)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=3_600)] = 60,
        stdout_limit: Annotated[int | None, Field(ge=0)] = None,
        stderr_limit: Annotated[int | None, Field(ge=0)] = None,
        output_mode: Literal["head", "tail", "both"] = "tail",
        env: Annotated[dict[str, str] | None, Field(max_length=100)] = None,
        delivery: Literal["inline", "file", "auto"] = output_default,
    ) -> dict[str, Any]:
        """Run PowerShell non-interactively with optional output limits and a hard timeout."""

        executable = shutil.which("pwsh") or shutil.which("powershell")
        if executable is None:
            raise ToolError("powershell_not_found", "Neither pwsh nor powershell is available on PATH.")
        working = _cwd(cwd)
        command = [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
        _audit_command("run_powershell", command, working, script_chars=len(script))
        result = run_bounded(
            command,
            cwd=working,
            timeout_sec=timeout_sec,
            stdout_limit=stdout_limit,
            stderr_limit=stderr_limit,
            output_mode=output_mode,
            env=env,
            delivery=delivery,
        )
        result["command"] = {"shell": Path(executable).name, "script_chars": len(script)}
        audit_action(
            "run_powershell", target=working, outcome="completed", details={"pid": result["pid"], "exit_code": result["exit_code"]}
        )
        return result

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("run_cmd")
    def run_cmd(
        command_text: Annotated[str, Field(min_length=1, max_length=100_000)],
        cwd: Annotated[str | None, Field(max_length=32_767)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=3_600)] = 60,
        stdout_limit: Annotated[int | None, Field(ge=0)] = None,
        stderr_limit: Annotated[int | None, Field(ge=0)] = None,
        output_mode: Literal["head", "tail", "both"] = "tail",
        env: Annotated[dict[str, str] | None, Field(max_length=100)] = None,
        delivery: Literal["inline", "file", "auto"] = output_default,
    ) -> dict[str, Any]:
        """Run one Windows CMD command line with optional output limits and a hard timeout."""

        executable = os.environ.get("COMSPEC") or shutil.which("cmd.exe")
        if not executable:
            raise ToolError("cmd_not_found", "cmd.exe is unavailable.")
        working = _cwd(cwd)
        command = [executable, "/d", "/s", "/c", command_text]
        _audit_command("run_cmd", command, working, script_chars=len(command_text))
        result = run_bounded(
            command,
            cwd=working,
            timeout_sec=timeout_sec,
            stdout_limit=stdout_limit,
            stderr_limit=stderr_limit,
            output_mode=output_mode,
            env=env,
            delivery=delivery,
        )
        result["command"] = {"shell": "cmd.exe", "command_chars": len(command_text)}
        audit_action("run_cmd", target=working, outcome="completed", details={"pid": result["pid"], "exit_code": result["exit_code"]})
        return result
