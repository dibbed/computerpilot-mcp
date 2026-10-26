"""Black-box validation: a readonly cached LSP request must avoid actual provider dispatch.

The provider fixture and its private dispatch log live OUTSIDE the fingerprinted workspace.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from test_lsp_sessions import hover_params, manager_provider

from core.errors import ToolError
from tools.language.sessions import LspSessionManager
from tools.project.project_types import lsp_input_fingerprint


def fixture(tmp_path: Path, *, mode: str = "normal", delay: float | None = None) -> tuple[Path, LspSessionManager, Path, Path]:
    server = tmp_path / "provider"
    workspace = tmp_path / "workspace"
    server.mkdir()
    workspace.mkdir()
    (workspace / "pyrightconfig.json").write_text("{}", encoding="utf8")
    source = workspace / "main.py"
    source.write_text("item = 1\n", encoding="utf8")
    provider, log = manager_provider(server, mode=mode, delay=delay)
    manager = LspSessionManager((replace(provider, trusted_roots=(workspace,)),), generation="reuse-test")
    return workspace, manager, source, log


def dispatches(log: Path) -> list[str]:
    return [line for line in log.read_text(encoding="utf8").splitlines() if line.startswith("dispatch:")]


def test_warm_read_avoids_real_wire_dispatch_and_changes_refresh(tmp_path: Path) -> None:
    workspace, manager, source, log = fixture(tmp_path)
    assert lsp_input_fingerprint(workspace, "python") is not None

    async def run() -> None:
        try:
            first = await manager.arequest(workspace, "python", "textDocument/hover", hover_params(source), timeout_sec=12)
            second = await manager.arequest(workspace, "python", "textDocument/hover", hover_params(source), timeout_sec=12)
            assert first == second
            assert len(dispatches(log)) == 1
            assert manager.snapshot()["context_cache_hits"] >= 1
            source.write_text("item = 2\n", encoding="utf8")
            changed = await manager.arequest(workspace, "python", "textDocument/hover", hover_params(source), timeout_sec=12)
            assert changed != first
            assert len(dispatches(log)) == 2
            assert isinstance(changed, dict)
            assert changed["version"] == 2
        finally:
            await asyncio.to_thread(manager.close_all)

    asyncio.run(run())
    assert manager.snapshot()["active"] == 0


def test_equivalent_concurrent_requests_share_one_dispatch(tmp_path: Path) -> None:
    workspace, manager, source, log = fixture(tmp_path)
    _ = source

    async def run() -> None:
        try:
            results = await asyncio.gather(*(manager.arequest(
                workspace, "python", "workspace/symbol", {"query": "item"}, timeout_sec=15,
            ) for _ in range(5)))
            assert all(item == results[0] for item in results)
            assert len(dispatches(log)) == 1
            assert manager.snapshot()["context_shared_waiters"] >= 1
        finally:
            await asyncio.to_thread(manager.close_all)

    asyncio.run(run())


def test_unproven_fingerprint_never_skips_wire_dispatch(tmp_path: Path) -> None:
    workspace, manager, source, log = fixture(tmp_path)
    (workspace / "pyrightconfig.json").unlink()
    assert lsp_input_fingerprint(workspace, "python") is None

    async def run() -> None:
        try:
            await manager.arequest(workspace, "python", "textDocument/hover", hover_params(source), timeout_sec=12)
            await manager.arequest(workspace, "python", "textDocument/hover", hover_params(source), timeout_sec=12)
            assert len(dispatches(log)) == 2
            assert manager.snapshot()["context_cache_hits"] == 0
        finally:
            await asyncio.to_thread(manager.close_all)

    asyncio.run(run())


def test_invalid_source_and_unavailable_provider_deny_stale_cache(tmp_path: Path) -> None:
    workspace, manager, source, log = fixture(tmp_path)

    async def run() -> None:
        try:
            await manager.arequest(workspace, "python", "textDocument/hover", hover_params(source), timeout_sec=12)
            source.unlink()
            with pytest.raises(ToolError) as raised:
                await manager.arequest(workspace, "python", "textDocument/hover", hover_params(source), timeout_sec=12)
            assert raised.value.code in {"lsp_document_unavailable", "lsp_result_stale", "lsp_document_changed"}
            assert len(dispatches(log)) == 1
        finally:
            await asyncio.to_thread(manager.close_all)

    asyncio.run(run())


def test_fingerprint_becomes_unavailable_during_request_never_replays(tmp_path: Path) -> None:
    workspace, manager, source, log = fixture(tmp_path, mode="slow", delay=0.4)

    async def run() -> None:
        try:
            pending = asyncio.create_task(manager.arequest(
                workspace, "python", "textDocument/hover", hover_params(source), timeout_sec=12,
            ))
            loop = asyncio.get_running_loop()
            cutoff = loop.time() + 7
            while not log.exists() or not dispatches(log):
                assert loop.time() < cutoff, "Expected provider to receive one dispatch"
                await asyncio.sleep(0.02)
            (workspace / "pyrightconfig.json").unlink()
            with pytest.raises(ToolError) as raised:
                await pending
            assert raised.value.code in {"lsp_document_changed", "lsp_result_stale"}
            assert len(dispatches(log)) == 1, "A stale read must never replay"
        finally:
            await asyncio.to_thread(manager.close_all)

    asyncio.run(run())


def test_externally_resolved_configuration_disables_cache(tmp_path: Path) -> None:
    workspace, manager, source, log = fixture(tmp_path)
    (workspace / "pyrightconfig.json").write_text('{"extraPaths": ["../other"]}', encoding="utf8")
    assert lsp_input_fingerprint(workspace, "python") is None

    async def run() -> None:
        try:
            await manager.arequest(workspace, "python", "textDocument/hover", hover_params(source), timeout_sec=12)
            await manager.arequest(workspace, "python", "textDocument/hover", hover_params(source), timeout_sec=12)
            assert len(dispatches(log)) == 2
            assert manager.snapshot()["context_cache_hits"] == 0
        finally:
            await asyncio.to_thread(manager.close_all)

    asyncio.run(run())
