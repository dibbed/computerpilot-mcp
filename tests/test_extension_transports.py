from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import anyio
import psutil
import pytest
from extension_test_utils import write_catalog_epoch
from mcp import Client, ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.subscriptions import ToolsListChanged
from test_extension_refresh import configured_manager

from core.process_ownership import spawn_owned_process

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "tests" / "fixtures" / "extension_core_server.py"
NAME = "external.refresh-fixture.read_value"


async def wait_for_provider_exit(pid: int, created: float) -> None:
    with anyio.fail_after(3):
        while psutil.pid_exists(pid):
            try:
                if psutil.Process(pid).create_time() != created:
                    return
            except psutil.NoSuchProcess:
                return
            await anyio.sleep(0.01)


def configuration(workspace: Path, mode: str = "readonly") -> None:
    manager = configured_manager(workspace, mode)
    owner = manager.get_owner("refresh-fixture")
    assert owner is not None
    (workspace / "extensions.json").write_text(json.dumps({
        "schema_version": 1, "extensions": [owner.definition.model_dump(mode="json")],
    }), encoding="utf-8")


@pytest.mark.parametrize("mode", ["--import-only", "--check"])
def test_actual_entrypoint_import_and_check_never_launch_extensions(tmp_path: Path, mode: str) -> None:
    configuration(tmp_path)
    result = subprocess.run(
        [sys.executable, str(ENTRYPOINT), str(tmp_path), mode], cwd=ROOT,
        capture_output=True, text=True, timeout=15, check=True,
    )
    data = json.loads(result.stdout)
    assert data.get("imported") or data.get("ok")
    if mode == "--check":
        assert data["tool_count"] > 0 and data["unique_tool_names"]
    assert not (tmp_path / "root_process").exists()


def test_actual_stdio_entrypoint_exposes_calls_and_reaps_provider_on_eof(tmp_path: Path) -> None:
    configuration(tmp_path)

    async def scenario() -> None:
        params = StdioServerParameters(command=sys.executable, args=[str(ENTRYPOINT), str(tmp_path)], cwd=str(ROOT))
        async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
            await client.initialize()
            with anyio.fail_after(10):
                while NAME not in {tool.name for tool in (await client.list_tools()).tools}:
                    await anyio.sleep(0.05)
            assert "discover_tool_domains" in {tool.name for tool in (await client.list_tools()).tools}
            arguments = {"file.name": "exact.dot", "value": "stdio"}
            result = await client.call_tool(NAME, arguments)
            assert not result.is_error and result.structured_content is not None
            assert result.structured_content["ok"]
            assert result.structured_content["structured_content"]["arguments"] == arguments
            denied = await client.call_tool("external.refresh-fixture.increment_value", {"amount": 1})
            assert denied.is_error and not (tmp_path / "counter.json").exists()
        process = json.loads((tmp_path / "root_process").read_text(encoding="utf-8"))
        await wait_for_provider_exit(process["pid"], process["created"])

    anyio.run(scenario)


def test_actual_http_entrypoint_exposes_modern_external_tools(tmp_path: Path) -> None:
    configuration(tmp_path, "catalog_change")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with (tmp_path / "server.log").open("wb") as log:
        owned = spawn_owned_process(
            command=[sys.executable, str(ENTRYPOINT), str(tmp_path), "--transport", "streamable-http", "--port", str(port)],
            cwd=ROOT, env=os.environ.copy(), stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=0, require_native=True,
        )
        try:
            async def scenario() -> None:
                with anyio.fail_after(10):
                    while True:
                        try:
                            stream = await anyio.connect_tcp("127.0.0.1", port)
                            await stream.aclose()
                            break
                        except OSError:
                            assert owned.process.poll() is None
                            await anyio.sleep(0.05)
                async with Client(f"http://127.0.0.1:{port}/mcp") as client:
                    assert client.protocol_version == "2026-07-28"
                    with anyio.fail_after(10):
                        while NAME not in {tool.name for tool in (await client.list_tools(cache_mode="refresh")).tools}:
                            await anyio.sleep(0.05)
                    arguments = {"file.name": "http.dot"}
                    native_names = {tool.name for tool in (await client.list_tools()).tools if not tool.name.startswith("external.")}
                    result = await client.call_tool(NAME, arguments)
                    assert not result.is_error and result.structured_content is not None
                    assert result.structured_content["structured_content"]["arguments"] == arguments
                    async with client.listen(tools_list_changed=True) as subscription:
                        write_catalog_epoch(tmp_path, 2)
                        with anyio.fail_after(5):
                            while True:
                                assert isinstance(await subscription.__anext__(), ToolsListChanged)
                                if NAME not in {tool.name for tool in (await client.list_tools()).tools}:
                                    break
                        write_catalog_epoch(tmp_path, 0)
                        with anyio.fail_after(5):
                            while True:
                                assert isinstance(await subscription.__anext__(), ToolsListChanged)
                                if NAME in {tool.name for tool in (await client.list_tools()).tools}:
                                    break
                    denied = await client.call_tool("external.refresh-fixture.increment_value", {"amount": 1})
                    assert denied.is_error and not (tmp_path / "counter.json").exists()
                async with Client(f"http://127.0.0.1:{port}/mcp", mode="legacy") as client:
                    tools = (await client.list_tools()).tools
                    assert {tool.name for tool in tools} == native_names
            anyio.run(scenario)
        finally:
            try:
                owned.process.terminate()
                owned.process.wait(timeout=10)
            finally:
                owned.terminate_tree(force=True)
                owned.close_ownership()
    process = json.loads((tmp_path / "root_process").read_text(encoding="utf-8"))
    anyio.run(wait_for_provider_exit, process["pid"], process["created"])
