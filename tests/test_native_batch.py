from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import threading
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mcp import Client


def _server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str = "read_batch") -> Any:
    from core import audit, config, heartbeat, registry
    from core.lifecycle import RuntimeLifecycle
    from tools.filesystem import batch, service
    from tools.language import registry as language_registry

    settings = replace(config.SETTINGS, state_dir=tmp_path / "state", memory_dir=tmp_path / "memory",
                       tool_profile=profile, persistent_enabled=False, extensions_enabled=False)
    for module in (audit, config, heartbeat, registry, service, language_registry):
        monkeypatch.setattr(module, "SETTINGS", settings)
    monkeypatch.setattr(batch, "RUNTIME_LIFECYCLE", RuntimeLifecycle())
    return registry.create_server()


def test_batch_profile_and_public_mcp_preserve_order_with_independent_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path, monkeypatch)
    (tmp_path / "one.txt").write_bytes(b"first\n")
    (tmp_path / "two.txt").write_bytes(b"second\n")

    async def scenario() -> None:
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            assert {tool.name for tool in tools} == {
                "native_read_batch", "read_file", "server_health", "discover_tool_domains", "recommend_tools",
                "execution_candidates", "execution_recommend",
            }
            result = await client.call_tool("native_read_batch", {"root": str(tmp_path), "max_parallel": 4, "items": [
                {"id": "one", "path": "one.txt"}, {"id": "missing", "path": "missing.txt"},
                {"id": "two", "path": "two.txt"},
            ]})
            wire = result.structured_content
            assert wire is not None and wire["ok"] is False and wire["complete"] is False, result
            assert [row["id"] for row in wire["items"]] == ["one", "missing", "two"]
            assert [row["status"] for row in wire["items"]] == ["success", "error", "success"]
            assert [wire["items"][i]["content"] for i in (0, 2)] == ["first\n", "second\n"]
            assert "missing.txt" not in json.dumps(wire)

    asyncio.run(scenario())


def test_actual_client_batch_replaces_four_read_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path, monkeypatch)
    for number in range(4):
        (tmp_path / f"{number}.txt").write_text(f"record-{number}", encoding="utf-8")
    original_call = Client.call_tool
    calls: list[str] = []

    async def counted_call(client: Client, name: str, arguments: dict[str, Any], **kwargs: Any) -> Any:
        calls.append(name)
        return await original_call(client, name, arguments, **kwargs)

    async def scenario() -> None:
        async with Client(server) as client:
            monkeypatch.setattr(Client, "call_tool", counted_call)
            direct = [
                (await client.call_tool("read_file", {"path": str(tmp_path / f"{n}.txt")})).structured_content
                for n in range(4)
            ]
            assert calls == ["read_file"] * 4
            calls.clear()
            result = await client.call_tool("native_read_batch", {
                "root": str(tmp_path), "items": [{"id": str(n), "path": f"{n}.txt"} for n in range(4)],
                "max_parallel": 2,
            })
            assert calls == ["native_read_batch"]
            wire = result.structured_content
            assert wire and wire["ok"] and wire["complete"]
            assert [entry["content"] for entry in wire["items"]] == [entry["content"] for entry in direct]

    asyncio.run(scenario())


@pytest.mark.parametrize("items", [
    [{"id": "a", "path": "one.txt", "kind": "run_process"}],
    [{"id": "a", "path": "one.txt"}, {"id": "a", "path": "one.txt"}],
    [{"id": str(i), "path": "one.txt"} for i in range(17)],
    [{"id": "a", "path": "one.txt", "offset": True}],
    [{"id": "a", "path": "one.txt", "encoding": "invalid-encoding-canary"}],
    [{"id": str(i), "path": "one.txt", "max_chars": 16384} for i in range(5)],
], ids=["kind", "duplicate", "count", "strict-bool", "encoding", "text-budget"])
def test_invalid_batch_never_dispatches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, items: list[dict[str, Any]]) -> None:
    from tools.filesystem import service

    server = _server(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "read_window", lambda *a, **k: pytest.fail("Invalid batch dispatched"))

    async def scenario() -> None:
        async with Client(server) as client:
            result = await client.call_tool("native_read_batch", {"root": str(tmp_path), "items": items})
            assert result.is_error or (result.structured_content and result.structured_content["ok"] is False)

    asyncio.run(scenario())


def test_direct_batch_call_enforces_the_same_validation_and_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.errors import ToolError
    from tools.filesystem.batch import BatchReader

    server = _server(tmp_path, monkeypatch, "coding_core")
    reader = BatchReader(server)
    with pytest.raises(ToolError) as denied:
        asyncio.run(reader.execute(str(tmp_path), [{"id": "a", "path": "one.txt"}]))
    assert denied.value.code == "batch_hidden_tool"
    with pytest.raises(ToolError) as invalid:
        asyncio.run(reader.execute(str(tmp_path), [{"id": "a", "path": "one.txt", "kind": "write_file"}]))
    assert invalid.value.code == "batch_invalid"


def test_source_budget_is_rejected_before_any_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.errors import ToolError
    from tools.filesystem import service

    server = _server(tmp_path, monkeypatch)
    for index in range(5):
        (tmp_path / f"{index}.txt").write_bytes(b"a" * (2 * 1024 * 1024))
    monkeypatch.setattr(service, "read_window", lambda *a, **k: pytest.fail("Source budget dispatched"))
    reader = server.native_read_batch_state
    with pytest.raises(ToolError) as denied:
        asyncio.run(reader.execute(str(tmp_path), [{"id": str(i), "path": f"{i}.txt"} for i in range(5)]))
    assert denied.value.code == "batch_source_budget"
    assert reader.snapshot()["active_batches"] == 0


def test_escape_network_and_link_paths_never_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.errors import ToolError
    from tools.filesystem import batch, service

    server = _server(tmp_path, monkeypatch)
    (tmp_path / "file.txt").write_text("secret-file-canary", encoding="utf-8")
    monkeypatch.setattr(service, "read_window", lambda *a, **k: pytest.fail("Denied path dispatched"))
    reader = server.native_read_batch_state
    result = asyncio.run(reader.execute(str(tmp_path), [{"id": "escape", "path": "../outside.txt"}]))
    assert result["ok"] is False and result["items"][0]["error"] == "batch_path_denied"
    with pytest.raises(ToolError) as denied:
        asyncio.run(reader.execute(r"\\untrusted\share", [{"id": "a", "path": "file.txt"}]))
    assert denied.value.code == "batch_path_denied"
    monkeypatch.setattr(batch, "unlinked_regular_file", lambda path: False)
    result = asyncio.run(reader.execute(str(tmp_path), [{"id": "link", "path": "file.txt"}]))
    assert result["items"][0]["error"] == "batch_path_denied"
    assert "secret-file-canary" not in json.dumps(result)


def test_utf8_control_text_obeys_aggregate_and_json_budgets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.filesystem.batch import MAX_RESPONSE_BYTES, MAX_TEXT_BYTES

    server = _server(tmp_path, monkeypatch)
    data = ("😀\x00\n" * 10_000).encode()
    (tmp_path / "unicode.txt").write_bytes(data)
    result = asyncio.run(server.native_read_batch_state.execute(str(tmp_path), [
        {"id": str(i), "path": "unicode.txt", "encoding": "utf-8"} for i in range(16)
    ], max_parallel=4))
    assert result["ok"] is True and result["complete"] is False and result["truncated"] is True, [
        (row["status"], row.get("error")) for row in result["items"]
    ]
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= MAX_RESPONSE_BYTES
    assert sum(len(row["content"].encode()) for row in result["items"]) <= MAX_TEXT_BYTES
    for row in result["items"]:
        assert row["read_sha256"] == hashlib.sha256(data).hexdigest()
        assert row["source_bytes"] == len(data)
        assert row["truncated"] and row["next_offset"] == len(row["content"])


def test_changed_source_is_not_published_as_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.filesystem import service

    server = _server(tmp_path, monkeypatch)
    path = tmp_path / "changing.txt"
    path.write_bytes(b"before")
    original = service._read_text_window

    def change(*args: Any, **kwargs: Any) -> dict[str, Any]:
        value = original(*args, **kwargs)
        path.write_bytes(b"after!")
        return value

    monkeypatch.setattr(service, "_read_text_window", change)
    result = asyncio.run(server.native_read_batch_state.execute(str(tmp_path), [{"id": "a", "path": path.name}]))
    assert result["ok"] is False and result["items"][0]["error"] == "batch_source_changed"
    assert "content" not in result["items"][0]


def test_existing_file_with_distinct_creation_and_write_times_is_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path, monkeypatch)
    path = tmp_path / "old.txt"
    path.write_bytes(b"unchanged")
    info = path.stat()
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns - 1_000_000_000))
    result = asyncio.run(server.native_read_batch_state.execute(str(tmp_path), [{"id": "a", "path": path.name}]))
    assert result["ok"] is True and result["items"][0]["content"] == "unchanged", result


def test_deadline_discards_late_result_and_prevents_next_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.filesystem import service

    server = _server(tmp_path, monkeypatch)
    (tmp_path / "file.txt").write_text("data", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    calls: list[bool] = []
    original = service.read_window

    def blocked(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(True)
        value = original(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return value

    monkeypatch.setattr(service, "read_window", blocked)

    async def scenario() -> None:
        reader = server.native_read_batch_state
        task = asyncio.create_task(reader.execute(str(tmp_path), [
            {"id": "a", "path": "file.txt"}, {"id": "b", "path": "file.txt"},
        ], timeout_sec=0.2))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.sleep(0.21)
            assert not task.done() and reader.snapshot()["active_reads"] == 1
        finally:
            release.set()
        result = await task
        assert result["ok"] is False and [row["status"] for row in result["items"]] == ["deadline", "deadline"]
        assert calls == [True] and reader.snapshot()["active_reads"] == 0

    asyncio.run(scenario())


def test_global_capacity_and_repeated_cancel_hold_slots_until_reads_finish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.errors import ToolError
    from tools.filesystem import service

    server = _server(tmp_path, monkeypatch)
    (tmp_path / "file.txt").write_text("data", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    lock = threading.Lock()
    calls: list[bool] = []
    original = service.read_window

    def blocked(*args: Any, **kwargs: Any) -> dict[str, Any]:
        with lock:
            calls.append(True)
            if len(calls) == 4:
                entered.set()
        assert release.wait(20)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "read_window", blocked)

    async def scenario() -> None:
        reader = server.native_read_batch_state
        # Exercise concurrency without conflating it with cold SQLite migration.
        await asyncio.to_thread(reader._store)
        # Each admitted batch must own a real blocked read before cancellation;
        # otherwise a scheduler may give all four slots to another batch.
        items = [{"id": "only", "path": "file.txt"}]
        tasks = [asyncio.create_task(reader.execute(str(tmp_path), items, max_parallel=1, timeout_sec=30)) for _ in range(4)]
        try:
            assert await asyncio.to_thread(entered.wait, 12), {
                "calls": len(calls), "snapshot": reader.snapshot(), "done": [task.done() for task in tasks],
                "stacks": [[(Path(row.filename).name, row.lineno, row.name) for row in traceback.extract_stack(frame)[-7:]]
                           for frame in sys._current_frames().values()],
            }
            assert reader.snapshot() == {"active_batches": 4, "active_reads": 4, "reserved_response_bytes": 4 * 192 * 1024}
            with pytest.raises(ToolError) as busy:
                await reader.execute(str(tmp_path), items)
            assert busy.value.code == "batch_busy" and len(calls) == 4
            tasks[0].cancel()
            await asyncio.sleep(0)
            tasks[0].cancel()
            await asyncio.sleep(0.01)
            assert not tasks[0].done() and reader.snapshot()["active_batches"] == 4
        finally:
            release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError)
        assert reader.snapshot() == {"active_batches": 0, "active_reads": 0, "reserved_response_bytes": 0}

    asyncio.run(scenario())


def test_profile_eligibility_is_rechecked_between_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.filesystem import service

    server = _server(tmp_path, monkeypatch)
    (tmp_path / "file.txt").write_text("data", encoding="utf-8")
    original = service.read_window
    calls: list[bool] = []

    def revoke(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(True)
        result = original(*args, **kwargs)
        server.native_allowlist = server.native_allowlist - {"read_file"}
        return result

    monkeypatch.setattr(service, "read_window", revoke)
    result = asyncio.run(server.native_read_batch_state.execute(str(tmp_path), [
        {"id": "a", "path": "file.txt"}, {"id": "b", "path": "file.txt"},
    ]))
    assert calls == [True]
    assert [row["status"] for row in result["items"]] == ["success", "error"]
    assert result["items"][1]["error"] == "batch_hidden_tool"


def test_racing_directory_link_cannot_escape_batch_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from tools.filesystem import service

    root = tmp_path / "root"
    child = root / "child"
    outside = tmp_path / "outside"
    child.mkdir(parents=True)
    outside.mkdir()
    (child / "data.txt").write_text("inside", encoding="utf-8")
    (outside / "data.txt").write_text("SECRET", encoding="utf-8")
    held = root / "held"
    server = _server(tmp_path, monkeypatch)
    original_file = service.unlinked_regular_file
    original_tree = service.unlinked_directory_tree
    swapped = False
    attempted = False

    def swap_after_check(path: Path) -> bool:
        nonlocal attempted, swapped
        valid = original_file(path)
        if valid and path == child / "data.txt" and not swapped:
            attempted = True
            child.rename(held)
            if os.name == "nt":
                subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(child), str(outside)],
                               check=True, capture_output=True)
            else:
                child.symlink_to(outside, target_is_directory=True)
            swapped = True
        return valid

    def restore_before_final_check(path: Path) -> bool:
        nonlocal swapped
        if swapped and path == child:
            if os.name == "nt":
                os.rmdir(child)
            else:
                child.unlink()
            held.rename(child)
            swapped = False
        return original_tree(path)

    monkeypatch.setattr(service, "unlinked_regular_file", swap_after_check)
    monkeypatch.setattr(service, "unlinked_directory_tree", restore_before_final_check)
    try:
        result = asyncio.run(server.native_read_batch_state.execute(
            str(root), [{"id": "a", "path": "child/data.txt"}]))
    finally:
        if swapped:
            if os.name == "nt":
                os.rmdir(child)
            else:
                child.unlink()
            held.rename(child)
    assert attempted
    assert "SECRET" not in json.dumps(result)
    assert result["ok"] is False
    child.rename(held)
    held.rename(child)
