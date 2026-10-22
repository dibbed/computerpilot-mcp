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
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from core.errors import ToolError
from core.process_ownership import OwnedProcess
from core.windows_job import NativeOwnershipRequiredError
from tools.language import transport
from tools.language.lsp import LspClient
from tools.language.providers import LspProvider

FIXTURE = Path(__file__).parent / "fixtures" / "persistent_lsp_server.py"


def manager_provider(
    root: Path, mode: str = "normal", *, delay: float | None = None, sync_kind: int = 1, open_close: bool = True,
) -> tuple[LspProvider, Path]:
    entrypoint = root / "managed-fixture.py"
    log = root / "managed-fixture.log"
    source = FIXTURE.read_text(encoding="utf-8")
    sync_anchor = '"textDocumentSync": {"openClose": True, "change": 1}'
    assert source.count(sync_anchor) == 1
    sync = {"openClose": open_close, "change": sync_kind, "save": {"includeText": True}}
    source = source.replace(sync_anchor, '"textDocumentSync": ' + repr(sync))
    notification_anchor = '        if "id" not in message:'
    assert source.count(notification_anchor) == 1
    source = source.replace(
        notification_anchor, notification_anchor + '\n            _append_log(log_path, "notify:" + json.dumps(message))',
    )
    change_anchor = '            elif method == "textDocument/didClose":'
    assert source.count(change_anchor) == 1
    source = source.replace(change_anchor, '''            elif method == "textDocument/didChange":
                params = _dict(message.get("params"))
                document = _dict(params.get("textDocument"))
                uri = document.get("uri")
                changes = params.get("contentChanges")
                version = document.get("version")
                if isinstance(uri, str) and uri in opened and isinstance(version, int):
                    if isinstance(changes, list) and changes and version > opened[uri][0]:
                        opened[uri] = (version, str(changes[-1].get("text", "")))
''' + change_anchor)
    anchor = '        result: dict[str, object] = {"method": method, "initialized": initialized}'
    assert source.count(anchor) == 1
    source = source.replace(
        anchor, anchor[:-1] + ', "pid": os.getpid()}',
    )
    slow_anchor = '        if mode == "slow":'
    assert source.count(slow_anchor) == 1
    source = source.replace(slow_anchor, '        _append_log(log_path, "dispatch:" + str(params.get("query", "")))\n' + slow_anchor)
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


def manager_notifications(log: Path) -> list[dict[str, Any]]:
    return [json.loads(line[7:]) for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("notify:")]


def hover_params(path: Path) -> dict[str, object]:
    return {"textDocument": {"uri": path.as_uri()}, "position": {"line": 0, "character": 0}}


@pytest.fixture
def lsp_runtime_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from core import audit, config, heartbeat, registry, resource_health
    from tools.language import registry as language_registry

    settings = replace(config.SETTINGS, state_dir=tmp_path / "state", memory_dir=tmp_path / "memory", persistent_enabled=True,
                       providers_config=None, extensions_enabled=False, tool_profile="testing")
    for module in (audit, config, heartbeat, registry, resource_health, language_registry):
        monkeypatch.setattr(module, "SETTINGS", settings, raising=False)
    for name in ("MCP_HEARTBEAT_FILE", "MCP_RUNTIME_HEALTH_FILE", "MCP_LIFECYCLE_CONTROL_FILE",
                 "MCP_LIFECYCLE_STATUS_FILE", "MCP_TOOL_ACTIVITY_FILE", "MCP_RUNTIME_GENERATION_ID"):
        monkeypatch.delenv(name, raising=False)
    for name in ("schedule_artifact_retention", "schedule_job_history_retention", "schedule_workflow_history_retention"):
        monkeypatch.setattr(heartbeat, name, lambda **_kwargs: None)
    monkeypatch.setattr(heartbeat.BACKUP_RETENTION, "schedule", lambda: None)
    monkeypatch.setattr(heartbeat.SEARCH_SNAPSHOTS, "cleanup", lambda: None)
    return settings


def test_persistent_registration_requires_explicit_server_manager(lsp_runtime_settings: Any) -> None:
    from mcp.server import MCPServer

    from tools.language import registry

    with pytest.raises(ToolError) as failure:
        registry.register(MCPServer("missing-manager"))
    assert failure.value.code == "lsp_configuration_invalid"


def test_core_creates_fresh_inert_lsp_managers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lsp_runtime_settings: Any) -> None:
    from core import registry
    from core.health_snapshot import collect_server_health

    monkeypatch.setattr(transport, "spawn_owned_process", lambda *_args, **_kwargs: pytest.fail("LSP started before request"))
    provider, _log = manager_provider(tmp_path)
    config_path = tmp_path / "providers.json"
    config_path.write_text(json.dumps({"schema_version": 1, "providers": [{
        "provider_id": provider.provider_id, "languages": list(provider.languages), "executable": str(provider.executable),
        "executable_sha256": provider.executable_sha256, "entrypoint": str(provider.entrypoint),
        "entrypoint_sha256": provider.entrypoint_sha256, "declared_version": provider.declared_version,
        "trusted_roots": [str(tmp_path)],
    }]}), encoding="utf-8")
    monkeypatch.setattr(registry, "SETTINGS", replace(lsp_runtime_settings, providers_config=str(config_path)))
    servers = [registry.create_server(), registry.create_server()]
    managers: list[Any] = [getattr(server, "lsp_manager", None) for server in servers]
    assert all(manager is not None for manager in managers)
    assert managers[0] is not managers[1]
    assert managers[0]._generation != managers[1]._generation
    for server, manager in zip(servers, managers, strict=True):
        asyncio.run(server.list_tools())
        health = asyncio.run(collect_server_health(server, settings=lsp_runtime_settings))
        assert health["lsp"]["spawn_count"] == health["lsp"]["active"] == 0
        assert manager.snapshot()["spawn_count"] == manager.snapshot()["active"] == 0
    assert not _log.exists()


def test_mcp_language_requests_share_server_owned_manager(tmp_path: Path, lsp_runtime_settings: Any) -> None:
    from mcp import Client
    from mcp.server import MCPServer

    from core.heartbeat import lifespan
    from tools.language.registry import register
    from tools.language.sessions import LspSessionManager

    provider, _log = manager_provider(tmp_path)
    manager = LspSessionManager((provider,), generation="mcp-test")
    server: Any = MCPServer("persistent-language", lifespan=lifespan)
    server.lsp_manager = manager
    register(server, manager=manager)

    async def scenario() -> None:
        try:
            results = []
            async with Client(server, raise_exceptions=True) as client:
                for query in ("first", "second"):
                    reply = await client.call_tool("workspace_symbols", {"root": str(tmp_path), "params": {"query": query}})
                    result = reply.structured_content
                    assert isinstance(result, dict) and result["ok"] is True
                    results.append(result["result"])
                assert len(manager_pids(results)) == 1
                assert manager.snapshot()["spawn_count"] == 1
            assert manager.snapshot()["active"] == 0
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())


def test_mcp_client_cancellation_reaches_lsp_owner(tmp_path: Path, lsp_runtime_settings: Any) -> None:
    from mcp import Client
    from mcp.server import MCPServer

    from core.heartbeat import lifespan
    from tools.language.registry import register
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path, "slow", delay=2)
    manager = LspSessionManager((provider,), generation="cancel-client")
    server: Any = MCPServer("cancel-language", lifespan=lifespan)
    server.lsp_manager = manager
    register(server, manager=manager)

    async def scenario() -> None:
        try:
            async with Client(server, raise_exceptions=True) as client:
                request = asyncio.create_task(client.call_tool("workspace_symbols", {"root": str(tmp_path), "params": {"query": "cancel"}}))
                await manager_until(lambda: log.exists() and "dispatch:cancel" in log.read_text(encoding="utf-8").splitlines())
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
                await manager_until(lambda: manager.snapshot()["active"] == 0)
                assert manager.snapshot()["spawn_count"] == 1
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())
    assert log.read_text(encoding="utf-8").splitlines().count("dispatch:cancel") == 1


def test_persistent_off_and_injected_manager_preserve_language_schemas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lsp_runtime_settings: Any,
) -> None:
    from mcp.server import MCPServer

    from tools.language import registry
    from tools.language.sessions import LspSessionManager

    provider, _log = manager_provider(tmp_path)
    monkeypatch.setattr(registry, "SETTINGS", replace(lsp_runtime_settings, persistent_enabled=False))
    legacy = MCPServer("legacy")
    registry.register(legacy)
    monkeypatch.setattr(registry, "SETTINGS", lsp_runtime_settings)
    persistent = MCPServer("persistent")
    registry.register(persistent, manager=LspSessionManager((provider,), generation="schema"))
    before = {tool.name: (tool.input_schema, tool.output_schema, tool.annotations) for tool in asyncio.run(legacy.list_tools())}
    after = {tool.name: (tool.input_schema, tool.output_schema, tool.annotations) for tool in asyncio.run(persistent.list_tools())}
    assert before == after
    assert len(before) == 9
    assert not _log.exists()


def test_legacy_lsp_request_keeps_event_loop_responsive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.language import registry

    entered, release = threading.Event(), threading.Event()

    def request(*_args: Any) -> dict[str, object]:
        entered.set()
        assert release.wait(5)
        return {"legacy": True}

    monkeypatch.setattr(registry.LspClient, "request", request)

    async def scenario() -> None:
        pending = asyncio.create_task(registry._request(str(tmp_path), 5, "workspace/symbol", {"query": "legacy"}))
        try:
            await manager_until(entered.is_set)
            assert not pending.done()
        finally:
            release.set()
            assert await pending == {"legacy": True}

    asyncio.run(scenario())


def test_legacy_lsp_cancellation_scope_waits_for_worker_completion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import anyio

    from tools.language import registry

    finished = threading.Event()

    def request(*_args: Any) -> dict[str, object]:
        time.sleep(0.15)
        finished.set()
        return {"legacy": True}

    monkeypatch.setattr(registry.LspClient, "request", request)

    async def scenario() -> None:
        with anyio.move_on_after(0.03) as scope:
            await registry._request(str(tmp_path), 5, "workspace/symbol", {"query": "legacy"})
        assert scope.cancel_called and finished.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize("persistent", [False, True])
def test_lsp_validation_does_not_block_event_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, persistent: bool) -> None:
    from tools.language import registry, sessions

    provider, _log = manager_provider(tmp_path)
    manager = sessions.LspSessionManager((provider,), generation="validation") if persistent else None
    entered, release, starved = threading.Event(), threading.Event(), threading.Event()

    def validation(*_args: Any) -> Any:
        entered.set()
        if not release.wait(2):
            starved.set()
        raise ToolError("lsp_params_invalid", "fixture validation denial")

    monkeypatch.setattr(registry, "validate_request", validation)
    monkeypatch.setattr(sessions, "validate_request", validation)

    async def scenario() -> None:
        task = asyncio.create_task(registry._request(str(tmp_path), 5, "workspace/symbol", {"query": "validate"}, manager=manager))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            release.set()
            with pytest.raises(ToolError):
                await task
            assert not starved.is_set()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_count", [0, 2])
def test_rename_worker_preserves_mutation_scope_until_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lsp_runtime_settings: Any, cancel_count: int,
) -> None:
    from mcp.server import MCPServer

    from core import heartbeat, recovery
    from tools.language import registry
    from tools.language.sessions import LspSessionManager, RenameResult

    provider, _log = manager_provider(tmp_path)
    manager = LspSessionManager((provider,), generation="rename-worker")
    server: Any = MCPServer("rename-worker", lifespan=heartbeat.lifespan)
    server.lsp_manager = manager
    registry.register(server, manager=manager)
    entered, release, starved = threading.Event(), threading.Event(), threading.Event()
    applied: list[bool] = []

    async def response(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        return RenameResult({"changes": {}}, {})

    def prepare(*_args: Any, **_kwargs: Any) -> list[object]:
        scope = recovery._CURRENT_OPERATION_SCOPE.get()
        assert scope is not None and scope.active
        entered.set()
        if not release.wait(2):
            starved.set()
        assert scope.active
        return []

    def apply(_plan: object) -> dict[str, object]:
        applied.append(True)
        return {"ok": True, "applied": True}

    monkeypatch.setattr(registry, "_request", response)
    monkeypatch.setattr(registry, "prepare_workspace_edit", prepare)
    monkeypatch.setattr(registry, "apply_workspace_edit", apply)

    async def scenario() -> None:
        async with heartbeat.lifespan(server):
            heartbeat.RUNTIME_LIFECYCLE.configure(tmp_path / "control", tmp_path / "status")
            task = asyncio.create_task(server.call_tool("rename_symbol", {
                "root": str(tmp_path), "document_uri": (tmp_path / "source.py").as_uri(),
                "line": 0, "character": 0, "new_name": "renamed", "expected_sha256": {},
            }))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                assert heartbeat.RUNTIME_LIFECYCLE.snapshot()["active_mutations"] == 1
                for _ in range(cancel_count):
                    task.cancel()
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
                assert not task.done()
                assert heartbeat.RUNTIME_LIFECYCLE.snapshot()["active_mutations"] == 1
                release.set()
                if cancel_count:
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    await task
                assert applied == [True] and not starved.is_set()
                assert heartbeat.RUNTIME_LIFECYCLE.snapshot()["active_mutations"] == 0
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_manager_preparation_is_bounded_and_cancelled_callers_cannot_release_it_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.language import sessions

    provider, _log = manager_provider(tmp_path)
    manager = sessions.LspSessionManager((provider,), generation="bounded-preparation")
    entered, release = threading.Event(), threading.Event()

    def validation(*_args: Any) -> Any:
        entered.set()
        assert release.wait(5)
        raise ToolError("lsp_params_invalid", "fixture")

    monkeypatch.setattr(sessions, "validate_request", validation)

    async def scenario() -> None:
        tasks = [asyncio.create_task(manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "hold"}, timeout_sec=5))
                 for _ in range(32)]
        try:
            await manager_until(lambda: manager.snapshot()["preparing"] == 32 and entered.is_set())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            assert manager.snapshot()["preparing"] == 32
            with pytest.raises(ToolError) as failure:
                await manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "denied"}, timeout_sec=5)
            assert failure.value.code == "lsp_capacity_exceeded"
            assert manager.snapshot()["spawn_count"] == 0
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await manager_until(lambda: manager.snapshot()["preparing"] == 0)
            await asyncio.to_thread(manager.close_all)

    asyncio.run(scenario())


def test_manager_preparation_deadline_denies_late_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.language import sessions

    provider, _log = manager_provider(tmp_path)
    manager = sessions.LspSessionManager((provider,), generation="preparation-deadline")
    release = threading.Event()

    def validation(*_args: Any) -> Any:
        assert release.wait(5)
        raise ToolError("lsp_params_invalid", "fixture")

    monkeypatch.setattr(sessions, "validate_request", validation)

    async def scenario() -> None:
        try:
            with pytest.raises(asyncio.TimeoutError):
                await manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "expires"}, timeout_sec=0.03)
            assert manager.snapshot()["preparing"] == 1
        finally:
            release.set()
            await manager_until(lambda: manager.snapshot()["preparing"] == 0)
            await asyncio.to_thread(manager.close_all)
        assert manager.snapshot()["spawn_count"] == 0

    asyncio.run(scenario())


def test_manager_close_all_stops_admission_and_drains_workspaces(tmp_path: Path) -> None:
    from tools.language.sessions import LspSessionManager

    provider, _log = manager_provider(tmp_path)
    manager = LspSessionManager((provider,), generation="close-test")
    roots = [tmp_path / "one", tmp_path / "two"]
    for root in roots:
        root.mkdir()

    async def scenario() -> None:
        try:
            for root in roots:
                await manager.arequest(root, "python", "workspace/symbol", {"query": "start"}, timeout_sec=5)
            await asyncio.to_thread(manager.close_all, timeout_sec=5)
            assert manager.snapshot()["active"] == 0
            with pytest.raises(ToolError):
                await manager.arequest(roots[0], "python", "workspace/symbol", {"query": "denied"}, timeout_sec=5)
        finally:
            for root in roots:
                await asyncio.to_thread(manager.close_workspace, root)

    asyncio.run(scenario())


@pytest.mark.parametrize("sync_kind", [0, 1, 2])
def test_manager_document_sync_versions_and_utf16_range(tmp_path: Path, sync_kind: int) -> None:
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path, sync_kind=sync_kind)
    path = tmp_path / "source.py"
    path.write_bytes("😀\r\nold😀".encode())
    manager = LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        try:
            first = await manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(path), timeout_sec=5)
            second = await manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(path), timeout_sec=5)
            assert isinstance(first, dict) and isinstance(second, dict)
            assert first["version"] == second["version"] == 1
            path.write_bytes("new😀\r\n".encode())
            changed = await manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(path), timeout_sec=5)
            assert isinstance(changed, dict) and changed["version"] == 2
            assert manager.snapshot()["documents"] == 1
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)
        assert manager.snapshot()["documents"] == 0

    asyncio.run(scenario())
    notifications = manager_notifications(log)
    opened = [item for item in notifications if item["method"] == "textDocument/didOpen"]
    changes = [item for item in notifications if item["method"] == "textDocument/didChange"]
    assert len(opened) == (2 if sync_kind == 0 else 1)
    assert len(changes) == (0 if sync_kind == 0 else 1)
    if sync_kind == 2:
        assert changes[0]["params"]["contentChanges"][0]["range"] == {
            "start": {"line": 0, "character": 0}, "end": {"line": 1, "character": 5},
        }
    saves = [item for item in notifications if item["method"] == "textDocument/didSave"]
    assert len(saves) == 1
    assert saves[0]["params"]["text"] == "new😀\r\n"


def test_manager_disk_change_during_hover_is_stale(tmp_path: Path) -> None:
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path, "slow", delay=0.3)
    path = tmp_path / "source.py"
    path.write_text("before", encoding="utf-8")
    manager = LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        try:
            await manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(path), timeout_sec=5)
            request = asyncio.create_task(manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(path), timeout_sec=5))
            await manager_until(lambda: log.read_text(encoding="utf-8").splitlines().count("dispatch:") == 2)
            original = path.stat()
            path.write_text("after!", encoding="utf-8")
            os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
            with pytest.raises(ToolError) as failure:
                await request
            assert failure.value.code == "lsp_result_stale"
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())


def test_manager_queued_position_is_checked_against_opened_snapshot(tmp_path: Path) -> None:
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path, "slow", delay=0.3)
    path = tmp_path / "source.py"
    path.write_text("first\nsecond", encoding="utf-8")
    manager = LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        active = asyncio.create_task(manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "block"}, timeout_sec=5))
        try:
            await manager_until(lambda: log.exists() and "dispatch:block" in log.read_text(encoding="utf-8").splitlines())
            params = {"textDocument": {"uri": path.as_uri()}, "position": {"line": 1, "character": 1}}
            queued = asyncio.create_task(manager.arequest(tmp_path, "python", "textDocument/hover", params, timeout_sec=5))
            await manager_until(lambda: manager.snapshot()["queue_depth"] == 1)
            path.write_text("short", encoding="utf-8")
            await active
            with pytest.raises(ToolError) as failure:
                await queued
            assert failure.value.code == "lsp_position_invalid"
            assert "dispatch:" not in log.read_text(encoding="utf-8").splitlines()
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())


@pytest.mark.parametrize("when", ["before", "during"])
def test_manager_workspace_query_uses_current_open_documents(tmp_path: Path, when: str) -> None:
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path, "slow", delay=0.2)
    path = tmp_path / "source.py"
    path.write_text("old", encoding="utf-8")
    manager = LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        try:
            await manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(path), timeout_sec=5)
            if when == "before":
                path.write_text("new", encoding="utf-8")
            query = asyncio.create_task(manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "workspace"}, timeout_sec=5))
            await manager_until(lambda: "dispatch:workspace" in log.read_text(encoding="utf-8").splitlines())
            if when == "during":
                path.write_text("new", encoding="utf-8")
                with pytest.raises(ToolError) as failure:
                    await query
                assert failure.value.code == "lsp_result_stale"
            else:
                await query
                changes = [item for item in manager_notifications(log) if item["method"] == "textDocument/didChange"]
                assert len(changes) == 1
                assert changes[0]["params"]["contentChanges"][0]["text"] == "new"
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())


def test_manager_document_lru_evicts_with_did_close(tmp_path: Path) -> None:
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path)
    manager = LspSessionManager((provider,), generation="test")
    paths = [tmp_path / f"source-{index}.py" for index in range(17)]
    for path in paths:
        path.write_text("x", encoding="utf-8")

    async def scenario() -> None:
        try:
            for path in paths:
                await manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(path), timeout_sec=5)
            assert manager.snapshot()["documents"] == 16
            await manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(paths[0]), timeout_sec=5)
            assert manager.snapshot()["documents"] == 16
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())
    notifications = manager_notifications(log)
    first_close = next(item for item in notifications if item["method"] == "textDocument/didClose")
    assert first_close["params"]["textDocument"]["uri"] == paths[0].as_uri()
    assert sum(item["method"] == "textDocument/didOpen" for item in notifications) == 18


@pytest.mark.parametrize("suffix,language", [(".tsx", "typescriptreact"), (".jsx", "javascriptreact"), (".py", "python")])
def test_manager_document_language_and_unicode_identity(tmp_path: Path, suffix: str, language: str) -> None:
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path)
    path = tmp_path / ("نمونه" + suffix)
    path.write_text("value = 1", encoding="utf-8")
    manager = LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        try:
            await manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(path), timeout_sec=5)
            alias = path.with_name(path.name.upper()) if os.name == "nt" else path
            await manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(alias), timeout_sec=5)
            assert manager.snapshot()["documents"] == 1
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())
    opens = [item for item in manager_notifications(log) if item["method"] == "textDocument/didOpen"]
    assert len(opens) == 1
    assert opens[0]["params"]["textDocument"]["languageId"] == language


def test_manager_missing_sync_capability_denies_before_document_send(tmp_path: Path) -> None:
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path, open_close=False)
    path = tmp_path / "source.py"
    path.write_text("x", encoding="utf-8")
    manager = LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        try:
            with pytest.raises(ToolError) as failure:
                await manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(path), timeout_sec=5)
            assert failure.value.code == "lsp_sync_unsupported"
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())
    assert all(item["method"] != "textDocument/didOpen" for item in manager_notifications(log))


def test_manager_deleted_document_closes_and_recreated_document_reopens(tmp_path: Path) -> None:
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path)
    path = tmp_path / "source.py"
    path.write_text("old", encoding="utf-8")
    manager = LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        try:
            await manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(path), timeout_sec=5)
            path.unlink()
            with pytest.raises(ToolError):
                await manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(path), timeout_sec=5)
            await manager_until(lambda: manager.snapshot()["documents"] == 0)
            path.write_text("new", encoding="utf-8")
            result = await manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(path), timeout_sec=5)
            assert isinstance(result, dict) and result["version"] == 1
            assert manager.snapshot()["spawn_count"] == 1
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())
    methods = [item["method"] for item in manager_notifications(log)]
    assert methods.count("textDocument/didOpen") == methods.count("textDocument/didClose") == 2


@pytest.mark.parametrize("change", ["marker", "config", "pin"])
def test_manager_identity_changes_retire_old_session(tmp_path: Path, change: str) -> None:
    from tools.language.sessions import LspSessionManager

    provider, _log = manager_provider(tmp_path)
    manager = LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        try:
            first = await manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "first"}, timeout_sec=5)
            assert isinstance(first, dict)
            if change == "pin":
                assert provider.entrypoint is not None
                provider.entrypoint.write_text(provider.entrypoint.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
                with pytest.raises(ToolError) as failure:
                    await manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "denied"}, timeout_sec=5)
                assert failure.value.code == "lsp_provider_changed"
                await manager_until(lambda: manager.snapshot()["active"] == 0)
            else:
                if change == "marker":
                    (tmp_path / ".git").write_text("gitdir: fixture-other-worktree\n", encoding="utf-8")
                else:
                    manager._providers = (replace(provider, declared_version="changed"),)
                second = await manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "second"}, timeout_sec=5)
                assert isinstance(second, dict) and first["pid"] != second["pid"]
                assert manager.snapshot()["active"] == 1
            assert not _process_alive(first["pid"])
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())


def test_manager_concurrent_retirement_submits_only_one_drain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.language import sessions
    from tools.language.providers import make_session_key

    provider, _log = manager_provider(tmp_path)
    manager = sessions.LspSessionManager((provider,), generation="test")
    key = make_session_key(tmp_path, "python", provider, "test")
    session = sessions._Session(key, provider, state="READY")
    manager._sessions[key] = session

    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        submissions = 0

        async def blocked_drain(*_args: object) -> None:
            nonlocal submissions
            submissions += 1
            entered.set()
            await release.wait()
            session.state = "CLOSED"

        monkeypatch.setattr(sessions.asyncio, "to_thread", blocked_drain)
        first = asyncio.create_task(manager._retire_obsolete(tmp_path, "python"))
        await entered.wait()
        other = asyncio.create_task(manager._retire_obsolete(tmp_path, "python"))
        try:
            await asyncio.sleep(0)
            assert submissions == 1
            with pytest.raises(ToolError) as failure:
                await other
            assert failure.value.code == "lsp_session_failed"
        finally:
            release.set()
            await asyncio.gather(first, other, return_exceptions=True)

    asyncio.run(scenario())


def test_manager_rename_never_evicts_its_active_input_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.language import sessions

    provider, log = manager_provider(tmp_path)
    monkeypatch.setattr(sessions, "_MAX_DOCUMENT_TOTAL_BYTES", 10)
    manager = sessions.LspSessionManager((provider,), generation="test")
    paths = [tmp_path / "a.py", tmp_path / "b.py"]
    for path in paths:
        path.write_text("1234", encoding="utf-8")

    async def scenario() -> None:
        try:
            for path in paths:
                await manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(path), timeout_sec=5)
            paths[1].write_text("12345678", encoding="utf-8")
            with pytest.raises(ToolError) as failure:
                await manager.arequest(
                    tmp_path, "python", "textDocument/rename", {**hover_params(paths[0]), "newName": "renamed"}, timeout_sec=5,
                )
            assert failure.value.code == "lsp_document_capacity"
            assert paths[0].as_uri() in next(iter(manager._sessions.values())).documents
            assert log.read_text(encoding="utf-8").splitlines().count("dispatch:") == 2
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())


def test_manager_document_byte_capacity_evicts_without_exceeding_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.language import sessions

    provider, log = manager_provider(tmp_path)
    monkeypatch.setattr(sessions, "_MAX_DOCUMENT_TOTAL_BYTES", 10)
    manager = sessions.LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        try:
            for name in ("a.py", "b.py"):
                path = tmp_path / name
                path.write_text("123456", encoding="utf-8")
                await manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(path), timeout_sec=5)
                assert manager.snapshot()["document_bytes"] == 6
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())
    closes = [item for item in manager_notifications(log) if item["method"] == "textDocument/didClose"]
    assert closes[0]["params"]["textDocument"]["uri"] == (tmp_path / "a.py").as_uri()


def test_manager_rename_carries_private_immutable_snapshot(tmp_path: Path) -> None:
    from tools.language.sessions import LspSessionManager, RenameResult

    provider, _log = manager_provider(tmp_path)
    path = tmp_path / "source.py"
    path.write_bytes(b"before\r\n")
    manager = LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        try:
            result = await manager.arequest(
                tmp_path, "python", "textDocument/rename", {**hover_params(path), "newName": "after"}, timeout_sec=5,
            )
            assert isinstance(result, RenameResult)
            assert result.snapshot_version == {path.as_uri(): 1}
            assert result.source_sha256 == {path.as_uri(): hashlib.sha256(path.read_bytes()).hexdigest()}
            assert "snapshot_version" not in json.loads(json.dumps(result))
            with pytest.raises(TypeError):
                cast(dict[str, int], result.snapshot_version)[path.as_uri()] = 2
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())


def test_manager_reply_keeps_owner_evidence_immutable_and_raw_results_compatible(tmp_path: Path) -> None:
    from tools.language.sessions import LspSessionManager, RenameResult, SessionReply

    provider, _log = manager_provider(tmp_path)
    assert provider.entrypoint is not None
    source = provider.entrypoint.read_text(encoding="utf-8")
    source = source.replace(
        '"hoverProvider": True,',
        '"hoverProvider": True, "workspaceSymbolProvider": {}, '
        '"documentSymbolProvider": {"private": "capability-prose-canary"}, "typeHierarchyProvider": False, '
        '"unrecognizedProvider": {"private": "unrecognized-prose-canary"},',
    )
    provider.entrypoint.write_text(source, encoding="utf-8")
    provider = replace(provider, entrypoint_sha256=hashlib.sha256(provider.entrypoint.read_bytes()).hexdigest())
    path = tmp_path / "source.py"
    path.write_bytes(b"before\n")
    before_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manager = LspSessionManager((provider,), generation="reply-evidence")

    async def scenario() -> None:
        try:
            reply = await manager.arequest_with_evidence(
                tmp_path, "python", "textDocument/hover", hover_params(path), timeout_sec=5,
            )
            assert isinstance(reply, SessionReply)
            assert reply.key.provider_id == "pyright"
            assert reply.key.runtime_generation == "reply-evidence"
            assert reply.capabilities == frozenset({
                "workspaceSymbolProvider", "documentSymbolProvider", "definitionProvider",
                "referencesProvider", "callHierarchyProvider",
            })
            assert "canary" not in repr(reply.capabilities)
            assert reply.documents == ((path.as_uri(), 1, before_digest),)
            with pytest.raises(FrozenInstanceError):
                reply.incarnation = "changed"  # type: ignore[misc]
            with pytest.raises(AttributeError):
                cast(Any, reply.capabilities).add("anotherProvider")
            raw = await manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(path), timeout_sec=5)
            assert isinstance(raw, dict) and not isinstance(raw, SessionReply)
            assert set(raw) == {"method", "initialized", "pid", "uri", "version"}
            path.write_bytes(b"after\n")
            renamed = await manager.arequest_with_evidence(
                tmp_path, "python", "textDocument/rename", {**hover_params(path), "newName": "updated"}, timeout_sec=5,
            )
            assert isinstance(renamed.value, RenameResult)
            assert renamed.value.snapshot_version == {path.as_uri(): 2}
            assert renamed.documents == ((path.as_uri(), 2, hashlib.sha256(path.read_bytes()).hexdigest()),)
            assert renamed.key == reply.key and renamed.incarnation == reply.incarnation
            assert reply.documents == ((path.as_uri(), 1, before_digest),)
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())


@pytest.mark.parametrize("capability", [None, False, 0, 1, "true", [], True, {}])
def test_manager_reply_projects_only_declared_capability_shapes(tmp_path: Path, capability: object) -> None:
    from tools.language.sessions import LspSessionManager

    provider, _log = manager_provider(tmp_path)
    assert provider.entrypoint is not None
    source = provider.entrypoint.read_text(encoding="utf-8").replace(
        '"definitionProvider": True,', f'"definitionProvider": {capability!r},',
    )
    provider.entrypoint.write_text(source, encoding="utf-8")
    provider = replace(provider, entrypoint_sha256=hashlib.sha256(provider.entrypoint.read_bytes()).hexdigest())
    manager = LspSessionManager((provider,), generation="capability-shapes")

    async def scenario() -> None:
        try:
            reply = await manager.arequest_with_evidence(tmp_path, "python", "workspace/symbol", {"query": ""}, timeout_sec=5)
            assert ("definitionProvider" in reply.capabilities) is (capability is True or isinstance(capability, dict))
            assert len(reply.capabilities) <= 6
            assert reply.documents == ()
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())


def test_manager_same_key_restart_gets_a_new_incarnation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.language import sessions

    provider, _log = manager_provider(tmp_path)
    real_clock = time.monotonic
    advance = [0.0]
    monkeypatch.setattr(sessions, "time", SimpleNamespace(monotonic=lambda: real_clock() + advance[0]))
    manager = sessions.LspSessionManager((provider,), generation="stable-generation")

    async def scenario() -> None:
        try:
            first = await manager.arequest_with_evidence(tmp_path, "python", "workspace/symbol", {"query": "first"}, timeout_sec=5)
            await asyncio.to_thread(manager.close_workspace, tmp_path)
            advance[0] = 6.0
            second = await manager.arequest_with_evidence(tmp_path, "python", "workspace/symbol", {"query": "second"}, timeout_sec=5)
            assert first.key == second.key
            assert first.incarnation != second.incarnation
            assert len(first.incarnation) == len(second.incarnation) == 32
            assert manager.snapshot()["spawn_count"] == 2
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["callHierarchy/incomingCalls", "callHierarchy/outgoingCalls",
                                   "typeHierarchy/supertypes", "typeHierarchy/subtypes"])
def test_manager_hierarchy_item_is_synchronized_and_included_in_reply(tmp_path: Path, method: str) -> None:
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path)
    path = tmp_path / "hierarchy.py"
    path.write_text("example = 1\n", encoding="utf-8")
    position = {"line": 0, "character": 0}
    span = {"start": position, "end": position}
    params: dict[str, object] = {"item": {
        "name": "example", "kind": 12, "uri": path.as_uri(), "range": span, "selectionRange": span,
    }}
    manager = LspSessionManager((provider,), generation="item-sync")

    async def scenario() -> None:
        try:
            reply = await manager.arequest_with_evidence(tmp_path, "python", method, params, timeout_sec=5)
            assert reply.documents == ((path.as_uri(), 1, hashlib.sha256(path.read_bytes()).hexdigest()),)
            opened = [item for item in manager_notifications(log) if item["method"] == "textDocument/didOpen"]
            assert [item["params"]["textDocument"]["uri"] for item in opened] == [path.as_uri()]
            assert log.read_text(encoding="utf-8").splitlines().count("dispatch:") == 1
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())


def test_manager_evidence_request_cancellation_never_publishes_or_replays(tmp_path: Path) -> None:
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path, "slow", delay=2)
    manager = LspSessionManager((provider,), generation="cancel-evidence")

    async def scenario() -> None:
        task = asyncio.create_task(manager.arequest_with_evidence(
            tmp_path, "python", "workspace/symbol", {"query": "once"}, timeout_sec=5,
        ))
        try:
            await manager_until(lambda: log.exists() and "dispatch:once" in log.read_text(encoding="utf-8").splitlines())
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await manager_until(lambda: manager.snapshot()["active"] == 0)
            assert task.cancelled()
            assert manager.snapshot()["spawn_count"] == 1
            assert log.read_text(encoding="utf-8").splitlines().count("dispatch:once") == 1
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())


def test_manager_hierarchy_item_scope_denial_never_starts_a_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.language.sessions import LspSessionManager

    provider, _log = manager_provider(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("pass", encoding="utf-8")
    position = {"line": 0, "character": 0}
    span = {"start": position, "end": position}
    manager = LspSessionManager((provider,), generation="item-denial")
    monkeypatch.setattr(transport, "spawn_owned_process", lambda *_args, **_kwargs: pytest.fail("Denied item started a provider"))

    async def scenario() -> None:
        try:
            with pytest.raises(ToolError) as denied:
                await manager.arequest_with_evidence(workspace, "python", "callHierarchy/incomingCalls", {"item": {
                    "name": "example", "kind": 12, "uri": outside.as_uri(), "range": span, "selectionRange": span,
                }}, timeout_sec=5)
            assert denied.value.code == "lsp_path_outside_workspace"
            assert manager.snapshot()["spawn_count"] == 0
        finally:
            await asyncio.to_thread(manager.close_workspace, workspace)

    asyncio.run(scenario())


def test_manager_rechecks_hierarchy_ranges_after_document_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.language import sessions

    provider, log = manager_provider(tmp_path)
    path = tmp_path / "hierarchy.py"
    path.write_text("first\nsecond\n", encoding="utf-8")
    position = {"line": 1, "character": 0}
    span = {"start": position, "end": position}
    original = sessions.validate_request

    def change_after_validation(root: Path, method: str, params: Any) -> dict[str, object]:
        result = original(root, method, params)
        path.write_text("short", encoding="utf-8")
        return result

    monkeypatch.setattr(sessions, "validate_request", change_after_validation)
    manager = sessions.LspSessionManager((provider,), generation="item-stale-range")

    async def scenario() -> None:
        try:
            with pytest.raises(ToolError) as stale:
                await manager.arequest_with_evidence(tmp_path, "python", "callHierarchy/incomingCalls", {"item": {
                    "name": "example", "kind": 12, "uri": path.as_uri(), "range": span, "selectionRange": span,
                }}, timeout_sec=5)
            assert stale.value.code == "lsp_position_invalid"
            assert "dispatch:" not in log.read_text(encoding="utf-8").splitlines()
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())


def test_disk_snapshot_retries_pointer_replacement_with_equal_stat_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.language import sessions

    path = tmp_path / "source.py"
    replacement = tmp_path / "replacement.py"
    path.write_bytes(b"old")
    previous = path.stat()
    replacement.write_bytes(b"new")
    os.utime(replacement, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    original_stat = Path.stat
    original_fstat = os.fstat
    replaced = False
    reads: list[int] = []

    def replace_before_open(target: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        nonlocal replaced
        result = original_stat(target, follow_symlinks=follow_symlinks)
        if target == path and not replaced:
            replaced = True
            replacement.replace(path)
        return result

    def count_fstat(fd: int) -> os.stat_result:
        reads.append(fd)
        return original_fstat(fd)

    monkeypatch.setattr(sessions, "uri_to_path", lambda _uri, _root: path)
    monkeypatch.setattr(Path, "stat", replace_before_open)
    monkeypatch.setattr(sessions, "os", SimpleNamespace(fstat=count_fstat, name=os.name))
    _path, digest, text, size = sessions._disk_document(path.as_uri(), tmp_path, time.monotonic() + 2)
    assert text == "new" and size == 3
    assert digest == hashlib.sha256(b"new").hexdigest()
    assert len(reads) == 4


def test_disk_snapshot_read_retries_are_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.language import sessions

    path = tmp_path / "source.py"
    path.write_bytes(b"source")
    original_fstat = os.fstat
    probes: list[int] = []

    def unstable(fd: int) -> Any:
        probes.append(fd)
        info = original_fstat(fd)
        return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino, st_size=info.st_size, st_mtime_ns=info.st_mtime_ns + 1)

    monkeypatch.setattr(sessions, "os", SimpleNamespace(fstat=unstable, name=os.name))
    with pytest.raises(ToolError) as failure:
        sessions._disk_document(path.as_uri(), tmp_path, time.monotonic() + 2)
    assert failure.value.code == "lsp_document_changed"
    assert len(probes) == 6  # Initial read plus two bounded retries.


@pytest.mark.parametrize("kind,code", [("encoding", "lsp_document_encoding"), ("size", "lsp_document_too_large")])
def test_manager_invalid_document_is_never_opened(tmp_path: Path, kind: str, code: str) -> None:
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path)
    path = tmp_path / "source.py"
    path.write_bytes(b"\xff" if kind == "encoding" else b"x" * (2 * 1024 * 1024 + 1))
    manager = LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        try:
            with pytest.raises(ToolError) as failure:
                await manager.arequest(
                    tmp_path, "python", "textDocument/documentSymbol", {"textDocument": {"uri": path.as_uri()}}, timeout_sec=5,
                )
            assert failure.value.code == code
            assert manager.snapshot()["document_bytes"] == 0
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())
    assert all(item["method"] != "textDocument/didOpen" for item in manager_notifications(log))


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
            await manager_until(lambda: log.exists() and "dispatch:cancel" in log.read_text(encoding="utf-8").splitlines())
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


def test_manager_cancel_during_preparation_preserves_warm_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.language import sessions

    provider, log = manager_provider(tmp_path)
    manager = sessions.LspSessionManager((provider,), generation="test")
    preparing = threading.Event()
    release = threading.Event()
    original_sample = manager._sample

    def hold_preparation(session: Any, *, cpu_baseline: float | None = None) -> None:
        if session.current is not None and session.current.phase == "preparing" and not release.is_set():
            preparing.set()
            assert release.wait(5)
        original_sample(session, cpu_baseline=cpu_baseline)

    monkeypatch.setattr(manager, "_sample", hold_preparation)

    async def scenario() -> None:
        request = asyncio.create_task(manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "cancel"}, timeout_sec=5))
        try:
            await manager_until(preparing.is_set)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            release.set()
            result = await manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "next"}, timeout_sec=5)
            assert isinstance(result, dict)
            assert manager.snapshot()["spawn_count"] == 1
        finally:
            release.set()
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())
    assert "dispatch:cancel" not in log.read_text(encoding="utf-8").splitlines()
    assert "dispatch:next" in log.read_text(encoding="utf-8").splitlines()


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


@pytest.mark.parametrize("resource", ["cpu", "cpu_error", "wire"])
def test_manager_document_preparation_keeps_resource_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resource: str,
) -> None:
    from tools.language import sessions

    provider, log = manager_provider(tmp_path)
    path = tmp_path / "source.py"
    path.write_text("source", encoding="utf-8")
    manager = sessions.LspSessionManager((provider,), generation="test")
    original = manager._prepare_documents

    def expensive_preparation(session: Any, message: dict[str, object], deadline: float) -> Any:
        documents = original(session, message, deadline)
        if resource.startswith("cpu"):
            session.cpu_seconds += 31
            if resource == "cpu_error":
                raise ToolError("lsp_document_capacity", "fixture")
        else:
            session.transport.wire_bytes = sessions._MAX_WIRE_BYTES + 1
            raise ToolError("lsp_document_capacity", "fixture")
        return documents

    monkeypatch.setattr(manager, "_prepare_documents", expensive_preparation)

    async def scenario() -> None:
        try:
            with pytest.raises(ToolError) as failure:
                await manager.arequest(tmp_path, "python", "textDocument/hover", hover_params(path), timeout_sec=5)
            assert failure.value.code == ("lsp_resource_exceeded" if resource.startswith("cpu") else "lsp_document_capacity")
            await manager_until(lambda: manager.snapshot()["active"] == 0)
            assert "dispatch:" not in log.read_text(encoding="utf-8").splitlines()
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["unavailable", "rss"])
def test_manager_resource_denial_prevents_business_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    from tools.language import sessions

    provider, log = manager_provider(tmp_path)
    if failure == "unavailable":
        real_psutil = sessions.psutil

        def unavailable(*args: object) -> object:
            raise real_psutil.AccessDenied()

        monkeypatch.setattr(sessions, "psutil", SimpleNamespace(Process=unavailable, Error=real_psutil.Error))
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


def test_manager_hung_shutdown_reaps_owner_without_orphans(tmp_path: Path) -> None:
    from tools.language.sessions import LspSessionManager

    provider, log = manager_provider(tmp_path, "hang-shutdown")
    manager = LspSessionManager((provider,), generation="hung-shutdown")

    async def scenario() -> None:
        pid = 0
        try:
            result = await manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "hung"}, timeout_sec=5)
            assert isinstance(result, dict) and type(result["pid"]) is int
            pid = result["pid"]
            started = time.monotonic()
            await asyncio.to_thread(manager.close_all, timeout_sec=5)
            assert time.monotonic() - started < 3
            snapshot = manager.snapshot()
            assert snapshot["active"] == snapshot["quarantined"] == snapshot["in_flight"] == 0
            assert snapshot["spawn_count"] == 1
            assert snapshot["restart_count"] == 0
        finally:
            await asyncio.to_thread(manager.close_all, timeout_sec=5)
        _wait_for_exit(pid, timeout=3)
        assert pid and not _process_alive(pid)

    asyncio.run(scenario())
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines.count("dispatch:hung") == 1
    assert lines.count("shutdown-hang") == 1
    assert "shutdown" not in lines


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


def test_manager_restart_backoff_starts_after_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.language import sessions

    provider, _log = manager_provider(tmp_path)
    clock = [100.0]
    validations = 0
    starts: list[float] = []
    original = sessions.validate_provider

    def slow_validation(*args: Any, **kwargs: Any) -> LspProvider:
        nonlocal validations
        validations += 1
        if validations == 1:
            clock[0] += 6
        return original(*args, **kwargs)

    original_start = threading.Thread.start

    def fail_start(thread: threading.Thread) -> None:
        if thread.name == "lsp-session-owner":
            starts.append(clock[0])
            raise RuntimeError("fixture start unavailable")
        original_start(thread)

    monkeypatch.setattr(sessions, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(sessions, "validate_provider", slow_validation)
    monkeypatch.setattr(threading.Thread, "start", fail_start)
    manager = sessions.LspSessionManager((provider,), generation="test")

    async def scenario() -> None:
        with pytest.raises(ToolError):
            await manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "first"}, timeout_sec=30)
        with pytest.raises(ToolError) as failure:
            await manager.arequest(tmp_path, "python", "workspace/symbol", {"query": "second"}, timeout_sec=30)
        assert failure.value.code == "lsp_restart_limited"

    asyncio.run(scenario())
    assert starts == [106.0]


@pytest.mark.parametrize("kind", ["exited_child", "reused_pid"])
def test_manager_cpu_budget_retains_consumption_across_tree_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    from tools.language import sessions
    from tools.language.providers import make_session_key

    provider, _log = manager_provider(tmp_path)
    phase = [0]

    class CpuProbe:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def children(self, *, recursive: bool) -> list[CpuProbe]:
            return [CpuProbe(2)] if kind == "exited_child" and phase[0] == 0 else []

        def create_time(self) -> float:
            return float(phase[0] + 1) if kind == "reused_pid" else float(self.pid)

        def memory_info(self) -> Any:
            return SimpleNamespace(rss=100)

        def cpu_times(self) -> Any:
            used = 20 if self.pid == 2 else (10 if phase[0] == 0 else (41 if kind == "exited_child" else 35))
            return SimpleNamespace(user=used, system=0)

        def num_handles(self) -> int:
            return 1

        def num_fds(self) -> int:
            return 1

    monkeypatch.setattr(sessions, "psutil", SimpleNamespace(Process=CpuProbe, Error=sessions.psutil.Error))
    manager = sessions.LspSessionManager((provider,), generation="test")
    key = make_session_key(tmp_path, "python", provider, "test")
    session = sessions._Session(key, provider, process=cast(OwnedProcess, SimpleNamespace(pid=1, poll=lambda: None)))
    manager._sample(session)
    baseline = session.cpu_seconds
    phase[0] = 1
    with pytest.raises(ToolError) as failure:
        manager._sample(session, cpu_baseline=baseline)
    assert failure.value.code == "lsp_resource_exceeded"


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


@pytest.mark.parametrize("overflow", [False, True])
def test_transport_preparation_has_one_current_request_budget(overflow: bool) -> None:
    from tools.language.lsp import _frame

    channel, process = protocol_transport(_frame({"id": 1, "result": "x" * 300}), max_message_bytes=1000, max_request_bytes=1000)
    try:
        channel.send({"id": 1, "method": "initialize"})
        with pytest.raises(ToolError):
            channel.begin_request()
        transport_result(channel)
        channel.begin_request()
        channel.send({"method": "textDocument/didOpen", "params": {"text": "x" * 500}})
        request = {"id": 2, "method": "workspace/symbol", "params": {"query": "x" * (500 if overflow else 0)}}
        if overflow:
            with pytest.raises(ToolError) as failure:
                channel.send(request)
            assert failure.value.code == "lsp_message_too_large"
        else:
            channel.send(request)
            process.stdout.feed(_frame({"id": 2, "result": "second"}))
            assert transport_result(channel)["result"] == "second"
    finally:
        assert channel.close()


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
