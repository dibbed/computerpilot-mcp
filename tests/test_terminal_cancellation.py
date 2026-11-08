from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path

import psutil
import pytest
from mcp import Client

from core import config
from core.errors import ToolError
from core.executor import run_bounded
from core.registry import create_server


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


def _wait_until_gone(token: str, timeout: float = 15.0) -> list[int]:
    deadline = time.monotonic() + timeout
    remaining = _token_processes(token)
    while remaining and time.monotonic() < deadline:
        time.sleep(0.1)
        remaining = _token_processes(token)
    return remaining


def test_run_bounded_cancel_event_reaps_owned_tree(tmp_path: Path) -> None:
    token = f"cpm-cancel-{uuid.uuid4().hex[:10]}"
    cancel = threading.Event()
    timer = threading.Timer(0.5, cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(ToolError) as raised:
            run_bounded(
                [sys.executable, "-c", f"import time; time.sleep(60)  # {token}"],
                cwd=tmp_path,
                timeout_sec=60,
                cancel_event=cancel,
            )
    finally:
        timer.cancel()
    assert raised.value.code == "command_cancelled"
    assert time.monotonic() - started < 30
    assert _wait_until_gone(token) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group cancellation probe")
def test_cancelled_mcp_call_reaps_owned_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = f"cpm-cancel-{uuid.uuid4().hex[:10]}"
    # Keep the token inside the Python program. A POSIX shell may exec the
    # final command, which would discard a shell-comment token before psutil
    # can observe the owned process.
    script = f"{sys.executable} -c 'import time; token=\"{token}\"; time.sleep(60)'"
    monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, state_dir=tmp_path))

    async def scenario() -> None:
        async with Client(create_server()) as client:
            call = asyncio.create_task(
                client.call_tool("run_shell", {"command_text": script, "cwd": str(tmp_path), "timeout_sec": 120})
            )
            deadline = time.monotonic() + 20
            while not _token_processes(token) and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert _token_processes(token), "owned command never started"
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
            for _ in range(150):
                if not _token_processes(token):
                    break
                await asyncio.sleep(0.1)
            assert _token_processes(token) == [], "cancelled call left an owned process tree behind"

    asyncio.run(scenario())
