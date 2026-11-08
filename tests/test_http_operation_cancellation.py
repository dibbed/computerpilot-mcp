"""Real streamable-HTTP cancellation contract (explicit operation ids)."""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psutil
import pytest

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _payload(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    return {}


def _token_processes(token: str) -> list[int]:
    rows: list[int] = []
    for process in psutil.process_iter(attrs=["pid", "cmdline"]):
        try:
            cmdline = process.info["cmdline"] or []
        except psutil.Error:
            continue
        if any(token in str(part) for part in cmdline):
            rows.append(int(process.info["pid"]))
    return rows


@pytest.fixture(scope="module")
def http_endpoint() -> Iterator[str]:
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable, "main.py", "--transport", "streamable-http",
            "--host", "127.0.0.1", "--port", str(port),
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 60
    try:
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                if process.poll() is not None:
                    pytest.fail(f"HTTP server exited with {process.returncode}")
                time.sleep(0.2)
        else:
            pytest.fail("HTTP server did not start")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def test_http_explicit_cancel_reaps_process_and_releases_admission(http_endpoint: str) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    token = f"cpm-httpcancel-{uuid.uuid4().hex[:10]}"
    operation_id = f"httpcancel-{uuid.uuid4().hex[:12]}"

    async def scenario() -> None:
        async with streamable_http_client(http_endpoint) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                call = asyncio.create_task(
                    session.call_tool(
                        "run_process",
                        {
                            "executable": sys.executable,
                            "args": ["-c", f"import time; time.sleep(120)  # {token}"],
                            "timeout_sec": 300,
                            "operation_id": operation_id,
                        },
                    )
                )
                deadline = time.monotonic() + 25
                while not _token_processes(token) and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
                assert _token_processes(token), "HTTP-dispatched command never started"

                cancel = _payload(
                    await session.call_tool("cancel_operation", {"operation_id": operation_id})
                )
                assert cancel["ok"] is True and cancel["cancelled"] is True

                started = time.monotonic()
                outcome = _payload(await asyncio.wait_for(call, timeout=25))
                assert time.monotonic() - started < 10
                assert outcome.get("error") == "command_cancelled"
                for _ in range(100):
                    if not _token_processes(token):
                        break
                    await asyncio.sleep(0.05)
                assert _token_processes(token) == []

                follow = _payload(
                    await session.call_tool(
                        "run_process",
                        {
                            "executable": sys.executable,
                            "args": ["-c", "print('released')"],
                            "timeout_sec": 60,
                        },
                    )
                )
                assert follow.get("ok") is True

    asyncio.run(scenario())


def test_http_cancel_rejects_malformed_and_reports_unknown_and_finished(http_endpoint: str) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    operation_id = f"httpterminal-{uuid.uuid4().hex[:12]}"

    async def scenario() -> None:
        async with streamable_http_client(http_endpoint) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                malformed = await session.call_tool("cancel_operation", {"operation_id": "short"})
                assert malformed.is_error is True, malformed
                assert "validation" in str(malformed.content).casefold()
                unknown = _payload(
                    await session.call_tool("cancel_operation", {"operation_id": "http-unknown-0001"})
                )
                assert unknown["ok"] is False and unknown["reason"] == "operation_not_found"

                completed = _payload(
                    await session.call_tool(
                        "run_process",
                        {
                            "executable": sys.executable,
                            "args": ["-c", "print('terminal')"],
                            "operation_id": operation_id,
                        },
                    )
                )
                assert completed.get("ok") is True
                finished = _payload(
                    await session.call_tool("cancel_operation", {"operation_id": operation_id})
                )
                assert finished["ok"] is True and finished["cancelled"] is False
                assert finished["reason"] == "operation_finished"

    asyncio.run(scenario())


def test_http_disconnect_does_not_cancel_side_effecting_command(
    http_endpoint: str, tmp_path: Path,
) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    proof = tmp_path / "disconnect-proof"
    command = f"import time; from pathlib import Path; time.sleep(2); Path({str(proof)!r}).write_text('survived')"

    async def run_and_abandon() -> None:
        async with streamable_http_client(http_endpoint) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                await session.call_tool(
                    "run_process",
                    {"executable": sys.executable, "args": ["-c", command], "timeout_sec": 60},
                )

    async def scenario() -> None:
        task = asyncio.create_task(run_and_abandon())
        await asyncio.sleep(0.8)
        task.cancel()
        try:
            await task
        except BaseException:
            pass
        for _ in range(80):
            if proof.is_file():
                break
            await asyncio.sleep(0.1)
        assert proof.is_file(), "HTTP disconnect cancelled a side-effecting command"

    asyncio.run(scenario())
