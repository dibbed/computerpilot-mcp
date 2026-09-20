from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from core.config import SETTINGS
from core.registry import create_server
from core.timings import flush_timings

REQUIRED_TOOLS = {
    "view_image",
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
    "apply_patch",
    "run_process",
    "run_powershell",
    "run_cmd",
    "list_processes",
    "process_info",
    "kill_process",
    "run_background",
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
    "code_context",
    "run_pytest",
    "run_ruff",
    "run_mypy",
    "affected_tests",
    "verify_changes",
    "symbol_definition",
    "symbol_references",
    "document_symbols",
    "workspace_symbols",
    "symbol_hover",
    "call_hierarchy",
    "language_diagnostics",
    "rename_symbol",
    "apply_code_action",
    "git_status",
    "git_diff_summary",
    "git_log_summary",
    "browser_open_page",
    "browser_screenshot",
    "browser_click",
    "browser_fill",
    "desktop_screenshot",
    "active_window",
    "mouse_click",
    "keyboard_type",
    "hotkey",
    "memory_read",
    "memory_update",
    "job_wait",
}


def _structured(result: Any) -> dict[str, Any]:
    assert result.is_error is not True
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def test_mcp_tool_timing_records_request_and_tool_body(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "timings.jsonl"
    monkeypatch.setenv("MCP_TIMINGS", "1")
    monkeypatch.setenv("MCP_TIMINGS_FILE", str(target))

    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            health = _structured(await client.call_tool("server_health", {}))
            assert health["ok"] is True

    asyncio.run(scenario())
    flush_timings()
    records = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    phases = {(record["phase"], record.get("tool")) for record in records}
    assert ("validation", "server_health") in phases
    assert ("queue_wait", "server_health") in phases
    assert ("tool_body", "server_health") in phases
    assert ("result_conversion", "server_health") in phases
    assert ("request_pipeline", "server_health") in phases
    assert ("serialization", "server_health") in phases


def test_server_health_exposes_resource_usage_and_budgets() -> None:
    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            health = _structured(await client.call_tool("server_health", {}))
            budgets = health["resource_budgets"]
            usage = health["resource_usage"]
            assert health["rss_mb"] > 0
            assert health["ast_cache_entries"] >= 0
            assert health["ast_cache_bytes"] >= 0
            assert health["search_snapshot_count"] >= 0
            assert health["active_browser_sessions"] >= 0
            assert health["active_background_processes"] >= 0
            assert health["queued_jobs"] >= 0
            assert health["running_jobs"] >= 0
            assert health["artifact_bytes"] >= 0
            assert health["artifact_storage_bytes"] == health["artifact_bytes"]
            assert health["backup_bytes"] >= 0
            assert health["audit_bytes"] >= 0
            assert health["resource_pressure"] in {"normal", "warning", "critical"}
            assert budgets["browser_max_sessions"] == SETTINGS.browser_max_sessions
            assert budgets["browser_idle_sec"] == SETTINGS.browser_idle_sec
            assert budgets["artifact_max_bytes"] == SETTINGS.artifact_max_bytes
            assert budgets["artifact_max_age_hours"] == SETTINGS.artifact_max_age_hours
            assert usage["artifact_bytes"]["max"] == SETTINGS.artifact_max_bytes
            assert usage["audit_bytes"]["max"] == SETTINGS.audit_max_file_bytes * (SETTINGS.audit_keep_files + 1)

    asyncio.run(scenario())


def test_registration_is_unique_strict_and_compact() -> None:
    server = create_server()
    tools = asyncio.run(server.list_tools())
    names = [tool.name for tool in tools]
    assert len(names) == 74
    assert len(names) == len(set(names))
    assert REQUIRED_TOOLS <= set(names)
    for tool in tools:
        assert tool.input_schema["type"] == "object"
        assert tool.input_schema["additionalProperties"] is False
        assert len(tool.description or "") <= 180


def test_output_limit_schemas_default_to_unlimited_without_hard_maximum() -> None:
    tools = {tool.name: tool for tool in asyncio.run(create_server().list_tools())}
    fields = {
        "read_file": ("max_chars",),
        "run_process": ("stdout_limit", "stderr_limit"),
        "run_powershell": ("stdout_limit", "stderr_limit"),
        "run_cmd": ("stdout_limit", "stderr_limit"),
        "run_background": ("capture_limit",),
        "run_pytest": ("output_max_chars",),
        "process_info": ("max_command_chars",),
        "environment_variables": ("value_max_chars",),
    }
    for tool_name, field_names in fields.items():
        properties = tools[tool_name].input_schema["properties"]
        for field_name in field_names:
            schema = properties[field_name]
            assert schema["default"] is None
            integer_schema = next(item for item in schema["anyOf"] if item.get("type") == "integer")
            assert "maximum" not in integer_schema


def test_unknown_arguments_are_rejected() -> None:
    async def scenario() -> None:
        server = create_server()
        async with Client(server, raise_exceptions=True) as client:
            result = await client.call_tool("server_health", {"unexpected": True})
            assert result.is_error is True

    asyncio.run(scenario())


def test_filesystem_round_trip_and_pagination(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = create_server()
        target = tmp_path / "sample.py"
        async with Client(server, raise_exceptions=True) as client:
            created = _structured(
                await client.call_tool(
                    "create_file",
                    {"path": str(target), "content": "def answer():\n    return 1\n"},
                )
            )
            assert created["created"] is True
            read = _structured(await client.call_tool("read_file", {"path": str(target), "max_chars": 10}))
            assert read["content"] == "def answer"
            assert read["truncated"] is True
            edited = _structured(
                await client.call_tool(
                    "replace_function",
                    {"path": str(target), "function_name": "answer", "new_body": "return 42", "backup": False},
                )
            )
            assert edited["diff"]["hunks"] == 1
            assert "return 42" in target.read_text(encoding="utf-8")
            patched = _structured(
                await client.call_tool(
                    "apply_patch",
                    {
                        "root": str(tmp_path),
                        "patch": (
                            "diff --git a/sample.py b/sample.py\n"
                            "--- a/sample.py\n+++ b/sample.py\n"
                            "@@ -1,2 +1,2 @@\n def answer():\n-    return 42\n+    return 43\n"
                        ),
                        "backup": False,
                    },
                )
            )
            assert patched["file_count"] == 1
            assert "return 43" in target.read_text(encoding="utf-8")
            refused_move = _structured(
                await client.call_tool(
                    "move_file",
                    {"source": str(target), "destination": str(target), "overwrite": True},
                )
            )
            assert refused_move["ok"] is False
            assert refused_move["error"] == "same_path"
            assert target.is_file()
            listing = _structured(await client.call_tool("list_directory", {"path": str(tmp_path), "max_items": 1}))
            assert listing["total_count"] == 1
            assert listing["items"][0]["name"] == "sample.py"
            deleted = _structured(await client.call_tool("delete_file", {"path": str(target)}))
            assert deleted["deleted"] is True

    asyncio.run(scenario())


def test_read_file_returns_large_content_without_a_limit(tmp_path: Path) -> None:
    expected = "r" * 300_000
    target = tmp_path / "large.txt"
    target.write_text(expected, encoding="utf-8")

    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            read = _structured(await client.call_tool("read_file", {"path": str(target)}))
            assert read["content"] == expected
            assert read["chars"] == len(expected)
            assert read["truncated"] is False

    asyncio.run(scenario())


def test_terminal_output_is_unlimited_by_default_and_optionally_limited(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = create_server()
        async with Client(server, raise_exceptions=True) as client:
            unlimited = _structured(
                await client.call_tool(
                    "run_process",
                    {
                        "executable": sys.executable,
                        "args": ["-c", "import sys; sys.stdout.write('x' * 300000); sys.stderr.write('y' * 300000)"],
                        "cwd": str(tmp_path),
                    },
                )
            )
            assert unlimited["stdout"]["text"] == "x" * 300_000
            assert unlimited["stderr"]["text"] == "y" * 300_000
            assert unlimited["stdout"]["truncated"] is False
            assert unlimited["stderr"]["truncated"] is False
            result = _structured(
                await client.call_tool(
                    "run_process",
                    {
                        "executable": sys.executable,
                        "args": ["-c", "print('x' * 5000)"],
                        "cwd": str(tmp_path),
                        "stdout_limit": 100,
                        "stderr_limit": 100,
                    },
                )
            )
            assert result["exit_code"] == 0
            assert result["stdout"]["truncated"] is True
            assert len(result["stdout"]["text"]) <= 100
            assert result["stdout"]["total_bytes"] > 5_000

    asyncio.run(scenario())


def test_process_command_line_and_environment_values_are_complete(tmp_path: Path) -> None:
    long_argument = "q" * 5_000
    variable_name = "ALI_MCP_LONG_VALUE_TEST"
    variable_value = "v" * 12_000
    previous = os.environ.get(variable_name)
    os.environ[variable_name] = variable_value
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", long_argument], cwd=tmp_path)
    try:

        async def scenario() -> None:
            async with Client(create_server(), raise_exceptions=True) as client:
                info = _structured(await client.call_tool("process_info", {"pid": process.pid, "include_command_line": True}))
                assert long_argument in info["command_line"]
                assert info["command_line_truncated"] is False
                environment = _structured(
                    await client.call_tool(
                        "environment_variables",
                        {"name_filter": variable_name, "include_values": True, "max_items": 10},
                    )
                )
                assert environment["total_count"] == 1
                assert environment["items"][0]["value"] == variable_value
                assert environment["items"][0]["value_truncated"] is False

        asyncio.run(scenario())
    finally:
        process.terminate()
        process.wait(timeout=5)
        if previous is None:
            os.environ.pop(variable_name, None)
        else:
            os.environ[variable_name] = previous


def test_project_ast_tools(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "import fastapi\nfrom sqlalchemy import select\n\nclass Service:\n    async def run(self, value: int):\n        return value\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("fastapi\nsqlalchemy\n", encoding="utf-8")

    async def scenario() -> None:
        server = create_server()
        async with Client(server, raise_exceptions=True) as client:
            summary = _structured(await client.call_tool("project_summary", {"path": str(tmp_path), "max_files": 100}))
            assert "Python" in {item["name"] for item in summary["languages"]}
            assert "FastAPI" in summary["frameworks"]
            assert "SQLAlchemy" in summary["databases"]
            functions = _structured(await client.call_tool("find_function", {"path": str(tmp_path), "function_name": "Service.run"}))
            assert functions["total_count"] == 1
            assert functions["items"][0]["async"] is True
            context = _structured(await client.call_tool("code_context", {"path": str(tmp_path), "symbol": "Service.run"}))
            assert context["definitions"][0]["qualified_name"] == "app.Service.run"
            imports = _structured(await client.call_tool("find_imports", {"path": str(tmp_path), "module_filter": "sqlalchemy"}))
            assert imports["total_count"] == 1

    asyncio.run(scenario())


def test_pytest_include_output_is_unlimited_by_default(tmp_path: Path) -> None:
    target = tmp_path / "test_large_failure.py"
    target.write_text("def test_large_failure():\n    raise AssertionError('z' * 60000)\n", encoding="utf-8")

    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            result = _structured(
                await client.call_tool(
                    "run_pytest",
                    {"cwd": str(tmp_path), "targets": [str(target)], "include_output": True, "timeout_sec": 30},
                )
            )
            assert result["ok"] is False
            assert result["output_truncated"] is False
            assert result["output"]["truncated"] is False
            assert result["output"]["total_chars"] > 50_000
            assert "z" * 1_000 in result["output"]["text"]

    asyncio.run(scenario())


def test_affected_tests_returns_structured_mcp_decision(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("docs", encoding="utf-8")

    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            result = _structured(
                await client.call_tool(
                    "affected_tests",
                    {"repo": str(tmp_path), "changed_paths": ["README.md"]},
                )
            )
            assert result["decision"] == "none"
            assert result["complete"] is True

    asyncio.run(scenario())


def test_verify_changes_runs_a_bounded_syntax_plan_over_mcp(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("answer = 42\n", encoding="utf-8")

    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            result = _structured(
                await client.call_tool(
                    "verify_changes",
                    {"repo": str(tmp_path), "changed_paths": ["app.py"], "checks": ["syntax"]},
                )
            )
            assert result["ok"] is True
            assert result["stages"][0]["name"] == "syntax"
            assert result["stages"][0]["status"] == "passed"

    asyncio.run(scenario())


def test_memory_round_trip_stays_compact(tmp_path: Path) -> None:
    project_name = f"pytest-{tmp_path.name}"
    memory_path = SETTINGS.memory_dir / f"{project_name}.json"

    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            updated = _structured(
                await client.call_tool(
                    "memory_update",
                    {
                        "project_name": project_name,
                        "architecture_decisions": ["Use bounded structured responses."],
                        "important_paths": [str(tmp_path)],
                    },
                )
            )
            assert updated["counts"]["architecture_decisions"] == 1
            assert updated["revision"] == 1
            read = _structured(await client.call_tool("memory_read", {"project_name": project_name, "max_items": 1}))
            records = read["sections"]["architecture_decisions"]
            assert len(records) == 1
            assert records[0]["text"] == "Use bounded structured responses."
            assert records[0]["revision"] == 1
            assert read["revision"] == 1
            assert "raw_transcript" not in read

    try:
        asyncio.run(scenario())
    finally:
        memory_path.unlink(missing_ok=True)


def test_browser_session_errors_are_compact() -> None:
    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            result = _structured(await client.call_tool("browser_click", {"selector": "body", "session_id": "missing"}))
            assert result == {
                "ok": False,
                "operation": "browser_click",
                "error": "browser_session_not_found",
                "message": "Browser session 'missing' is not open.",
                "hint": "Call browser_open_page first.",
            }

    asyncio.run(scenario())
