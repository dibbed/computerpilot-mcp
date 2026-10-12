from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client

FIXTURE = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"


def test_fixture_public_session_pages_exact_args_and_one_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert FIXTURE.is_file()
    monkeypatch.setenv("FIXTURE_PARENT_SECRET", "synthetic-never-inherited")
    monkeypatch.setenv("MCP_EXTENSIONS_ENABLED", "1")
    monkeypatch.setenv("MCP_EXTENSIONS_CONFIG", str(tmp_path / "real-config-must-not-be-read.json"))

    async def scenario() -> None:
        params = StdioServerParameters(
            command=sys.executable, args=[str(FIXTURE.resolve()), "--workspace", str(tmp_path), "--page-size", "1"],
            cwd=str(tmp_path), env=_fixture_env(tmp_path),
        )
        with (tmp_path / "stderr.txt").open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write), ClientSession(read, write) as session:
                info = await session.initialize()
                assert info.server_info.name == "computerpilot-local-fixture"
                first = await session.list_tools()
                second = await session.list_tools(params=types.PaginatedRequestParams(cursor=first.next_cursor))
                assert [tool.name for tool in first.tools + second.tools] == ["increment_value", "read_value"]
                assert second.next_cursor is None
                result = await _call(session, "read_value", {"value": "checked", "file.name": "exact.property"})
                assert result.structured_content == {"value": "checked", "arguments": {"value": "checked", "file.name": "exact.property"}}
                changed = await _call(session, "increment_value", {"amount": 1})
                assert changed.structured_content and changed.structured_content["dispatch_count"] == 1

    asyncio.run(asyncio.wait_for(scenario(), timeout=20))
    records = [json.loads(line) for line in (tmp_path / "effects.jsonl").read_text(encoding="utf-8").splitlines()]
    assert records == [{"dispatch_count": 1, "arguments": {"amount": 1}}]
    assert all(json.loads((tmp_path / "environment_isolated").read_text(encoding="utf-8")).values())


async def _call(session: ClientSession, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    return await session.send_request(
        types.CallToolRequest(method="tools/call", params=types.CallToolRequestParams(name=name, arguments=arguments)),
        types.CallToolResult, request_read_timeout_seconds=10,
    )


@pytest.mark.parametrize("mode", [
    "duplicate", "cursor_loop", "schema_remote_ref", "schema_regex", "recursive_schema", "dangerous_annotation",
])
def test_fixture_catalog_adversaries_are_real_sdk_data(tmp_path: Path, mode: str) -> None:
    async def scenario() -> None:
        params = StdioServerParameters(
            command=sys.executable, args=[str(FIXTURE.resolve()), "--workspace", str(tmp_path), "--mode", mode],
            cwd=str(tmp_path), env=_fixture_env(tmp_path),
        )
        with (tmp_path / "stderr.txt").open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                if mode == "duplicate":
                    assert len(listed.tools) != len({tool.name for tool in listed.tools})
                elif mode == "cursor_loop":
                    second = await session.list_tools(params=types.PaginatedRequestParams(cursor=listed.next_cursor))
                    assert listed.next_cursor == second.next_cursor == "0"
                elif mode == "dangerous_annotation":
                    assert listed.tools[0].annotations and listed.tools[0].annotations.read_only_hint is True
                else:
                    schema = next(tool.input_schema for tool in listed.tools if tool.name == "read_value")
                    key = {"schema_remote_ref": "$ref", "schema_regex": "pattern", "recursive_schema": "$defs"}[mode]
                    assert key in json.dumps(schema)

    asyncio.run(asyncio.wait_for(scenario(), timeout=20))


@pytest.mark.parametrize("mode", ["malformed", "oversize", "no_newline", "nesting"])
def test_fixture_bad_wire_modes_have_finite_exact_payloads(tmp_path: Path, mode: str) -> None:
    process = subprocess.Popen(
        [sys.executable, str(FIXTURE.resolve()), "--workspace", str(tmp_path), "--mode", mode],
        cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=_fixture_env(tmp_path),
    )
    try:
        assert process.stdin and process.stdout
        process.stdin.write(_initialize_line())
        process.stdin.flush()
        assert json.loads(process.stdout.readline())["id"] == 1
        process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
                            b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"read_value","arguments":{}}}\n')
        process.stdin.flush()
        _wait_marker(tmp_path / "call_received", process)
        output, _ = process.communicate(timeout=10)
        if mode == "malformed":
            assert output == b"not-json\n"
        elif mode in {"oversize", "no_newline"}:
            assert len(output) == 2 * 1024 * 1024 + (2 if mode == "oversize" else 1)
            assert output.endswith(b"\n") is (mode == "oversize")
        else:
            assert output == b"[" * 33 + b"]" * 33 + b"\n"
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def _fixture_env(workspace: Path) -> dict[str, str]:
    from core.extensions.config import build_extension_environment
    from core.extensions.models import ExtensionDefinition
    from core.platform import normalize_system

    model = ExtensionDefinition.model_validate_json(json.dumps({
        "extension_id": "fixture", "display_name": "fixture", "provider": "fixture", "command": sys.executable,
        "working_directory": str(Path.cwd()), "platforms": [normalize_system()],
        "environment": {"values": {"PYTHONUNBUFFERED": "1"}},
    }))
    env = {**build_extension_environment(model), "MCP_EXTENSIONS_ENABLED": "0"}
    paths = {"HOME": "home", "USERPROFILE": "home", "APPDATA": "config", "LOCALAPPDATA": "state",
             "XDG_CONFIG_HOME": "config", "XDG_STATE_HOME": "state", "TEMP": "temp", "TMP": "temp", "TMPDIR": "temp"}
    for name, directory in paths.items():
        path = workspace / directory
        path.mkdir(exist_ok=True)
        env[name] = str(path)
    return env


def _initialize_line() -> bytes:
    return (json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "fixture-probe", "version": "1"},
    }}) + "\n").encode()


def _wait_marker(path: Path, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 10
    while not path.exists():
        assert process.poll() is None, "fixture exited before synchronization marker"
        assert time.monotonic() < deadline, "fixture synchronization deadline"
        time.sleep(0.01)


@pytest.mark.parametrize("mode", ["catalog_change", "notification_flood", "stderr_flood", "unknown", "remove"])
def test_fixture_refresh_and_flood_modes_complete(tmp_path: Path, mode: str) -> None:
    async def scenario() -> None:
        params = StdioServerParameters(
            command=sys.executable, args=[str(FIXTURE.resolve()), "--workspace", str(tmp_path), "--mode", mode],
            cwd=str(tmp_path), env=_fixture_env(tmp_path),
        )
        with (tmp_path / "stderr.txt").open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = [tool.name for tool in listed.tools]
                if mode == "unknown":
                    assert "unclassified_tool" in names
                elif mode == "remove":
                    assert "read_value" not in names
                elif mode == "catalog_change":
                    (tmp_path / "catalog_epoch").write_text("1", encoding="utf-8")
                    changed = await session.list_tools()
                    assert next(t.input_schema for t in changed.tools if t.name == "read_value") != listed.tools[1].input_schema
                    (tmp_path / "catalog_epoch").write_text("2", encoding="utf-8")
                    assert "read_value" not in [t.name for t in (await session.list_tools()).tools]
                else:
                    assert (await _call(session, "read_value", {})).structured_content
        if mode == "stderr_flood":
            assert (tmp_path / "stderr.txt").read_text(encoding="utf-8").count("synthetic-canary") == 128

    asyncio.run(asyncio.wait_for(scenario(), timeout=20))


def test_fixture_split_utf8_uses_controlled_fragment_boundary(tmp_path: Path) -> None:
    process = subprocess.Popen(
        [sys.executable, str(FIXTURE.resolve()), "--workspace", str(tmp_path), "--mode", "split_utf8"],
        cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_fixture_env(tmp_path),
    )
    try:
        assert process.stdin and process.stdout
        process.stdin.write(_initialize_line())
        process.stdin.flush()
        assert json.loads(process.stdout.readline())["id"] == 1
        process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
                            b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"read_value","arguments":{}}}\n')
        process.stdin.flush()
        _wait_marker(tmp_path / "split_ready", process)
        prefix = process.stdout.read(int((tmp_path / "split_ready").read_text(encoding="utf-8")))
        with pytest.raises(UnicodeDecodeError):
            prefix.decode("utf-8")
        (tmp_path / "release").write_text("ready", encoding="utf-8")
        message = json.loads(prefix + process.stdout.readline())
        assert message["id"] == 2 and message["result"]["content"][0]["text"] == "پاسخ"
        (tmp_path / "close").write_text("ready", encoding="utf-8")
        process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


@pytest.mark.parametrize("mode", ["startup_timeout", "handshake_failed", "blocked_stdin"])
def test_fixture_startup_modes_are_controlled_and_reaped(tmp_path: Path, mode: str) -> None:
    process = subprocess.Popen(
        [sys.executable, str(FIXTURE.resolve()), "--workspace", str(tmp_path), "--mode", mode],
        cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_fixture_env(tmp_path),
    )
    try:
        assert process.stdin
        process.stdin.write(_initialize_line())
        process.stdin.flush()
        _wait_marker(tmp_path / "startup_ready", process)
        if mode == "handshake_failed":
            output, _ = process.communicate(timeout=10)
            assert json.loads(output)["result"]["protocolVersion"] == "invalid-fixture"
        else:
            assert process.poll() is None
            (tmp_path / "close").write_text("ready", encoding="utf-8")
            output, _ = process.communicate(timeout=10)
            assert output == b""
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_fixture_lost_response_counter_survives_fresh_runtime_without_replay(tmp_path: Path) -> None:
    process = subprocess.Popen(
        [sys.executable, str(FIXTURE.resolve()), "--workspace", str(tmp_path), "--mode", "uncertain_mutation"],
        cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_fixture_env(tmp_path),
    )
    try:
        assert process.stdin and process.stdout
        process.stdin.write(_initialize_line())
        process.stdin.flush()
        assert json.loads(process.stdout.readline())["id"] == 1
        process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
                            b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"increment_value","arguments":{"amount":1}}}\n')
        process.stdin.flush()
        _wait_marker(tmp_path / "effect_committed", process)
        output, _ = process.communicate(timeout=10)
        assert output == b"" and (tmp_path / "counter.json").read_text(encoding="utf-8") == "1"
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)

    async def restarted() -> None:
        params = StdioServerParameters(
            command=sys.executable, args=[str(FIXTURE.resolve()), "--workspace", str(tmp_path)],
            cwd=str(tmp_path), env=_fixture_env(tmp_path),
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            await _call(session, "read_value", {})

    asyncio.run(asyncio.wait_for(restarted(), timeout=20))
    assert len((tmp_path / "effects.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_fixture_root_exit_retains_owned_child_and_grandchild_cleanup(tmp_path: Path) -> None:
    import os

    import psutil

    from core.process_ownership import spawn_owned_process

    owned = spawn_owned_process(
        [sys.executable, str(FIXTURE.resolve()), "--workspace", str(tmp_path), "--mode", "root_exit_child"],
        cwd=tmp_path, env=_fixture_env(tmp_path), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        assert owned.ownership_backend in {"windows_job", "posix_process_group"}
        assert owned.stdin and owned.stdout
        owned.stdin.write(_initialize_line())
        owned.stdin.flush()
        assert json.loads(owned.stdout.readline())["id"] == 1
        owned.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
                          b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"read_value","arguments":{}}}\n')
        owned.stdin.flush()
        assert owned.wait(timeout=10) == 6
        descendants = []
        for name in ("child_ready", "grandchild_ready"):
            record = json.loads((tmp_path / name).read_text(encoding="utf-8"))
            child = psutil.Process(record["pid"])
            assert child.create_time() == record["created"] and child.is_running()
            descendants.append(child)
        owned.terminate_tree(force=True)
        _, alive = psutil.wait_procs(descendants, timeout=5)
        assert not alive
    finally:
        owned.terminate_tree(force=True)
        owned.close_ownership()
        for stream in (owned.stdin, owned.stdout, owned.stderr):
            if stream:
                stream.close()


@pytest.mark.parametrize("mode", ["delayed", "call_timeout", "ignore_cancel"])
def test_fixture_wait_and_cancel_modes_have_explicit_release(tmp_path: Path, mode: str) -> None:
    process = subprocess.Popen(
        [sys.executable, str(FIXTURE.resolve()), "--workspace", str(tmp_path), "--mode", mode],
        cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_fixture_env(tmp_path),
    )
    try:
        assert process.stdin and process.stdout
        process.stdin.write(_initialize_line())
        process.stdin.flush()
        assert json.loads(process.stdout.readline())["id"] == 1
        process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
                            b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"read_value","arguments":{}}}\n')
        process.stdin.flush()
        _wait_marker(tmp_path / "call_received", process)
        if mode == "ignore_cancel":
            process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":2,"reason":"fixture"}}\n')
            process.stdin.flush()
            _wait_marker(tmp_path / "cancel_ignored", process)
        assert process.poll() is None
        (tmp_path / "close").write_text("ready", encoding="utf-8")
        process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
