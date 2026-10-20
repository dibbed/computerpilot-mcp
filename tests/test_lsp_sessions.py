"""Harness for the deterministic persistent LSP fixture (test-only)."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest

from core.errors import ToolError
from core.process_ownership import OwnedProcess
from core.windows_job import NativeOwnershipRequiredError
from tools.language import transport
from tools.language.lsp import LspClient
from tools.language.providers import LspProvider

FIXTURE = Path(__file__).parent / "fixtures" / "persistent_lsp_server.py"


def manager_provider(root: Path, mode: str = "normal", *, delay: float | None = None) -> tuple[LspProvider, Path]:
    entrypoint = root / "managed-fixture.py"
    log = root / "managed-fixture.log"
    source = FIXTURE.read_text(encoding="utf-8")
    anchor = '        result: dict[str, object] = {"method": method, "initialized": initialized}'
    assert source.count(anchor) == 1
    source = source.replace(
        anchor, '        _append_log(log_path, "dispatch:" + str(params.get("query", "")))\n' + anchor[:-1] + ', "pid": os.getpid()}',
    )
    arguments = ["--mode", mode, "--log", str(log)]
    source = source.replace("raise SystemExit(main())", f"sys.argv += {arguments!r}\n    raise SystemExit(main())")
    if delay is not None:
        source = source.replace("SLOW_DELAY_SECONDS = 0.2", f"SLOW_DELAY_SECONDS = {delay!r}")
    entrypoint.write_text(source, encoding="utf-8")
    executable = Path(sys.executable).resolve()
    return LspProvider(
        "pyright", ("python",), executable, hashlib.sha256(executable.read_bytes()).hexdigest(),
        entrypoint, hashlib.sha256(entrypoint.read_bytes()).hexdigest(), "fixture", (root,),
    ), log


def manager_pids(results: list[object]) -> set[int]:
    pids: set[int] = set()
    for result in results:
        assert isinstance(result, dict)
        assert type(result["pid"]) is int
        pids.add(result["pid"])
    return pids


@pytest.mark.parametrize("count", [1, 10, 100])
def test_manager_warm_requests_reuse_one_real_process(tmp_path: Path, count: int) -> None:
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path)
    manager = LspSessionManager((provider,), generation="test")
    assert manager.snapshot()["spawn_count"] == 0
    assert not log.exists()

    async def scenario() -> None:
        try:
            results = [
                await manager.arequest(tmp_path, "python", "workspace/symbol", {"query": str(i)}, timeout_sec=5)
                for i in range(count)
            ]
            pids = manager_pids(results)
            assert len(pids) == 1
            assert manager.snapshot()["spawn_count"] == 1
            assert manager.snapshot()["reuse_count"] == count - 1
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)
        for pid in pids:
            _wait_for_exit(pid, timeout=3)
            assert not _process_alive(pid)

    asyncio.run(scenario())
    assert log.read_text(encoding="utf-8").splitlines().count("initialize") == 1
    assert "shutdown" in log.read_text(encoding="utf-8")
    assert manager.snapshot()["quarantined"] == 0


def test_manager_concurrent_first_requests_have_one_owner(tmp_path: Path) -> None:
    from tools.language.sessions import LspSessionManager

    provider, _log = manager_provider(tmp_path)
    manager = LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        try:
            results = await asyncio.gather(*[
                manager.arequest(tmp_path, "python", "workspace/symbol", {"query": str(i)}, timeout_sec=5) for i in range(8)
            ])
            assert len(manager_pids(results)) == 1
            assert manager.snapshot()["spawn_count"] == 1
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())


def test_manager_different_workspaces_have_distinct_processes(tmp_path: Path) -> None:
    from tools.language.sessions import LspSessionManager

    roots = [tmp_path / "a", tmp_path / "b"]
    for root in roots:
        root.mkdir()
    providers = tuple(manager_provider(root)[0] for root in roots)
    manager = LspSessionManager(providers, generation="test")

    async def scenario() -> None:
        try:
            results = await asyncio.gather(*[
                manager.arequest(root, "python", "workspace/symbol", {"query": "x"}, timeout_sec=5) for root in roots
            ])
            assert len(manager_pids(results)) == 2
        finally:
            for root in roots:
                await asyncio.to_thread(manager.close_workspace, root)

    asyncio.run(scenario())


def test_manager_invalid_request_never_spawns(tmp_path: Path) -> None:
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path)
    manager = LspSessionManager((provider,), generation="test")
    with pytest.raises(ToolError):
        asyncio.run(manager.arequest(tmp_path, "python", "workspace/executeCommand", {}, timeout_sec=5))
    assert manager.snapshot()["spawn_count"] == 0
    assert not log.exists()


async def manager_until(predicate: Any) -> None:
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, "session did not reach the required bounded state"
        await asyncio.sleep(0.005)


def test_manager_cancelled_and_expired_queued_requests_never_dispatch(tmp_path: Path) -> None:
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path, "slow", delay=1)
    manager = LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        active = asyncio.create_task(manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "active"}, timeout_sec=5))
        try:
            await manager_until(lambda: manager.snapshot()["in_flight"] == 1)
            queued = asyncio.create_task(manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "cancelled"}, timeout_sec=5))
            await manager_until(lambda: manager.snapshot()["queue_depth"] == 1)
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
            with pytest.raises(asyncio.TimeoutError):
                await manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "expired"}, timeout_sec=0.03)
            await active
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())
    lines = log.read_text(encoding="utf-8").splitlines()
    assert "dispatch:active" in lines
    assert "dispatch:cancelled" not in lines
    assert "dispatch:expired" not in lines


def test_manager_cancel_inflight_retires_without_replay(tmp_path: Path) -> None:
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path, "slow", delay=2)
    manager = LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        request = asyncio.create_task(manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "cancel"}, timeout_sec=5))
        try:
            await manager_until(lambda: manager.snapshot()["in_flight"] == 1)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            await manager_until(lambda: manager.snapshot()["active"] == 0)
            assert manager.snapshot()["spawn_count"] == 1
            assert manager.snapshot()["in_flight"] == 0
            with pytest.raises(ToolError):
                await manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "never-replay"}, timeout_sec=5)
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())
    assert "dispatch:never-replay" not in log.read_text(encoding="utf-8")


def test_manager_crash_fails_queued_without_replay(tmp_path: Path) -> None:
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path, "crash")
    manager = LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        try:
            results = await asyncio.gather(*[
                manager.arequest(tmp_path, "python", "workspace/symbol", {"query": str(i)}, timeout_sec=5) for i in range(5)
            ], return_exceptions=True)
            assert all(isinstance(result, ToolError) for result in results)
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)
        assert manager.snapshot()["spawn_count"] == 1
        assert manager.snapshot()["in_flight"] == 0
        assert manager.snapshot()["quarantined"] == 0

    asyncio.run(scenario())
    assert log.read_text(encoding="utf-8").splitlines().count("crash") == 1


def test_manager_quarantined_owner_retains_key_and_capacity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.language import sessions

    provider, _log = manager_provider(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    process = ProtocolProcess(b"")

    def blocked_open(*args: object) -> OwnedProcess:
        entered.set()
        assert release.wait(5)
        return cast(OwnedProcess, process)

    monkeypatch.setattr(sessions, "open_transport", blocked_open)
    manager = sessions.LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        request = asyncio.create_task(manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "x"}, timeout_sec=5))
        try:
            await manager_until(entered.is_set)
            manager.close_workspace(tmp_path, timeout_sec=0)
            assert manager.snapshot()["quarantined"] == 1
            assert manager.snapshot()["active"] == 1
            with pytest.raises(ToolError):
                await manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "denied"}, timeout_sec=5)
            release.set()
            with pytest.raises(ToolError):
                await request
            await manager_until(lambda: manager.snapshot()["active"] == 0)
        finally:
            release.set()
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())
    assert process.released


@pytest.mark.parametrize("failure", ["unavailable", "rss"])
def test_manager_resource_denial_prevents_business_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    from tools.language import sessions

    provider, log = manager_provider(tmp_path)
    if failure == "unavailable":
        def unavailable(*args: object) -> object:
            raise sessions.psutil.AccessDenied()

        monkeypatch.setattr(sessions.psutil, "Process", unavailable)
    else:
        monkeypatch.setattr(sessions, "_MAX_RSS_BYTES", 1)
    manager = sessions.LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        try:
            with pytest.raises(ToolError):
                await manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "denied"}, timeout_sec=5)
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)
        assert manager.snapshot()["quarantined"] == 0

    asyncio.run(scenario())
    assert not log.exists() or "dispatch:" not in log.read_text(encoding="utf-8")


def test_manager_idle_cleanup_releases_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.language import sessions

    provider, log = manager_provider(tmp_path)
    monkeypatch.setattr(sessions, "_IDLE_SEC", 0.01)
    monkeypatch.setattr(sessions, "_SAMPLE_SEC", 0.01)
    manager = sessions.LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        try:
            result = await manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "x"}, timeout_sec=5)
            assert isinstance(result, dict)
            await manager_until(lambda: manager.snapshot()["active"] == 0)
            assert not _process_alive(result["pid"])
            assert manager.snapshot()["quarantined"] == 0
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())
    assert "shutdown" in log.read_text(encoding="utf-8")


def test_manager_queue_and_session_admission_are_bounded(tmp_path: Path) -> None:
    from tools.language.sessions import LspSessionManager

    provider, _log = manager_provider(tmp_path, "slow", delay=60)
    roots = [tmp_path / str(index) for index in range(5)]
    for root in roots:
        root.mkdir()
    manager = LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        requests: list[asyncio.Task[object]] = []
        try:
            for root in roots[:4]:
                requests.append(asyncio.create_task(
                    manager.arequest(root, "python", "workspace/symbol", {"query": "hold"}, timeout_sec=30),
                ))
            await manager_until(lambda: manager.snapshot()["in_flight"] == 4)
            with pytest.raises(ToolError) as failure:
                await manager.arequest(roots[4], "python", "workspace/symbol", {"query": "fifth"}, timeout_sec=5)
            assert failure.value.code == "lsp_capacity_exceeded"
            for root in roots[:4]:
                for index in range(8):
                    requests.append(asyncio.create_task(
                        manager.arequest(root, "python", "workspace/symbol", {"query": str(index)}, timeout_sec=30),
                    ))
            await manager_until(lambda: manager.snapshot()["queue_depth"] == 32)
            with pytest.raises(ToolError) as failure:
                await manager.arequest(roots[0], "python", "workspace/symbol", {"query": "overflow"}, timeout_sec=5)
            assert failure.value.code == "lsp_capacity_exceeded"
            assert manager.snapshot()["spawn_count"] == 4
        finally:
            for request in requests:
                request.cancel()
            await asyncio.gather(*requests, return_exceptions=True)
            for root in roots:
                await asyncio.to_thread(manager.close_workspace, root)
        assert manager.snapshot()["active"] == 0
        assert manager.snapshot()["in_flight"] == 0
        assert manager.snapshot()["queue_depth"] == 0

    asyncio.run(scenario())


def test_manager_construction_and_snapshot_do_not_start_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.language import sessions

    calls: list[object] = []
    monkeypatch.setattr(threading.Thread, "start", lambda thread: calls.append(thread))
    monkeypatch.setattr(sessions, "open_transport", lambda *args: calls.append(args))
    manager = sessions.LspSessionManager((), generation="test")
    assert manager.snapshot()["active"] == 0
    assert calls == []


class ProtocolStream(io.BytesIO):
    """Finite fragmented input that stays open until its process exits."""

    def __init__(self, data: bytes, stopped: threading.Event, ready: threading.Event) -> None:
        super().__init__(data)
        self.stopped = stopped
        self.ready = ready
        self.changed = threading.Condition()
        self.body_reads = 0
        self.chunk_size = 3 if len(data) < 10_000 else 4096

    def read(self, size: int | None = -1) -> bytes:
        self.body_reads += 1
        return super().read(min(size, self.chunk_size) if size is not None and size >= 0 else self.chunk_size)

    def readline(self, size: int | None = -1) -> bytes:
        self.ready.wait(5)
        with self.changed:
            while not self.stopped.is_set():
                line = super().readline(size)
                if line:
                    return line
                self.changed.wait(0.05)
            return b""

    def feed(self, data: bytes) -> None:
        with self.changed:
            offset = self.tell()
            self.seek(0, 2)
            self.write(data)
            self.seek(offset)
            self.changed.notify_all()


class ProtocolInput(io.BytesIO):
    def __init__(self, ready: threading.Event) -> None:
        super().__init__()
        self.ready = ready
        self.on_write: Any = None

    def write(self, data: Any) -> int:
        size = super().write(data)
        self.ready.set()
        if self.on_write is not None:
            self.on_write(data)
        return size


class ProtocolProcess:
    def __init__(self, data: bytes) -> None:
        self.stopped = threading.Event()
        self.ready = threading.Event()
        self.stdin = ProtocolInput(self.ready)
        self.stdout = ProtocolStream(data, self.stopped, self.ready)
        self.stderr = io.BytesIO()
        self.terminated = False
        self.released = False

    def poll(self) -> int | None:
        return 0 if self.stopped.is_set() else None

    def wait(self, timeout: float | None = None) -> int:
        self.stopped.set()
        self.ready.set()
        return 0

    def terminate_tree(self, *, force: bool = True) -> dict[str, Any]:
        self.terminated = True
        self.stopped.set()
        return {}

    def close_ownership(self) -> None:
        self.released = True


def protocol_transport(data: bytes, **bounds: int) -> tuple[Any, ProtocolProcess]:
    process = ProtocolProcess(data)
    return transport.LspTransport(cast(OwnedProcess, process), **bounds), process


def transport_result(channel: Any) -> dict[str, object]:
    return channel.receive(deadline=time.monotonic() + 2, cancel=threading.Event())


def test_transport_fragmented_body_and_old_response_correlation() -> None:
    from tools.language.lsp import _frame

    channel, process = protocol_transport(b"".join(_frame(row) for row in (
        {"id": 1, "result": "first"}, {"id": 1, "result": "stale"},
    )))
    try:
        channel.send({"id": 1, "method": "initialize"})
        assert transport_result(channel)["result"] == "first"
        channel.send({"id": 2, "method": "workspace/symbol"})
        process.stdout.feed(_frame({"id": 2, "result": "second"}))
        assert transport_result(channel)["result"] == "second"
        with pytest.raises(ToolError):
            channel.send({"id": 2, "method": "workspace/symbol"})
    finally:
        assert channel.close()
    assert process.released


@pytest.mark.parametrize("data", [
    b"Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}",
    b"Content-Length: +2\r\n\r\n{}",
    b"Content-Length: 10000000\r\n\r\n",
    b"Content-Length: 2\r\n\r\n[]",
    b"Content-Length: 1\r\n\r\n\xff",
])
def test_transport_malformed_frame_is_closed_without_body_overread(data: bytes) -> None:
    channel, process = protocol_transport(data)
    try:
        with pytest.raises(ToolError):
            channel.send({"id": 1, "method": "initialize"})
            transport_result(channel)
        if b"10000000" in data:
            assert process.stdout.body_reads == 0
        assert process.stopped.wait(1)
    finally:
        assert channel.close()


@pytest.mark.parametrize("kind", ["count", "bytes", "depth", "future", "invalid_id", "invalid_method"])
def test_transport_untrusted_output_limits(kind: str) -> None:
    from tools.language.lsp import _frame

    messages: list[dict[str, Any]] = [{"method": "$/progress", "params": {}}] * 65
    if kind == "bytes":
        messages = [{"method": "$/progress", "params": {"value": "x" * 3_000_000}}] * 3
    elif kind == "depth":
        value: Any = None
        for _ in range(33):
            value = [value]
        messages = [{"id": 1, "result": value}]
    elif kind == "future":
        messages = [{"id": 2, "result": None}]
    elif kind == "invalid_id":
        messages = [{"id": True, "result": None}]
    elif kind == "invalid_method":
        messages = [{"method": 42}]
    channel, process = protocol_transport(b"".join(_frame(row) for row in messages))
    try:
        channel.send({"id": 1, "method": "initialize"})
        if kind in {"count", "bytes"}:
            assert process.stopped.wait(3)
        with pytest.raises(ToolError):
            transport_result(channel)
    finally:
        assert channel.close()


def test_transport_server_callbacks_never_dispatch_edits() -> None:
    from tools.language.lsp import _frame, _parse_frames

    channel, process = protocol_transport(b"".join(_frame(row) for row in (
        {"id": "config", "method": "workspace/configuration", "params": {"items": [{}, {}]}},
        {"id": "edit", "method": "workspace/applyEdit", "params": {"edit": {"changes": {}}}},
        {"id": 1, "result": "safe"},
    )))
    try:
        channel.send({"id": 1, "method": "initialize"})
        assert transport_result(channel)["result"] == "safe"
        replies = _parse_frames(process.stdin.getvalue(), 4_000_000)
        assert replies[1] == {"jsonrpc": "2.0", "id": "config", "result": [None, None]}
        assert replies[2]["error"]["code"] == -32601
    finally:
        assert channel.close()


def test_transport_notification_flood_is_bounded_even_with_fast_consumer() -> None:
    from tools.language.lsp import _frame

    callback = _frame({"id": "callback", "method": "workspace/configuration", "params": {"items": []}})
    channel, process = protocol_transport(callback)

    def next_callback(data: bytes) -> None:
        if b'"result":' in data:
            process.stdout.feed(callback)

    process.stdin.on_write = next_callback
    try:
        channel.send({"id": 1, "method": "initialize"})
        with pytest.raises(ToolError, match="malformed"):
            transport_result(channel)
        assert channel._notifications == 257
        assert process.terminated
    finally:
        assert channel.close()


def test_transport_request_budget_resets_only_between_requests() -> None:
    from tools.language.lsp import _frame

    channel, process = protocol_transport(_frame({"id": 1, "result": "x" * 350}), max_message_bytes=500, max_request_bytes=600)
    try:
        channel.send({"id": 1, "method": "initialize"})
        transport_result(channel)
        first_wire = channel.wire_bytes
        channel.send({"id": 2, "method": "workspace/symbol"})
        process.stdout.feed(_frame({"id": 2, "result": "x" * 350}))
        transport_result(channel)
        assert channel.wire_bytes > first_wire
        channel.send({"id": 3, "method": "workspace/symbol"})
        with pytest.raises(ToolError, match="total limit"):
            channel.send({"method": "initialized", "params": "x" * 550})
    finally:
        assert channel.close()


def test_transport_future_response_before_next_dispatch_is_rejected() -> None:
    from tools.language.lsp import _frame

    channel, process = protocol_transport(_frame({"id": 1, "result": None}))
    try:
        channel.send({"id": 1, "method": "initialize"})
        transport_result(channel)
        process.stdout.feed(_frame({"id": 2, "result": "unsolicited"}))
        assert process.stopped.wait(1)
        with pytest.raises(ToolError):
            channel.send({"id": 2, "method": "workspace/symbol"})
    finally:
        assert channel.close()


def test_transport_cancel_timeout_and_multiple_owners_are_denied() -> None:
    channel, process = protocol_transport(b"")
    try:
        channel.send({"id": 1, "method": "initialize"})
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(ToolError, match="cancelled"):
            channel.receive(deadline=time.monotonic() + 1, cancel=cancel)
        with pytest.raises(ToolError, match="timeout"):
            channel.receive(deadline=time.monotonic() - 1, cancel=threading.Event())
        denied: list[str] = []

        def wrong_owner() -> None:
            try:
                channel.send({"method": "exit"})
            except ToolError as error:
                denied.append(error.code)

        writer = threading.Thread(target=wrong_owner)
        writer.start()
        writer.join(1)
        assert denied == ["lsp_protocol_error"]
        with pytest.raises(ToolError):
            channel.send({"id": 2, "method": "workspace/symbol"})
    finally:
        assert channel.close()
        assert channel.close()
    assert process.released


def test_transport_fragmented_headers_over_real_pipe() -> None:
    from tools.language.lsp import _frame

    read_fd, write_fd = os.pipe()
    framed = _frame({"id": 1, "result": "😀"})

    def fragmented_writer() -> None:
        with os.fdopen(write_fd, "wb", buffering=0) as output:
            for byte in framed:
                output.write(bytes([byte]))

    writer = threading.Thread(target=fragmented_writer)
    writer.start()
    try:
        with os.fdopen(read_fd, "rb") as source:
            message, size = transport._read_message(source, 500)
            assert message == {"id": 1, "result": "😀"}
            assert size == len(framed)
    finally:
        writer.join(2)
    assert not writer.is_alive()


def test_transport_partial_progress_is_bounded_and_never_reported_complete() -> None:
    from tools.language.lsp import _frame

    channel, _process = protocol_transport(b"".join(_frame(row) for row in (
        {"method": "$/progress", "params": {"token": "partial", "value": [{"name": "source"}]}},
        {"id": 1, "result": []},
    )))
    try:
        channel.send({"id": 1, "method": "workspace/symbol", "params": {"partialResultToken": "partial"}})
        result = transport_result(channel)
        assert result["complete"] is False
        assert result["partialResults"] == [[{"name": "source"}]]
        assert channel.notification_counts == {"textDocument/publishDiagnostics": 0, "$/progress": 1}
    finally:
        assert channel.close()


@pytest.mark.parametrize("bounds", [{"max_message_bytes": 4_000_001}, {"max_total_bytes": 8_000_001}])
def test_client_invalid_transport_bounds_never_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bounds: dict[str, int]) -> None:
    launched: list[object] = []
    monkeypatch.setattr(transport, "_open_command_transport", lambda *args: launched.append(args))
    with pytest.raises(ValueError):
        LspClient([sys.executable, str(FIXTURE)], tmp_path, **bounds)
    assert not launched


@pytest.mark.parametrize("failed_start", [1, 2])
def test_transport_thread_start_failure_closes_owned_process(monkeypatch: pytest.MonkeyPatch, failed_start: int) -> None:
    process = ProtocolProcess(b"")
    starts = 0
    original = threading.Thread.start

    def fail_start(thread: threading.Thread) -> None:
        nonlocal starts
        starts += 1
        if starts == failed_start:
            raise RuntimeError("private-start-canary")
        original(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    with pytest.raises(ToolError, match="could not be started"):
        transport.LspTransport(cast(OwnedProcess, process))
    assert process.released
    assert process.poll() == 0


class FixtureSession:
    def __init__(
        self,
        root: Path,
        *,
        mode: str = "normal",
        log: Path | None = None,
        protocol_process: Any = None,
    ) -> None:
        if protocol_process is not None:
            self.process: Any = protocol_process
        else:
            command = [sys.executable, str(FIXTURE), "--mode", mode]
            if log is not None:
                command += ["--log", str(log)]
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=root,
            )
        self._next_id = 0
        self.server_requests: list[dict[str, Any]] = []

    def _write(self, payload: dict[str, Any]) -> None:
        if self.process.poll() is not None or self.process.stdin is None or self.process.stdin.closed:
            raise AssertionError("fixture process is not running")
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        self.process.stdin.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _read(self) -> dict[str, Any]:
        assert self.process.stdout is not None
        length = 0
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise AssertionError("fixture closed stdout before sending a frame")
            if line in {b"\r\n", b"\n"}:
                break
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1])
        body = self.process.stdout.read(length)
        message = json.loads(body)
        assert isinstance(message, dict)
        return message

    def _read_with_timeout(self, timeout: float) -> dict[str, Any]:
        received: dict[str, Any] = {}
        failure: list[BaseException] = []

        def read_once() -> None:
            try:
                received["message"] = self._read()
            except BaseException as exc:  # pragma: no cover - diagnostic path
                failure.append(exc)

        reader = threading.Thread(target=read_once, daemon=True)
        reader.start()
        reader.join(timeout)
        if reader.is_alive():
            self.close()
            raise AssertionError(f"fixture did not answer within {timeout}s")
        if failure:
            raise failure[0]
        if "message" not in received:
            raise AssertionError("fixture produced no message")
        return received["message"]

    def request(self, method: str, params: dict[str, Any], *, read_timeout: float = 10.0) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            message = self._read_with_timeout(read_timeout)
            if "id" in message and message.get("id") != request_id and "method" in message:
                self.server_requests.append(message)
                continue
            return message

    def close(self) -> None:
        stdin = self.process.stdin
        if stdin is not None and not stdin.closed:
            try:
                stdin.close()
            except OSError:  # pragma: no cover - cleanup best effort
                pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(self.process.pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - forced cleanup
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def _process_alive(pid: int) -> bool:
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - different user
        return True
    return True


def _wait_for_exit(pid: int, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while _process_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)


def test_fixture_reuses_process_for_two_requests(tmp_path: Path) -> None:
    session = FixtureSession(tmp_path)
    try:
        initialized = session.request("initialize", {})
        capabilities = initialized["result"]["capabilities"]
        assert capabilities["hoverProvider"] is True
        assert capabilities["definitionProvider"] is True
        assert capabilities["referencesProvider"] is True
        assert capabilities["callHierarchyProvider"] is True
        assert capabilities["textDocumentSync"] == {"openClose": True, "change": 1}
        session.notify("initialized", {})
        first = session.request("test/inspect", {})
        second = session.request("test/inspect", {})
    finally:
        session.close()

    # ``sys.executable`` may be a venv launcher shim on Windows, so ``Popen.pid``
    # is not necessarily the interpreter PID reported by the fixture; process
    # reuse is proven by a constant reported PID across both requests.
    assert first["result"]["pid"] == second["result"]["pid"]
    assert second["result"]["request_count"] > first["result"]["request_count"]
    assert len(first["result"]["fixture_digest"]) == 64
    assert first["result"]["held_requests"] == 0


def test_fixture_enforces_document_versions(tmp_path: Path) -> None:
    session = FixtureSession(tmp_path)
    target = tmp_path / "service.py"
    target.write_text("value = 1\n", encoding="utf-8")
    uri = target.as_uri()
    try:
        session.request("initialize", {})
        session.notify("initialized", {})

        early = session.request(
            "textDocument/didChange",
            {"textDocument": {"uri": uri, "version": 1}, "contentChanges": []},
        )
        assert early["error"]["code"] == -32602

        session.notify(
            "textDocument/didOpen",
            {"textDocument": {"uri": uri, "languageId": "python", "version": 1, "text": "value = 1\n"}},
        )
        opened = session.request("test/inspect", {})
        assert opened["result"]["documents"][uri] == 1

        changed = session.request(
            "textDocument/didChange",
            {"textDocument": {"uri": uri, "version": 2}, "contentChanges": [{"text": "value = 2\n"}]},
        )
        assert changed["result"] is None

        stale = session.request(
            "textDocument/didChange",
            {"textDocument": {"uri": uri, "version": 2}, "contentChanges": [{"text": "value = 3\n"}]},
        )
        assert stale["error"]["code"] == -32602

        inspected = session.request("test/inspect", {})
        assert inspected["result"]["documents"][uri] == 2
        assert inspected["result"]["open_count"] == 1
    finally:
        session.close()


def test_fixture_shutdown_exits(tmp_path: Path) -> None:
    session = FixtureSession(tmp_path)
    try:
        session.request("initialize", {})
        session.notify("initialized", {})
        shutdown = session.request("shutdown", {})
        assert shutdown["result"] is None
        session.notify("exit", {})
        assert session.process.wait(timeout=5) == 0
    finally:
        session.close()

    assert session.process.poll() == 0
    with pytest.raises(AssertionError):
        session.request("test/inspect", {})


def test_fixture_close_leaves_no_descendant_alive(tmp_path: Path) -> None:
    session = FixtureSession(tmp_path)
    try:
        session.request("initialize", {})
        session.notify("initialized", {})
        real_pid = session.request("test/inspect", {})["result"]["pid"]
    finally:
        session.close()

    assert session.process.poll() == 0
    _wait_for_exit(real_pid)
    assert not _process_alive(real_pid)


def test_lsp_native_ownership_denial_prevents_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    popen_calls: list[Any] = []

    def deny(command: Any, **kwargs: Any) -> Any:
        calls.append({"command": list(command), "kwargs": kwargs})
        raise NativeOwnershipRequiredError("test denial")

    def record_popen(*args: Any, **kwargs: Any) -> Any:
        popen_calls.append(args)
        raise AssertionError("raw Popen must not run without native ownership")

    monkeypatch.setattr(transport, "spawn_owned_process", deny)
    monkeypatch.setattr(subprocess, "Popen", record_popen)

    client = LspClient([sys.executable, str(FIXTURE)], tmp_path)
    with pytest.raises(ToolError) as denied:
        client.request("workspace/symbol", {"query": "Service"})
    assert denied.value.code == "lsp_ownership_unavailable"
    assert len(calls) == 1
    assert calls[0]["kwargs"]["require_native"] is True
    assert popen_calls == []

    provider = LspProvider(
        provider_id="pyright",
        languages=("python",),
        executable=Path(sys.executable),
        executable_sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
        entrypoint=None,
        entrypoint_sha256=None,
        declared_version="test",
        trusted_roots=(tmp_path,),
    )
    with pytest.raises(ToolError) as provider_denied:
        transport.open_transport(provider, tmp_path)
    assert provider_denied.value.code == "lsp_ownership_unavailable"
    assert len(calls) == 2
    assert calls[1]["kwargs"]["require_native"] is True
    assert popen_calls == []


def test_lsp_timeout_reaps_descendant(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "hang_with_child.py"
    script.write_text(
        "from __future__ import annotations\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    client = LspClient([sys.executable, str(script), str(pid_file)], tmp_path, timeout_sec=3)
    with pytest.raises(ToolError, match="timeout"):
        client.request("workspace/symbol", {"query": "x"})

    assert pid_file.exists(), "fixture child did not record its PID before the timeout"
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    _wait_for_exit(child_pid, timeout=5)
    assert not _process_alive(child_pid)


def test_lsp_environment_excludes_secret_and_loader_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_LSP_TEST_CANARY", "canary-secret-9f3a")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "canary-pythonpath"))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "canary-pythonhome"))
    monkeypatch.setenv("NODE_OPTIONS", "--require=canary")
    monkeypatch.setenv("NODE_PATH", str(tmp_path / "canary-nodepath"))
    monkeypatch.setenv("__CF_USER_TEXT_ENCODING", "canary-parent-corefoundation")
    monkeypatch.setenv("PATH", str(tmp_path / "canary-path") + os.pathsep + os.environ.get("PATH", ""))
    launched_environments: list[dict[str, str]] = []
    original_spawn = transport.spawn_owned_process

    def capture_spawn(*args: Any, **kwargs: Any) -> Any:
        launched_environments.append(dict(kwargs["env"]))
        return original_spawn(*args, **kwargs)

    monkeypatch.setattr(transport, "spawn_owned_process", capture_spawn)

    dump = tmp_path / "env.json"
    probe = tmp_path / "dump_env.py"
    probe.write_text(
        "import json\n"
        "import os\n"
        "import sys\n"
        "with open(sys.argv[1], 'w', encoding='utf-8') as handle:\n"
        "    json.dump(dict(os.environ), handle, sort_keys=True)\n",
        encoding="utf-8",
    )

    process = transport._open_command_transport([sys.executable, str(probe), str(dump)], tmp_path)
    assert transport.close_transport(process, timeout_sec=2.0) is True
    assert process.returncode == 0

    env = {str(key).upper(): str(value) for key, value in json.loads(dump.read_text(encoding="utf-8")).items()}
    assert "MCP_LSP_TEST_CANARY" not in env
    assert "PYTHONPATH" not in env
    assert "PYTHONHOME" not in env
    assert "NODE_OPTIONS" not in env
    assert "NODE_PATH" not in env
    assert "canary-path" not in env.get("PATH", "")
    assert env["PATH"].split(os.pathsep) == [str(Path(sys.executable).parent)]
    os_vars = ("SystemRoot", "WINDIR", "TEMP", "TMP") if os.name == "nt" else ("LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")
    for name in os_vars:
        parent_value = os.environ.get(name)
        if parent_value is not None:
            assert env.get(name.upper()) == parent_value
    allowed = {"PATH"} | {name.upper() for name in os_vars}
    assert len(launched_environments) == 1
    assert {name.upper() for name in launched_environments[0]} <= allowed
    assert "canary-parent-corefoundation" not in env.values()
    # Darwin may synthesize this key after launch; it is never forwarded in the explicit environment.
    runtime_added = {"__CF_USER_TEXT_ENCODING"} if sys.platform == "darwin" else set()
    assert set(env) <= allowed | runtime_added


def test_lsp_error_and_stderr_canaries_do_not_escape(tmp_path: Path) -> None:
    error_canary = "canary-error-4f7d1b"
    stderr_canary = "canary-stderr-8e2a5c"
    script = tmp_path / "error_server.py"
    script.write_text(
        "import json\n"
        "import sys\n"
        "import time\n"
        f"sys.stderr.write('{stderr_canary}\\n')\n"
        "sys.stderr.flush()\n"
        "length = 0\n"
        "while True:\n"
        "    line = sys.stdin.buffer.readline()\n"
        "    if line in {b'\\r\\n', b'\\n', b''}:\n"
        "        break\n"
        "    if line.lower().startswith(b'content-length:'):\n"
        "        length = int(line.split(b':', 1)[1])\n"
        "body = sys.stdin.buffer.read(length)\n"
        "message = json.loads(body)\n"
        f"response = {{'jsonrpc': '2.0', 'id': message['id'], 'error': {{'code': -32000, 'message': '{error_canary}'}}}}\n"
        "frame = json.dumps(response, separators=(',', ':')).encode()\n"
        "sys.stdout.buffer.write(f'Content-Length: {len(frame)}\\r\\n\\r\\n'.encode() + frame)\n"
        "sys.stdout.buffer.flush()\n"
        "time.sleep(5)\n",
        encoding="utf-8",
    )

    client = LspClient([sys.executable, str(script)], tmp_path, timeout_sec=5)
    with pytest.raises(ToolError) as failure:
        client.request("workspace/symbol", {"query": "x"})
    assert failure.value.code == "lsp_request_failed"
    rendered = "\n".join(
        [
            failure.value.message,
            repr(failure.value),
            repr(client.capabilities),
            json.dumps(client.capabilities, default=str),
        ]
    )
    assert error_canary not in rendered
    assert stderr_canary not in rendered
    assert "_last_stderr" not in client.capabilities


def test_lsp_outside_uri_denied_before_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def deny_spawn(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("invalid document must be denied before spawning")

    monkeypatch.setattr(transport, "_open_command_transport", deny_spawn)
    client = LspClient([sys.executable, str(FIXTURE)], tmp_path)
    with pytest.raises(ToolError, match="outside"):
        client.request("textDocument/hover", {"textDocument": {"uri": (tmp_path.parent / "outside.py").as_uri()}})


def test_lsp_transport_rechecks_provider_before_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def deny_spawn(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("changed provider must not spawn")

    monkeypatch.setattr(transport, "spawn_owned_process", deny_spawn)
    provider = LspProvider(
        provider_id="pyright", languages=("python",), executable=Path(sys.executable),
        executable_sha256="0" * 64, entrypoint=None, entrypoint_sha256=None,
        declared_version="test", trusted_roots=(tmp_path,),
    )
    with pytest.raises(ToolError) as failure:
        transport.open_transport(provider, tmp_path)
    assert failure.value.code == "lsp_provider_changed"


def test_lsp_incomplete_cleanup_never_returns_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = transport.close_transport

    def incomplete(*args: Any, **kwargs: Any) -> bool:
        assert original(*args, **kwargs)
        return False

    monkeypatch.setattr(transport, "close_transport", incomplete)
    with pytest.raises(ToolError) as failure:
        LspClient([sys.executable, str(FIXTURE)], tmp_path).request("workspace/symbol", {"query": "x"})
    assert failure.value.code == "lsp_cleanup_incomplete"


def test_lsp_document_open_read_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "large.py"
    reads: list[int] = []
    cap = 2 * 1024 * 1024

    class Document(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            assert size is not None and 0 <= size <= cap + 1, "document read must be bounded before allocation"
            reads.append(size)
            return super().read(size)

    original = Path.open

    def bounded_document(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == target:
            return Document(b"x" * (cap + 1))
        return original(path, *args, **kwargs)

    def deny_spawn(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("oversize document must not spawn")

    monkeypatch.setattr(Path, "open", bounded_document)
    monkeypatch.setattr(transport, "_open_command_transport", deny_spawn)
    with pytest.raises(ToolError) as failure:
        LspClient([sys.executable, str(FIXTURE)], tmp_path).request(
            "textDocument/documentSymbol", {"textDocument": {"uri": target.as_uri()}},
        )
    assert failure.value.code == "lsp_document_too_large"
    assert reads == [cap + 1]
