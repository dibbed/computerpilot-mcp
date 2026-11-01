"""MCP registration for direct, PowerShell, and platform shell execution."""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from core.artifacts import default_delivery
from core.audit import audit_action
from core.command_concurrency import SHARED_COMMANDS
from core.config import PROJECT_ROOT, resolve_path
from core.errors import ToolError
from core.executor import run_bounded
from core.operation_control import OPERATIONS, run_cancellable_operation
from core.resource_claims import coordination_claim, managed_resource_claims, path_claim
from core.tooling import OPEN_WORLD_WRITE, compact_errors


def _cwd(value: str | None) -> Path:
    return resolve_path(value) if value else PROJECT_ROOT


@contextmanager
def _managed_command_claims(
    working: Path, timeout_sec: float, cancel_event: threading.Event | None = None,
    access: Literal["exclusive", "read"] = "exclusive",
) -> Iterator[float]:
    deadline = time.monotonic() + timeout_sec
    claims = (
        [coordination_claim(exclusive=False), path_claim(working, "read", scope="subtree")]
        if access == "read"
        else [coordination_claim(exclusive=True), path_claim(working, "write", scope="subtree")]
    )
    with managed_resource_claims(
        claims,
        category="native.command",
        timeout_sec=timeout_sec,
        cancel_event=cancel_event,
    ):
        remaining = deadline - time.monotonic()
        if cancel_event is not None and cancel_event.is_set():
            raise ToolError("command_cancelled", "The command was cancelled before dispatch.")
        if remaining <= 0:
            raise ToolError("resource_admission_timeout", "Command timeout expired before process dispatch.")
        if access == "read":
            with SHARED_COMMANDS.slot(remaining, cancel_event):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ToolError("resource_admission_timeout", "Command timeout expired before process dispatch.")
                yield remaining
        else:
            yield remaining


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
    async def run_process(
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
        operation_id: Annotated[str | None, Field(
            min_length=8, max_length=128,
            description="Client-chosen id for cancel_operation; enables HTTP cancellation.",
        )] = None,
        access: Annotated[Literal["exclusive", "read"], Field(
            description="read only when the command writes nothing; read commands may overlap.",
        )] = "exclusive",
    ) -> dict[str, Any]:
        """Run an executable with a timeout and optional per-stream output limits. Prefer read_file/search_files/git_* for native work."""

        def execute(cancel: threading.Event) -> dict[str, Any]:
            working = _cwd(cwd)
            process_args = args or []
            command = [executable, *process_args]
            _audit_command("run_process", command, working)
            with _managed_command_claims(working, timeout_sec, cancel, access=access) as remaining:
                result = run_bounded(
                    command,
                    cwd=working,
                    timeout_sec=remaining,
                    stdout_limit=stdout_limit,
                    stderr_limit=stderr_limit,
                    output_mode=output_mode,
                    env=env,
                    stdin_text=stdin_text,
                    delivery=delivery,
                    cancel_event=cancel,
                )
            result["command"] = {"executable": executable, "argument_count": len(process_args)}
            audit_action(
                "run_process", target=working, outcome="completed",
                details={"pid": result["pid"], "exit_code": result["exit_code"]},
            )
            return result

        return await run_cancellable_operation(operation_id, "run_process", execute)

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("run_powershell")
    async def run_powershell(
        script: Annotated[str, Field(min_length=1, max_length=100_000)],
        cwd: Annotated[str | None, Field(max_length=32_767)] = None,
        timeout_sec: Annotated[float, Field(gt=0, le=3_600)] = 60,
        stdout_limit: Annotated[int | None, Field(ge=0)] = None,
        stderr_limit: Annotated[int | None, Field(ge=0)] = None,
        output_mode: Literal["head", "tail", "both"] = "tail",
        env: Annotated[dict[str, str] | None, Field(max_length=100)] = None,
        delivery: Literal["inline", "file", "auto"] = output_default,
        operation_id: Annotated[str | None, Field(min_length=8, max_length=128)] = None,
        access: Literal["exclusive", "read"] = "exclusive",
    ) -> dict[str, Any]:
        """Run PowerShell non-interactively with optional output limits and a hard timeout.
        For cmdlets/pipelines; prefer run_process or native file/search/Git tools.
        """

        def execute(cancel: threading.Event) -> dict[str, Any]:
            executable = shutil.which("pwsh") or shutil.which("powershell")
            if executable is None:
                raise ToolError("powershell_not_found", "Neither pwsh nor powershell is available on PATH.")
            working = _cwd(cwd)
            command = [executable, "-NoLogo", "-NoProfile", "-NonInteractive"]
            if os.name == "nt":
                command.extend(["-ExecutionPolicy", "Bypass"])
            command.extend(["-Command", script])
            _audit_command("run_powershell", command, working, script_chars=len(script))
            with _managed_command_claims(working, timeout_sec, cancel, access=access) as remaining:
                result = run_bounded(
                    command,
                    cwd=working,
                    timeout_sec=remaining,
                    stdout_limit=stdout_limit,
                    stderr_limit=stderr_limit,
                    output_mode=output_mode,
                    env=env,
                    delivery=delivery,
                    cancel_event=cancel,
                )
            result["command"] = {"shell": Path(executable).name, "script_chars": len(script)}
            audit_action(
                "run_powershell", target=working, outcome="completed",
                details={"pid": result["pid"], "exit_code": result["exit_code"]},
            )
            return result

        return await run_cancellable_operation(operation_id, "run_powershell", execute)

    if os.name == "nt":
        @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
        @compact_errors("run_cmd")
        async def run_cmd(
            command_text: Annotated[str, Field(min_length=1, max_length=100_000)],
            cwd: Annotated[str | None, Field(max_length=32_767)] = None,
            timeout_sec: Annotated[float, Field(gt=0, le=3_600)] = 60,
            stdout_limit: Annotated[int | None, Field(ge=0)] = None,
            stderr_limit: Annotated[int | None, Field(ge=0)] = None,
            output_mode: Literal["head", "tail", "both"] = "tail",
            env: Annotated[dict[str, str] | None, Field(max_length=100)] = None,
            delivery: Literal["inline", "file", "auto"] = output_default,
            operation_id: Annotated[str | None, Field(min_length=8, max_length=128)] = None,
            access: Literal["exclusive", "read"] = "exclusive",
        ) -> dict[str, Any]:
            """Run one Windows CMD command line with optional output limits and a hard timeout."""

            def execute(cancel: threading.Event) -> dict[str, Any]:
                executable = os.environ.get("COMSPEC") or shutil.which("cmd.exe")
                if not executable:
                    raise ToolError("cmd_not_found", "cmd.exe is unavailable.")
                working = _cwd(cwd)
                command = [executable, "/d", "/s", "/c", command_text]
                _audit_command("run_cmd", command, working, script_chars=len(command_text))
                with _managed_command_claims(working, timeout_sec, cancel, access=access) as remaining:
                    result = run_bounded(
                        command,
                        cwd=working,
                        timeout_sec=remaining,
                        stdout_limit=stdout_limit,
                        stderr_limit=stderr_limit,
                        output_mode=output_mode,
                        env=env,
                        delivery=delivery,
                        cancel_event=cancel,
                    )
                result["command"] = {"shell": "cmd.exe", "command_chars": len(command_text)}
                audit_action(
                    "run_cmd",
                    target=working,
                    outcome="completed",
                    details={"pid": result["pid"], "exit_code": result["exit_code"]},
                )
                return result

            return await run_cancellable_operation(operation_id, "run_cmd", execute)
    else:
        @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
        @compact_errors("run_shell")
        async def run_shell(
            command_text: Annotated[str, Field(min_length=1, max_length=100_000)],
            cwd: Annotated[str | None, Field(max_length=32_767)] = None,
            timeout_sec: Annotated[float, Field(gt=0, le=3_600)] = 60,
            stdout_limit: Annotated[int | None, Field(ge=0)] = None,
            stderr_limit: Annotated[int | None, Field(ge=0)] = None,
            output_mode: Literal["head", "tail", "both"] = "tail",
            env: Annotated[dict[str, str] | None, Field(max_length=100)] = None,
            delivery: Literal["inline", "file", "auto"] = output_default,
            operation_id: Annotated[str | None, Field(min_length=8, max_length=128)] = None,
            access: Literal["exclusive", "read"] = "exclusive",
        ) -> dict[str, Any]:
            """Run one POSIX sh command line with explicit shell expansion and a hard timeout."""

            def execute(cancel: threading.Event) -> dict[str, Any]:
                executable = shutil.which("sh")
                if executable is None:
                    raise ToolError("posix_shell_not_found", "A POSIX sh executable is unavailable on PATH.")
                working = _cwd(cwd)
                command = [executable, "-c", command_text]
                _audit_command("run_shell", command, working, script_chars=len(command_text))
                with _managed_command_claims(working, timeout_sec, cancel, access=access) as remaining:
                    result = run_bounded(
                        command,
                        cwd=working,
                        timeout_sec=remaining,
                        stdout_limit=stdout_limit,
                        stderr_limit=stderr_limit,
                        output_mode=output_mode,
                        env=env,
                        delivery=delivery,
                        cancel_event=cancel,
                    )
                result["command"] = {"shell": Path(executable).name, "command_chars": len(command_text)}
                audit_action(
                    "run_shell",
                    target=working,
                    outcome="completed",
                    details={"pid": result["pid"], "exit_code": result["exit_code"]},
                )
                return result

            return await run_cancellable_operation(operation_id, "run_shell", execute)

    @mcp.tool(annotations=OPEN_WORLD_WRITE, structured_output=True)
    @compact_errors("cancel_operation")
    def cancel_operation(
        operation_id: Annotated[str, Field(min_length=8, max_length=128)],
    ) -> dict[str, Any]:
        """Cancel one in-flight terminal command by its client-chosen operation_id; the HTTP-safe explicit contract."""
        audit_action("cancel_operation", target=operation_id, durable=True)
        return OPERATIONS.request_cancel(operation_id)
