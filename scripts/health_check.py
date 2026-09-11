"""Compact end-to-end startup checks used by START_MCP.bat."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_TOOLS = {
    "read_file",
    "write_file",
    "create_file",
    "delete_file",
    "move_file",
    "copy_file",
    "search_files",
    "list_directory",
    "get_file_info",
    "replace_exact",
    "replace_between_anchors",
    "replace_function",
    "replace_class",
    "safe_refactor",
    "run_process",
    "run_powershell",
    "run_cmd",
    "list_processes",
    "process_info",
    "kill_process",
    "run_background",
    "process_output",
    "system_info",
    "cpu_usage",
    "memory_usage",
    "disk_usage",
    "environment_variables",
    "installed_programs",
    "windows_services",
    "project_summary",
    "find_function",
    "find_class",
    "find_imports",
    "dependency_graph",
    "run_pytest",
    "run_ruff",
    "run_mypy",
    "git_status",
    "git_diff_summary",
    "git_log_summary",
    "browser_open_page",
    "browser_screenshot",
    "browser_click",
    "browser_fill",
    "browser_close",
    "desktop_screenshot",
    "active_window",
    "mouse_click",
    "keyboard_type",
    "hotkey",
    "memory_read",
    "memory_update",
    "memory_list",
    "server_health",
    "submit_job",
    "job_status",
    "job_output",
    "list_jobs",
    "cancel_job",
}


def _structured(result: Any, operation: str) -> dict[str, Any]:
    if result.is_error:
        text = result.content[0].text if result.content else "unknown tool error"
        raise RuntimeError(f"{operation} returned an MCP error: {text}")
    data = result.structured_content
    if not isinstance(data, dict):
        raise RuntimeError(f"{operation} did not return structured content")
    if not data.get("ok"):
        raise RuntimeError(f"{operation} failed: {data.get('message', data)}")
    return data


async def run_checks() -> dict[str, Any]:
    checks: dict[str, bool] = {"import": True}
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(PROJECT_ROOT / "local_pc_mcp.py")],
        cwd=PROJECT_ROOT,
    )
    async with Client(stdio_client(parameters)) as client:
        checks["mcp_startup"] = True
        listing = await client.list_tools()
        names = {tool.name for tool in listing.tools}
        missing = sorted(REQUIRED_TOOLS - names)
        if missing:
            raise RuntimeError(f"Missing required tools: {missing}")
        checks["tool_registration"] = len(names) == len(listing.tools)
        with tempfile.TemporaryDirectory(prefix="ali-mcp-health-") as temp_dir:
            probe = Path(temp_dir) / "probe.txt"
            _structured(await client.call_tool("create_file", {"path": str(probe), "content": "alpha\n"}), "create_file")
            read = _structured(await client.call_tool("read_file", {"path": str(probe), "max_chars": 100}), "read_file")
            if read.get("content") != "alpha\n":
                raise RuntimeError("read_file returned unexpected content")
            _structured(
                await client.call_tool(
                    "replace_exact",
                    {"path": str(probe), "old": "alpha", "new": "beta", "backup": False},
                ),
                "replace_exact",
            )
            checks["filesystem"] = probe.read_text(encoding="utf-8") == "beta\n"
            terminal = _structured(
                await client.call_tool(
                    "run_process",
                    {
                        "executable": sys.executable,
                        "args": ["-c", "print('MCP_TERMINAL_OK')"],
                        "cwd": temp_dir,
                        "timeout_sec": 15,
                        "stdout_limit": 1_000,
                        "stderr_limit": 1_000,
                    },
                ),
                "run_process",
            )
            checks["terminal"] = terminal.get("exit_code") == 0 and "MCP_TERMINAL_OK" in terminal.get("stdout", {}).get("text", "")
            _structured(await client.call_tool("delete_file", {"path": str(probe)}), "delete_file")
    if not all(checks.values()):
        raise RuntimeError(f"One or more checks failed: {checks}")
    return {"ok": True, "checks": checks, "tool_count": len(names)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.parse_args()
    try:
        result = asyncio.run(run_checks())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, separators=(",", ":")))
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
