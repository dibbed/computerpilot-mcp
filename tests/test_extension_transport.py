from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import anyio
import mcp.types as types
import pytest
from mcp.client.session import ClientSession
from mcp.shared.message import SessionMessage

from core.extensions.transport import (
    MAX_FRAME_BYTES,
    MAX_JSON_DEPTH,
    MAX_PENDING_NOTIFICATIONS,
    MAX_STDERR_BYTES,
    BoundedByteFrameReader,
    BoundedStderrDrain,
    FrameDepthExceededError,
    FrameEncodingError,
    FrameOversizeError,
    NotificationFloodCoalescer,
    bounded_stdio_transport,
    parse_session_message,
    scan_json_depth,
)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_server.py"


def _fixture_env(workspace: Path) -> dict[str, str]:
    from core.extensions.config import build_extension_environment
    from core.extensions.models import ExtensionDefinition
    from core.platform import normalize_system

    workspace.mkdir(parents=True, exist_ok=True)
    model = ExtensionDefinition.model_validate_json(
        json.dumps({
            "extension_id": "fixture",
            "display_name": "fixture",
            "provider": "fixture",
            "command": sys.executable,
            "working_directory": str(Path.cwd()),
            "platforms": [normalize_system()],
            "environment": {"values": {"PYTHONUNBUFFERED": "1"}},
        })
    )
    env = {**build_extension_environment(model), "MCP_EXTENSIONS_ENABLED": "0"}
    paths = {
        "HOME": "home",
        "USERPROFILE": "home",
        "APPDATA": "config",
        "LOCALAPPDATA": "state",
        "XDG_CONFIG_HOME": "config",
        "XDG_STATE_HOME": "state",
        "TEMP": "temp",
        "TMP": "temp",
        "TMPDIR": "temp",
    }
    for name, directory in paths.items():
        path = workspace / directory
        path.mkdir(parents=True, exist_ok=True)
        env[name] = str(path)
    return env


def _initialize_line() -> bytes:
    return (
        json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "fixture-probe", "version": "1"},
            },
        })
        + "\n"
    ).encode("utf-8")


def _wait_marker(path: Path, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 10
    while not path.exists():
        assert process.poll() is None, "fixture exited before synchronization marker"
        assert time.monotonic() < deadline, "fixture synchronization deadline"
        time.sleep(0.01)


def test_scan_json_depth_valid_nesting() -> None:
    assert scan_json_depth('{"a": 1}') == 1
    assert scan_json_depth('{"a": {"b": [1, 2, {"c": 3}]}}') == 4
    assert scan_json_depth("[]") == 1
    assert scan_json_depth("123") == 0


def test_scan_json_depth_ignores_brackets_in_strings() -> None:
    # Brackets inside string values must not count toward structural depth
    assert scan_json_depth('{"text": "[[[nested brackets in string]]]" }') == 1
    assert scan_json_depth('{"text": "escaped \\"quote\\" and {braces}" }') == 1


def test_scan_json_depth_exceeded_raises_error() -> None:
    deep_json = "[" * 33 + "]" * 33
    with pytest.raises(FrameDepthExceededError, match="exceeds ceiling of 32"):
        scan_json_depth(deep_json, max_depth=MAX_JSON_DEPTH)


def test_bounded_frame_reader_extracts_complete_lines() -> None:
    reader = BoundedByteFrameReader()
    chunks = [
        b'{"jsonrpc": "2.0", "id": 1, "result": "first"}\n{"jsonrpc": "2.0",',
        b' "id": 2, "result": "second"}\n',
    ]
    frames1 = reader.feed_chunk(chunks[0])
    assert len(frames1) == 1
    assert "first" in frames1[0]

    frames2 = reader.feed_chunk(chunks[1])
    assert len(frames2) == 1
    assert "second" in frames2[0]


def test_bounded_frame_reader_split_multibyte_utf8() -> None:
    reader = BoundedByteFrameReader()
    # "پاسخ" contains non-ASCII UTF-8 multibyte characters
    persian_text = "پاسخ"
    payload = json.dumps({"result": persian_text}, ensure_ascii=False).encode("utf-8") + b"\n"

    # Split right in the middle of the first 2-byte UTF-8 character
    split_pos = payload.index("پ".encode()) + 1
    part1 = payload[:split_pos]
    part2 = payload[split_pos:]

    # Feed part 1: no newline, ends with incomplete UTF-8 byte
    frames1 = reader.feed_chunk(part1)
    assert frames1 == []

    # Feed part 2: completes the character and provides newline
    frames2 = reader.feed_chunk(part2)
    assert len(frames2) == 1
    parsed = json.loads(frames2[0])
    assert parsed["result"] == persian_text


def test_bounded_frame_reader_rejects_oversize_line() -> None:
    reader = BoundedByteFrameReader(max_frame_bytes=100)
    oversized = b"a" * 101 + b"\n"
    with pytest.raises(FrameOversizeError, match="exceeds limit"):
        reader.feed_chunk(oversized)


def test_bounded_frame_reader_rejects_no_newline_stream_before_allocation() -> None:
    reader = BoundedByteFrameReader(max_frame_bytes=1000)
    # Feed chunks without any newline
    reader.feed_chunk(b"x" * 600)
    assert reader.buffer_size == 600

    # Feeding another 500 bytes exceeds 1000 without newline -> immediate fail
    with pytest.raises(FrameOversizeError, match="Pending frame bytes"):
        reader.feed_chunk(b"x" * 500)

    # Buffer must not have grown to 1100
    assert reader.buffer_size == 600


def test_bounded_frame_reader_rejects_invalid_utf8_on_frame() -> None:
    reader = BoundedByteFrameReader()
    # Invalid UTF-8 sequence terminated by newline
    invalid_bytes = b"\xff\xfe\xfd\n"
    with pytest.raises(FrameEncodingError, match="Frame is not valid UTF-8"):
        reader.feed_chunk(invalid_bytes)


def test_bounded_frame_reader_large_base64_payload_within_limit() -> None:
    reader = BoundedByteFrameReader(max_frame_bytes=MAX_FRAME_BYTES)
    # Base64 string of 256 KiB
    large_base64 = "A" * (256 * 1024)
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"data": large_base64}}).encode("utf-8") + b"\n"
    frames = reader.feed_chunk(payload)
    assert len(frames) == 1
    parsed = json.loads(frames[0])
    assert len(parsed["result"]["data"]) == 256 * 1024


def test_parse_session_message_valid_and_malformed() -> None:
    valid_line = '{"jsonrpc": "2.0", "id": 1, "result": {"ok": true}}'
    msg = parse_session_message(valid_line)
    assert isinstance(msg, SessionMessage)

    malformed_line = "not-json"
    err = parse_session_message(malformed_line)
    assert isinstance(err, Exception)


def test_bounded_stderr_drain_caps_retained_bytes() -> None:
    drain = BoundedStderrDrain(max_bytes=MAX_STDERR_BYTES)
    # Feed 10 KiB of data in chunks
    for i in range(10):
        drain.feed_chunk((f"line {i:04d} " + "y" * 1000 + "\n").encode("utf-8"))

    tail = drain.get_tail()
    assert len(tail.encode("utf-8")) <= MAX_STDERR_BYTES
    assert drain.total_bytes_seen > 10000
    assert "line 0009" in tail


def test_bounded_stderr_drain_sanitizes_canaries_and_secrets() -> None:
    drain = BoundedStderrDrain()
    raw = (
        b"Starting worker with synthetic-canary-42\n"
        b"Connecting: Authorization: Bearer super_secret_token_1234567890\n"
        b"\x1b[31mError:\x1b[0m password: my_admin_password!\n"
    )
    drain.feed_chunk(raw)

    tail = drain.get_tail()
    assert "synthetic-canary" not in tail
    assert "super_secret_token" not in tail
    assert "my_admin_password" not in tail
    assert "\x1b[31m" not in tail
    assert "[REDACTED_SECRET]" in tail

    summary = drain.get_sanitized_summary()
    assert "[REDACTED_SECRET]" in summary
    assert "my_admin_password" not in summary


def test_notification_flood_coalescer_plateaus_queue() -> None:
    from mcp.types import JSONRPCNotification

    coalescer = NotificationFloodCoalescer(max_notifications=MAX_PENDING_NOTIFICATIONS)

    # 1. Test duplicate list_changed notification coalescing
    list_changed_msg = SessionMessage(
        JSONRPCNotification(jsonrpc="2.0", method="notifications/tools/list_changed")
    )

    accepted_count = 0
    for _ in range(50):
        if coalescer.push_message(list_changed_msg):
            accepted_count += 1

    # Only 1 list_changed should be in queue because duplicates are coalesced
    assert accepted_count == 1
    assert coalescer.pending_notification_count == 1

    # 2. Test logging notifications (non-coalescing) cap at maxlen
    for i in range(30):
        log_msg = SessionMessage(
            JSONRPCNotification(
                jsonrpc="2.0",
                method="notifications/message",
                params={"level": "info", "data": f"log {i}"},
            )
        )
        coalescer.push_message(log_msg)

    # Queue must strictly plateau at max_notifications (16)
    assert coalescer.pending_notification_count == MAX_PENDING_NOTIFICATIONS


def _spawn_fixture_and_call(tmp_path: Path, mode: str) -> tuple[subprocess.Popen[bytes], bytes]:
    process = subprocess.Popen(
        [sys.executable, str(FIXTURE_PATH.resolve()), "--workspace", str(tmp_path), "--mode", mode],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_fixture_env(tmp_path),
    )
    assert process.stdin and process.stdout
    process.stdin.write(_initialize_line())
    process.stdin.flush()
    init_res = json.loads(process.stdout.readline())
    assert init_res["id"] == 1
    process.stdin.write(
        b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"read_value","arguments":{}}}\n'
    )
    process.stdin.flush()
    _wait_marker(tmp_path / "call_received", process)
    output, _ = process.communicate(timeout=10)
    return process, output


@pytest.mark.parametrize("mode", ["malformed", "oversize", "no_newline", "nesting"])
def test_fixture_raw_modes_with_bounded_reader(tmp_path: Path, mode: str) -> None:
    sub_path = tmp_path / mode
    proc, output = _spawn_fixture_and_call(sub_path, mode)
    try:
        reader = BoundedByteFrameReader()
        if mode == "malformed":
            frames = reader.feed_chunk(output)
            assert len(frames) == 1
            assert frames[0] == "not-json"
            msg = parse_session_message(frames[0])
            assert isinstance(msg, Exception)
        elif mode in {"oversize", "no_newline"}:
            with pytest.raises(FrameOversizeError):
                reader.feed_chunk(output)
        elif mode == "nesting":
            with pytest.raises(FrameDepthExceededError):
                reader.feed_chunk(output)
    finally:
        if proc.poll() is None:
            proc.kill()


def test_fixture_split_utf8_with_bounded_reader(tmp_path: Path) -> None:
    process = subprocess.Popen(
        [sys.executable, str(FIXTURE_PATH.resolve()), "--workspace", str(tmp_path), "--mode", "split_utf8"],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_fixture_env(tmp_path),
    )
    try:
        assert process.stdin and process.stdout
        process.stdin.write(_initialize_line())
        process.stdin.flush()
        assert json.loads(process.stdout.readline())["id"] == 1
        process.stdin.write(
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
            b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"read_value","arguments":{}}}\n'
        )
        process.stdin.flush()
        _wait_marker(tmp_path / "split_ready", process)
        prefix = process.stdout.read(int((tmp_path / "split_ready").read_text(encoding="utf-8")))

        # Feed the prefix (ending mid-multibyte UTF-8) into BoundedByteFrameReader
        reader = BoundedByteFrameReader()
        frames_prefix = reader.feed_chunk(prefix)
        assert frames_prefix == [], "No complete frame yet"

        (tmp_path / "release").write_text("ready", encoding="utf-8")
        remainder = process.stdout.readline()
        (tmp_path / "close").write_text("ready", encoding="utf-8")

        # Feed remainder containing the rest of the multibyte character and newline
        frames = reader.feed_chunk(remainder)
        assert len(frames) == 1
        parsed = json.loads(frames[0])
        assert parsed["id"] == 2
        assert parsed["result"]["content"][0]["text"] == "پاسخ"

        msg = parse_session_message(frames[0])
        assert isinstance(msg, SessionMessage)
        process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()


def test_fixture_stderr_flood_with_bounded_drain(tmp_path: Path) -> None:
    # Stderr flood emits 128 synthetic canaries into stderr
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    drain = BoundedStderrDrain()

    async def scenario() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(FIXTURE_PATH.resolve()), "--workspace", str(tmp_path), "--mode", "stderr_flood"],
            cwd=str(tmp_path),
            env=_fixture_env(tmp_path),
        )
        stderr_file = tmp_path / "stderr.txt"
        with stderr_file.open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                await session.list_tools()
                await session.call_tool("read_value", {})

        # Feed the collected stderr into BoundedStderrDrain
        drain.feed_chunk(stderr_file.read_bytes())

    import asyncio

    asyncio.run(asyncio.wait_for(scenario(), timeout=20))

    tail = drain.get_tail()
    assert len(tail.encode("utf-8")) <= MAX_STDERR_BYTES
    assert "synthetic-canary" not in tail, "Synthetic canary must be sanitized"
    assert "[REDACTED_SECRET]" in tail


def test_bounded_stdio_transport_roundtrip(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with bounded_stdio_transport(
            [sys.executable, str(FIXTURE_PATH.resolve()), "--workspace", str(tmp_path), "--mode", "readonly"],
            cwd=tmp_path,
            env=_fixture_env(tmp_path),
        ) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert len(tools.tools) == 2
                res = await session.call_tool("read_value", {})
                first = res.content[0]
                assert isinstance(first, types.TextContent)
                assert first.text is not None

    anyio.run(scenario)


def test_bounded_stdio_transport_blocked_stdin_is_bounded(tmp_path: Path) -> None:
    async def scenario() -> None:
        start_t = time.monotonic()
        try:
            with anyio.fail_after(5.0):
                async with bounded_stdio_transport(
                    [sys.executable, str(FIXTURE_PATH.resolve()), "--workspace", str(tmp_path), "--mode", "blocked_stdin"],
                    cwd=tmp_path,
                    env=_fixture_env(tmp_path),
                    write_timeout=1.0,
                    shutdown_timeout=1.5,
                ) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
        except (TimeoutError, Exception):
            pass
        elapsed = time.monotonic() - start_t
        # Blocked stdin must not hang forever; must cleanly terminate within bounds
        assert elapsed < 7.0, f"Blocked stdin elapsed {elapsed}s exceeded limit"

    anyio.run(scenario)


def test_bounded_stdio_transport_ignore_cancel_forced_reap(tmp_path: Path) -> None:
    async def scenario() -> None:
        start_t = time.monotonic()
        async with bounded_stdio_transport(
            [sys.executable, str(FIXTURE_PATH.resolve()), "--workspace", str(tmp_path), "--mode", "ignore_cancel"],
            cwd=tmp_path,
            env=_fixture_env(tmp_path),
            shutdown_timeout=2.0,
        ) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
        elapsed = time.monotonic() - start_t
        # Courtesy cancel stall broken by Job Object / POSIX group force reap
        assert elapsed < 6.0, f"Ignore cancel cleanup took {elapsed}s, stall was not broken"

    anyio.run(scenario)


def test_bounded_stdio_transport_ab_isolation(tmp_path: Path) -> None:
    path_a = tmp_path / "provider_a"
    path_b = tmp_path / "provider_b"
    path_a.mkdir(parents=True, exist_ok=True)
    path_b.mkdir(parents=True, exist_ok=True)

    b_succeeded = False

    async def run_b() -> None:
        nonlocal b_succeeded
        async with bounded_stdio_transport(
            [sys.executable, str(FIXTURE_PATH.resolve()), "--workspace", str(path_b), "--mode", "readonly"],
            cwd=path_b,
            env=_fixture_env(path_b),
        ) as (read_b, write_b):
            async with ClientSession(read_b, write_b) as session_b:
                await session_b.initialize()
                tools_b = await session_b.list_tools()
                assert len(tools_b.tools) == 2
                b_succeeded = True

    async def run_a() -> None:
        try:
            with anyio.fail_after(3.0):
                async with bounded_stdio_transport(
                    [sys.executable, str(FIXTURE_PATH.resolve()), "--workspace", str(path_a), "--mode", "delayed"],
                    cwd=path_a,
                    env=_fixture_env(path_a),
                    shutdown_timeout=1.0,
                ) as (read_a, write_a):
                    async with ClientSession(read_a, write_a) as session_a:
                        await session_a.initialize()
                        await session_a.call_tool("read_value", {})
        except (TimeoutError, Exception):
            pass

    async def scenario() -> None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_a)
            tg.start_soon(run_b)

    anyio.run(scenario)
    assert b_succeeded, "Provider B must succeed independently even while Provider A is delayed"


def test_bounded_stdio_transport_root_exit_child_reaps_entire_tree(tmp_path: Path) -> None:
    import psutil

    async def scenario() -> None:
        try:
            async with bounded_stdio_transport(
                [sys.executable, str(FIXTURE_PATH.resolve()), "--workspace", str(tmp_path), "--mode", "root_exit_child"],
                cwd=tmp_path,
                env=_fixture_env(tmp_path),
                shutdown_timeout=2.0,
            ) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    await session.call_tool("read_value", {})
        except Exception:
            pass

    anyio.run(scenario)

    descendants = []
    for name in ("child_ready", "grandchild_ready"):
        marker = tmp_path / name
        if marker.exists():
            record = json.loads(marker.read_text(encoding="utf-8"))
            try:
                descendants.append(psutil.Process(record["pid"]))
            except psutil.NoSuchProcess:
                pass
    if descendants:
        _, alive = psutil.wait_procs(descendants, timeout=5)
        assert not alive, f"Descendant processes remained alive after transport exit: {alive}"
