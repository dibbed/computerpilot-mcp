from __future__ import annotations

import asyncio
import hashlib
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import Client
from mcp.server import MCPServer

from core import audit
from tools.language.providers import LspProvider
from tools.language.sessions import LspSessionManager
from tools.project import registry
from tools.project.index import PYTHON_METADATA_CACHE


@pytest.fixture(autouse=True)
def isolated_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit, "SETTINGS", replace(audit.SETTINGS, state_dir=tmp_path / "state"))


def context_server(manager: LspSessionManager | None = None) -> MCPServer:
    server = MCPServer("project-context")
    if manager is not None:
        cast(Any, server).lsp_manager = manager
    registry.register(server)
    return server


def test_patches_import_does_not_require_project_registration_first() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import tools.filesystem.patches; import tools.project.registry"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr


async def context_call(client: Client, root: Path, **kwargs: Any) -> dict[str, Any]:
    response = await client.call_tool("code_context", {"path": str(root), "symbol": "alpha", **kwargs})
    assert isinstance(response.structured_content, dict)
    return response.structured_content


def semantic_provider(workspace: Path, *, sibling: bool = False) -> tuple[LspSessionManager, Path]:
    provider_root = workspace.parent / "provider"
    provider_root.mkdir()
    log = provider_root / "protocol.log"
    entrypoint = provider_root / "server.py"
    alpha = workspace / "alpha.ts"
    bravo = workspace / "bravo.ts"
    alpha.write_bytes(b"function alpha() {}\n")
    bravo.write_bytes(b"function bravo(){ alpha(); }\n")
    sibling_file = workspace / "alpha.tsx"
    if sibling:
        sibling_file.write_bytes(b"function alpha() {}\n")

    def span(start: int, end: int) -> dict[str, Any]:
        return {"start": {"line": 0, "character": start}, "end": {"line": 0, "character": end}}

    def item(path: Path, name: str) -> dict[str, Any]:
        return {"name": name, "kind": 12, "uri": path.as_uri(), "range": span(0, len(path.read_text().splitlines()[0])),
                "selectionRange": span(9, 14)}

    alpha_item, bravo_item = item(alpha, "alpha"), item(bravo, "bravo")
    symbols = [{"name": "alpha", "kind": 12, "location": {"uri": alpha.as_uri(), "range": span(9, 14)}}]
    if sibling:
        symbols.append({"name": "alpha", "kind": 12, "location": {"uri": sibling_file.as_uri(), "range": span(9, 14)}})
    responses = {
        "workspace/symbol": symbols,
        "textDocument/definition": {"uri": alpha.as_uri(), "range": span(9, 14)},
        "textDocument/documentSymbol": [{key: value for key, value in alpha_item.items() if key != "uri"}],
        "textDocument/references": [{"uri": bravo.as_uri(), "range": span(18, 23)}],
        "textDocument/prepareCallHierarchy": [alpha_item],
        "callHierarchy/incomingCalls": [{"from": bravo_item, "fromRanges": [span(18, 23)]}],
        "callHierarchy/outgoingCalls": [],
    }
    source = (Path(__file__).parent / "fixtures" / "persistent_lsp_server.py").read_text(encoding="utf-8")
    source = source.replace('"hoverProvider": True,',
                            '"hoverProvider": True, "workspaceSymbolProvider": True, "documentSymbolProvider": True,')
    anchor = '        result: dict[str, object] = {"method": method, "initialized": initialized}'
    assert source.count(anchor) == 1
    source = source.replace(anchor, f'''        semantic_responses = {responses!r}
        if method in semantic_responses:
            result = semantic_responses[method]
            if method == "callHierarchy/incomingCalls" and _dict(params.get("item")).get("name") != "alpha":
                result = []
            _append_log(log_path, "semantic:" + method)
            _write_message({{"jsonrpc": "2.0", "id": message.get("id"), "result": result}})
            continue
''' + anchor)
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
    source = source.replace("raise SystemExit(main())", f"sys.argv += {['--log', str(log)]!r}\n    raise SystemExit(main())")
    entrypoint.write_text(source, encoding="utf-8")
    executable = Path(sys.executable).resolve()
    provider = LspProvider("typescript", ("typescript",), executable, hashlib.sha256(executable.read_bytes()).hexdigest(),
                           entrypoint, hashlib.sha256(entrypoint.read_bytes()).hexdigest(), "fixture", (workspace,))
    return LspSessionManager((provider,), generation="context-fixture"), log


def test_auto_language_requires_complete_inventory(tmp_path: Path) -> None:
    (tmp_path / "a.ts").write_bytes(b"export const a = 1;\n")
    (tmp_path / "package.json").write_bytes(b"{invalid")

    async def scenario() -> None:
        async with Client(context_server(), raise_exceptions=True) as client:
            automatic = await context_call(client, tmp_path, language_id="auto")
            assert automatic["ok"] is False and automatic["error"] == "language_required"
            explicit = await context_call(client, tmp_path, language_id="typescript")
            assert explicit["ok"] is True and explicit["backend"] == "lsp"

    asyncio.run(scenario())


def test_file_target_filters_an_exact_sibling_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager, _log = semantic_provider(workspace, sibling=True)

    async def scenario() -> None:
        try:
            async with Client(context_server(manager), raise_exceptions=True) as client:
                response = await client.call_tool("code_context", {
                    "path": str(workspace / "alpha.ts"), "symbol": "alpha",
                    "language_id": "auto", "include_source": False,
                })
                result = response.structured_content
                assert result["backend"] == "lsp" and result["ambiguous"] is False
                assert [row["file"] for row in result["definitions"]] == ["alpha.ts"]
        finally:
            await asyncio.to_thread(manager.close_all)

    asyncio.run(scenario())


def test_legacy_python_call_shape_unchanged_in_mixed_root(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_bytes(b"def alpha():\n    return 1\n")
    (tmp_path / "a.ts").write_bytes(b"function alpha() {}\n")

    async def scenario() -> None:
        async with Client(context_server(), raise_exceptions=True) as client:
            legacy = await context_call(client, tmp_path)
            explicit_null = await context_call(client, tmp_path, language_id=None)
            assert legacy["backend"] == explicit_null["backend"] == "ast"
            assert legacy["definitions"][0]["qualified_name"] == "a.alpha"
            automatic = await context_call(client, tmp_path, language_id="auto")
            assert automatic["ok"] is False and automatic["error"] == "language_required"

    asyncio.run(scenario())


@pytest.mark.parametrize("language,suffix", [("typescript", "ts"), ("javascript", "js"), ("php", "php")])
def test_non_python_without_provider_is_not_complete(tmp_path: Path, language: str, suffix: str) -> None:
    (tmp_path / f"a.{suffix}").write_bytes(b"alpha\n")

    async def scenario() -> None:
        async with Client(context_server(), raise_exceptions=True) as client:
            result = await context_call(client, tmp_path, language_id=language)
            assert result["ok"] is True and result["backend"] == "lsp"
            assert not result["complete"] and not result["semantic_complete"] and not result["definitions"]
            assert result["fallback_reason"] == "language_session_unavailable"
            denied = await context_call(client, tmp_path, language_id=language, backend="ast")
            assert denied["ok"] is False and denied["error"] == "unsupported_language"

    asyncio.run(scenario())


def test_explicit_semantic_python_falls_back_honestly(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_bytes(b"def alpha():\n    return 1\n")

    async def scenario() -> None:
        async with Client(context_server(), raise_exceptions=True) as client:
            result = await context_call(client, tmp_path, backend="semantic")
            assert result["backend"] == "ast" and result["definitions"]
            assert result["fallback_reason"] == "language_session_unavailable" and not result["semantic_complete"]

    asyncio.run(scenario())


def test_real_fixture_global_pagination_filtering_warm_cache_and_changes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager, log = semantic_provider(workspace)

    async def scenario() -> None:
        try:
            async with Client(context_server(manager), raise_exceptions=True) as client:
                options = {"language_id": "auto", "include_source": False, "max_depth": 1}
                first = await context_call(client, workspace, **options)
                assert first["backend"] == "lsp" and first["count"] == first["total"] == 2
                assert len(first["callers"]) == len(first["references"]) == 1
                before = log.read_text(encoding="utf-8")
                again = await context_call(client, workspace, **options)
                assert again == first and log.read_text(encoding="utf-8") == before
                page0 = await context_call(client, workspace, **{**options, "max_relationships": 1})
                page1 = await context_call(client, workspace, **{**options, "max_relationships": 1, "offset": 1})
                assert page0["callers"] and not page0["references"]
                assert page1["references"] and not page1["callers"]
                filtered = await context_call(client, workspace, **{**options, "include_callers": False})
                assert filtered["total"] == filtered["count"] == 1 and not filtered["callers"]
                assert filtered["references"]
                old_digest = first["snapshot"]["document_digest"]
                (workspace / "alpha.ts").write_bytes(b"function alpha() {}\n// changed\n")
                edited = await context_call(client, workspace, **options)
                assert edited["snapshot"]["document_digest"] != old_digest
                previous_dispatches = log.read_text(encoding="utf-8").count("semantic:workspace/symbol")
                (workspace / "unreturned.ts").write_bytes(b"const unused = 1;\n")
                added = await context_call(client, workspace, **options)
                assert added["definitions"]
                assert log.read_text(encoding="utf-8").count("semantic:workspace/symbol") > previous_dispatches
                PYTHON_METADATA_CACHE.invalidate(workspace / "absent.ts")
                before_flight = log.read_text(encoding="utf-8").count("semantic:workspace/symbol")
                concurrent = await asyncio.gather(*[context_call(client, workspace, **options) for _ in range(3)])
                assert all(row["snapshot"] == concurrent[0]["snapshot"] for row in concurrent)
                assert log.read_text(encoding="utf-8").count("semantic:workspace/symbol") == before_flight + 1
                (workspace / "alpha.ts").rename(workspace / "renamed.ts")
                renamed = await context_call(client, workspace, **options)
                assert not renamed["definitions"] and not renamed["complete"]
        finally:
            await asyncio.to_thread(manager.close_all)

    asyncio.run(scenario())
    events = log.read_text(encoding="utf-8").splitlines()
    assert events.count("initialize") == events.count("shutdown") == 1
    assert manager.snapshot()["active"] == 0
