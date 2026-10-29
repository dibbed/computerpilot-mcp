from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import threading
import traceback
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mcp import Client


@pytest.fixture(autouse=True)
def close_batch_readers(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from tools.filesystem.batch import BatchReader

    readers: list[BatchReader] = []
    original = BatchReader.__init__

    def initialize(reader: BatchReader, server: Any) -> None:
        original(reader, server)
        readers.append(reader)

    monkeypatch.setattr(BatchReader, "__init__", initialize)
    yield
    for reader in readers:
        reader.close()


def _server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str = "read_batch", *, enabled: bool = False) -> Any:
    from core import audit, config, heartbeat, registry
    from core.lifecycle import RuntimeLifecycle
    from tools.filesystem import batch, service
    from tools.language import registry as language_registry

    settings = replace(config.SETTINGS, state_dir=tmp_path / "state", memory_dir=tmp_path / "memory",
                       tool_profile=profile, native_read_batch_enabled=enabled, persistent_enabled=False, extensions_enabled=False)
    for module in (audit, config, heartbeat, registry, service, language_registry):
        monkeypatch.setattr(module, "SETTINGS", settings)
    monkeypatch.setattr(batch, "RUNTIME_LIFECYCLE", RuntimeLifecycle())
    return registry.create_server()


def test_full_batch_opt_in_preserves_existing_descriptors_and_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = _server(tmp_path, monkeypatch, "full")
    before = {tool.name: tool.model_dump(mode="json", by_alias=True, exclude_none=True)
              for tool in asyncio.run(baseline.list_tools())}
    assert "native_read_batch" not in before
    server = _server(tmp_path, monkeypatch, "full", enabled=True)
    after = {tool.name: tool.model_dump(mode="json", by_alias=True, exclude_none=True)
             for tool in asyncio.run(server.list_tools())}
    assert after.keys() - before.keys() == {"native_read_batch"}
    assert {name: after[name] for name in before} == before
    def encode(value: Any) -> bytes:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(encode(list(after.values()))) - len(encode(list(before.values()))) <= 4096
    health = asyncio.run(server.call_tool("server_health", {}))
    assert health.structured_content["snapshot_reason"] == "runtime_not_started"
    from core import registry

    async def collected(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "health_status": "healthy", "snapshot_schema_version": 1,
                "runtime_generation": "fixture", "collected_at": "2026-10-27T00:00:00+00:00", "collected_monotonic": 1,
                "server": "fixture", "version": "0.8.0", "tool_profile": "full", "tool_count": len(after),
                "unique_tool_names": True}

    monkeypatch.setattr(registry, "collect_server_health", collected)
    server.runtime_started = True
    health = asyncio.run(server.call_tool("server_health", {}))
    assert health.structured_content["native_read_batch"] == {
        "active_batches": 0, "active_reads": 0, "reserved_response_bytes": 0,
    }


@pytest.mark.parametrize("profile", ["coding", "coding_core", "minimal", "browser"])
def test_batch_opt_in_rejects_unsupported_profiles_before_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str,
) -> None:
    from core import registry
    from core.errors import ToolError

    monkeypatch.setattr(registry, "detect_capabilities", lambda: pytest.fail("Discovered capabilities before configuration denial"))
    with pytest.raises(ToolError) as raised:
        _server(tmp_path, monkeypatch, profile, enabled=True)
    assert raised.value.code == "batch_profile_unsupported"


def test_eligibility_uses_live_native_descriptors_without_building_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp.server import MCPServer

    from core.errors import ToolError

    server = _server(tmp_path, monkeypatch)
    reader = server.native_read_batch_state
    monkeypatch.setattr(MCPServer, "list_tools", lambda *_: pytest.fail("Rebuilt the complete catalog"))
    asyncio.run(reader._eligible())
    tool = server._tool_manager.get_tool("read_file")
    monkeypatch.setattr(tool, "annotations", tool.annotations.model_copy(update={"read_only_hint": False}))
    with pytest.raises(ToolError) as raised:
        asyncio.run(reader._eligible())
    assert raised.value.code == "batch_hidden_tool"
    server._tool_manager.remove_tool("read_file")
    with pytest.raises(ToolError) as raised:
        asyncio.run(reader._eligible())
    assert raised.value.code == "batch_hidden_tool"


@pytest.mark.parametrize(("value", "expected"), [("1", "1"), ("TRUE", "1"), ("0", "0"), ("false", "0"), ("invalid", "0")])
def test_batch_operator_flag_accepts_only_explicit_true_values(value: str, expected: str) -> None:
    import subprocess

    result = subprocess.run(
        [sys.executable, "-c", "from core.config import SETTINGS; print(int(SETTINGS.native_read_batch_enabled))"],
        cwd=Path(__file__).resolve().parents[1], env={**os.environ, "MCP_NATIVE_READ_BATCH_ENABLED": value},
        capture_output=True, text=True, timeout=10, check=True,
    )
    assert result.stdout.strip() == expected


def test_batch_connection_is_closed_with_lifespan_and_server_can_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path, monkeypatch)
    (tmp_path / "file.txt").write_text("sample", encoding="utf-8")
    stores: list[Any] = []

    async def scenario() -> None:
        for _ in range(2):
            async with Client(server) as client:
                result = await client.call_tool("native_read_batch", {
                    "root": str(tmp_path), "items": [{"id": "file", "path": "file.txt"}],
                })
                assert result.structured_content and result.structured_content["ok"]
                stores.append(server.native_read_batch_state._claim_store)
            assert stores[-1]._closed and stores[-1]._shared_connection is None
            assert server.native_read_batch_state._claim_store is None
        assert stores[0] is not stores[1]

    asyncio.run(scenario())


def test_batch_shutdown_keeps_connection_until_cancelled_read_finishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.errors import ToolError
    from core.workflow_store import WorkflowStore
    from tools.filesystem import service

    server = _server(tmp_path, monkeypatch)
    reader = server.native_read_batch_state
    (tmp_path / "file.txt").write_text("sample", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    original = service.read_window

    def blocked(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "read_window", blocked)

    async def scenario() -> None:
        task = asyncio.create_task(reader.execute(str(tmp_path), [{"id": "file", "path": "file.txt"}]))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            store = reader._claim_store
            reader.close()
            assert not store._closed
            with pytest.raises(ToolError) as denied:
                await reader.execute(str(tmp_path), [{"id": "next", "path": "file.txt"}])
            assert denied.value.code == "batch_draining"
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and reader.snapshot()["active_reads"] == 1
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store._closed and reader._claim_store is None
        health = WorkflowStore(tmp_path / "state" / "workflows.sqlite3", recover=False).health_summary()
        assert health["active_resource_request_count"] == health["quarantined_resource_request_count"] == 0

    asyncio.run(scenario())


def test_reused_batch_store_observes_external_quarantine_before_reading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.resource_claims import path_claim
    from core.workflow_store import WorkflowStore
    from tools.filesystem import service

    server = _server(tmp_path, monkeypatch)
    reader = server.native_read_batch_state
    path = tmp_path / "file.txt"
    path.write_text("sample", encoding="utf-8")
    arguments = [{"id": "file", "path": "file.txt"}]
    assert asyncio.run(reader.execute(str(tmp_path), arguments))["ok"]
    other = WorkflowStore(tmp_path / "state" / "workflows.sqlite3", recover=False)
    assert other.reserve_resource_claims(
        request_id="blocker", owner_token="external-owner", category="test", claims=(path_claim(path, "write"),),
    )["admitted"]
    other.quarantine_resource_request("blocker", owner_token="external-owner", reason="fixture")
    original = service.read_window
    dispatched: list[bool] = []

    def observed(*args: Any, **kwargs: Any) -> Any:
        dispatched.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "read_window", observed)
    denied = asyncio.run(reader.execute(str(tmp_path), arguments))
    assert not denied["ok"] and denied["items"][0]["error"] == "batch_scope_denied"
    assert dispatched == []
    assert other.resource_request("blocker")["state"] == "quarantined"
    other.resolve_quarantined_resource_request(
        "blocker", owner_token="external-owner", resolution="released", evidence="fixture has no effects",
    )
    assert asyncio.run(reader.execute(str(tmp_path), arguments))["ok"]
    assert dispatched == [True]


def test_deferred_store_cleanup_cannot_close_restarted_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.filesystem import service

    reader = _server(tmp_path, monkeypatch).native_read_batch_state
    (tmp_path / "file.txt").write_text("sample", encoding="utf-8")
    entered, release_read = threading.Event(), threading.Event()
    original_read, original_thread = service.read_window, reader._thread

    def blocked(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release_read.wait(5)
        return original_read(*args, **kwargs)

    monkeypatch.setattr(service, "read_window", blocked)

    async def scenario() -> None:
        queued, release_close = asyncio.Event(), asyncio.Event()

        async def pause_close(call: Any, cancelled: threading.Event) -> Any:
            if reader._closed and reader.snapshot()["active_batches"] == 0:
                queued.set()
                await release_close.wait()
            return await original_thread(call, cancelled)

        monkeypatch.setattr(reader, "_thread", pause_close)
        arguments = [{"id": "file", "path": "file.txt"}]
        task = asyncio.create_task(reader.execute(str(tmp_path), arguments))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            reader.close()
            release_read.set()
            await asyncio.wait_for(queued.wait(), timeout=5)
            reader.start()
            assert (await reader.execute(str(tmp_path), arguments))["ok"]
            new_store = reader._claim_store
        finally:
            release_read.set()
            release_close.set()
        await task
        assert not reader._closed and reader._claim_store is new_store and not new_store._closed
        assert (await reader.execute(str(tmp_path), arguments))["ok"]

    asyncio.run(scenario())


def test_request_waiting_for_eligibility_cannot_cross_reader_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.errors import ToolError

    reader = _server(tmp_path, monkeypatch).native_read_batch_state
    (tmp_path / "file.txt").write_text("sample", encoding="utf-8")
    original = reader._eligible

    async def scenario() -> None:
        entered, resume = asyncio.Event(), asyncio.Event()

        async def paused() -> None:
            entered.set()
            await resume.wait()
            await original()

        monkeypatch.setattr(reader, "_eligible", paused)
        task = asyncio.create_task(reader.execute(str(tmp_path), [{"id": "file", "path": "file.txt"}]))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            reader.close()
            reader.start()
        finally:
            resume.set()
        with pytest.raises(ToolError) as denied:
            await task
        assert denied.value.code == "batch_draining"
        assert reader._claim_store is None
        assert reader.snapshot()["active_batches"] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["allowlist", "remove", "annotations"])
def test_read_waiting_for_capacity_rechecks_profile_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from tools.filesystem import service

    server = _server(tmp_path, monkeypatch)
    reader = server.native_read_batch_state
    (tmp_path / "file.txt").write_text("sample", encoding="utf-8")
    original_read = service.read_window
    dispatched: list[bool] = []

    def observed(*args: Any, **kwargs: Any) -> Any:
        dispatched.append(True)
        return original_read(*args, **kwargs)

    monkeypatch.setattr(service, "read_window", observed)

    async def scenario() -> None:
        checked = asyncio.Event()
        slots = reader._read_slots

        class ObservedSlots:
            def acquire(self, *args: Any, **kwargs: Any) -> bool:
                acquired = bool(slots.acquire(*args, **kwargs))
                if not acquired:
                    checked.set()
                return acquired

            def release(self) -> None:
                slots.release()

        monkeypatch.setattr(reader, "_read_slots", ObservedSlots())
        held = 0
        for _ in range(4):
            assert reader._read_slots.acquire(blocking=False)
            held += 1
        task = asyncio.create_task(reader.execute(str(tmp_path), [{"id": "file", "path": "file.txt"}]))
        try:
            await asyncio.wait_for(checked.wait(), timeout=5)
            if change == "allowlist":
                server.native_allowlist = frozenset(server.native_allowlist - {"read_file"})
            elif change == "remove":
                server._tool_manager.remove_tool("read_file")
            else:
                tool = server._tool_manager.get_tool("read_file")
                monkeypatch.setattr(tool, "annotations", tool.annotations.model_copy(update={"read_only_hint": False}))
            reader._read_slots.release()
            held -= 1
            result = await task
        finally:
            for _ in range(held):
                reader._read_slots.release()
        assert result["items"][0]["error"] == "batch_hidden_tool"
        assert dispatched == []

    asyncio.run(scenario())


def test_read_waiting_for_claim_rechecks_profile_before_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.resource_claims import path_claim
    from core.workflow_store import WorkflowStore
    from tools.filesystem import service

    server = _server(tmp_path, monkeypatch)
    reader = server.native_read_batch_state
    path = tmp_path / "file.txt"
    path.write_text("sample", encoding="utf-8")
    other = WorkflowStore(tmp_path / "state" / "workflows.sqlite3", recover=False)
    assert other.reserve_resource_claims(
        request_id="blocker", owner_token="external-owner", category="test", claims=(path_claim(path, "write"),),
    )["admitted"]
    store = reader._store()
    original_reserve, original_read = store.reserve_resource_claims, service.read_window
    waiting = threading.Event()
    dispatched: list[bool] = []

    def reserve(*args: Any, **kwargs: Any) -> Any:
        result = original_reserve(*args, **kwargs)
        if not result["admitted"]:
            waiting.set()
        return result

    def observed(*args: Any, **kwargs: Any) -> Any:
        dispatched.append(True)
        return original_read(*args, **kwargs)

    monkeypatch.setattr(store, "reserve_resource_claims", reserve)
    monkeypatch.setattr(service, "read_window", observed)

    async def scenario() -> None:
        task = asyncio.create_task(reader.execute(str(tmp_path), [{"id": "file", "path": "file.txt"}]))
        try:
            assert await asyncio.to_thread(waiting.wait, 5)
            server.native_allowlist = frozenset(server.native_allowlist - {"read_file"})
        finally:
            other.release_resource_request("blocker", owner_token="external-owner")
        result = await task
        assert result["items"][0]["error"] == "batch_hidden_tool"
        assert dispatched == []
        assert other.health_summary()["active_resource_request_count"] == 0

    asyncio.run(scenario())


def test_final_eligibility_timeout_cancels_check_and_releases_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.filesystem import service

    reader = _server(tmp_path, monkeypatch).native_read_batch_state
    (tmp_path / "file.txt").write_text("sample", encoding="utf-8")
    store = reader._store()
    store.health_summary()
    original = reader._eligible
    dispatched: list[bool] = []
    monkeypatch.setattr(service, "read_window", lambda *args, **kwargs: dispatched.append(True))

    async def scenario() -> None:
        finished = asyncio.Event()
        checks = 0

        async def eligible() -> None:
            nonlocal checks
            await original()
            checks += 1
            if checks == 2:
                try:
                    await asyncio.Event().wait()
                finally:
                    finished.set()

        monkeypatch.setattr(reader, "_eligible", eligible)
        result = await reader.execute(str(tmp_path), [{"id": "file", "path": "file.txt"}], timeout_sec=1.0)
        await asyncio.wait_for(finished.wait(), timeout=5)
        assert result["items"][0]["status"] == "deadline"
        assert dispatched == []
        assert reader.snapshot()["active_reads"] == 0
        assert store.health_summary()["active_resource_request_count"] == 0

    asyncio.run(scenario())


def test_stop_during_final_eligibility_prevents_file_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.filesystem import service

    reader = _server(tmp_path, monkeypatch).native_read_batch_state
    (tmp_path / "file.txt").write_text("sample", encoding="utf-8")
    store = reader._store()
    original = reader._eligible
    dispatched: list[bool] = []
    monkeypatch.setattr(service, "read_window", lambda *args, **kwargs: dispatched.append(True))

    async def scenario() -> None:
        validated, release = asyncio.Event(), asyncio.Event()
        checks = 0

        async def eligible() -> None:
            nonlocal checks
            await original()
            checks += 1
            if checks == 2:
                validated.set()
                await release.wait()

        monkeypatch.setattr(reader, "_eligible", eligible)
        task = asyncio.create_task(reader.execute(str(tmp_path), [{"id": "file", "path": "file.txt"}]))
        try:
            await asyncio.wait_for(validated.wait(), timeout=5)
            reader.stop()
        finally:
            release.set()
        result = await task
        assert result["items"][0]["error"] == "batch_draining"
        assert dispatched == []
        assert reader.snapshot()["active_reads"] == 0
        assert store._closed

    asyncio.run(scenario())


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


@pytest.mark.parametrize("parallel", [1, 4])
def test_full_profile_batch_matches_unicode_read_windows_and_drains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parallel: int,
) -> None:
    server = _server(tmp_path, monkeypatch, "full", enabled=True)
    expected = ["سلام 🌍\nخط دوم\n", "café\n日本語\n", "αβγ\n🧪\n", "نهاية\n"]
    for index, content in enumerate(expected):
        (tmp_path / f"unicode-{index}.txt").write_bytes(("skip\n" + content).encode("utf-8"))

    async def scenario() -> None:
        async with Client(server) as client:
            direct = [await client.call_tool("read_file", {
                "path": str(tmp_path / f"unicode-{i}.txt"), "encoding": "utf-8", "start_line": 2,
                "max_chars": 1024, "delivery": "inline",
            }) for i in range(4)]
            result = await client.call_tool("native_read_batch", {
                "root": str(tmp_path), "max_parallel": parallel, "items": [
                    {"id": str(i), "path": f"unicode-{i}.txt", "encoding": "utf-8", "start_line": 2, "max_chars": 1024}
                    for i in range(4)
                ],
            })
            wire = result.structured_content
            assert wire and wire["ok"] and wire["complete"]
            assert [item["id"] for item in wire["items"]] == [str(i) for i in range(4)]
            assert [item["content"] for item in wire["items"]] == expected
            assert [item.structured_content["content"] for item in direct] == expected
            assert len(result.model_dump_json().encode("utf-8")) <= 192 * 1024
            assert server.native_read_batch_state.snapshot() == {
                "active_batches": 0, "active_reads": 0, "reserved_response_bytes": 0,
            }
            store = server.native_read_batch_state._claim_store
            assert store is not None
        assert store._closed and store._shared_connection is None

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


@pytest.mark.skipif(os.name != "nt", reason="Windows delete-sharing contract")
def test_contained_read_pins_ancestors_and_file_until_read_finishes(tmp_path: Path) -> None:
    import time

    from tools.filesystem import service

    root = tmp_path / "root"
    child = root / "child"
    child.mkdir(parents=True)
    path = child / "data.txt"
    path.write_bytes(b"inside")
    info = root.stat()
    limits = service.ReadWindowLimits(root, (info.st_dev, info.st_ino), 6, time.monotonic() + 10, threading.Event())
    with service._open_contained(path, limits) as stream:
        for target in (root, child, path):
            with pytest.raises(PermissionError):
                target.rename(target.with_name(target.name + "-moved"))
        assert stream.read() == b"inside"
    for target in (path, child, root):
        moved = target.with_name(target.name + "-moved")
        target.rename(moved)
        moved.rename(target)


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
