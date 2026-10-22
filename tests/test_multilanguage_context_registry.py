from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import Client
from mcp.server import MCPServer

from core import audit
from core.resource_locks import canonical_path
from tools.language.providers import LspProvider
from tools.language.sessions import LspSessionManager
from tools.project import registry
from tools.project.index import PYTHON_METADATA_CACHE
from tools.project.project_types import detect_project_types


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


def span(start: int, end: int, line: int = 0) -> dict[str, Any]:
    return {"start": {"line": line, "character": start}, "end": {"line": line, "character": end}}


def name_span(path: Path, name: str, line: int) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").splitlines()[line]
    start = text.index(name)
    return span(start, start + len(name), line)


def hierarchy_item(path: Path, name: str, line: int, end_line: int | None = None) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    last = len(lines) - 1 if end_line is None else end_line
    return {
        "name": name, "kind": 12, "uri": path.as_uri(),
        "range": {"start": {"line": line, "character": 0},
                  "end": {"line": last, "character": len(lines[last].rstrip("\r\n"))}},
        "selectionRange": name_span(path, name, line),
    }


def symbol_row(item: dict[str, Any]) -> dict[str, Any]:
    return {"name": item["name"], "kind": item["kind"], "location": {"uri": item["uri"], "range": item["selectionRange"]}}


def document_symbol(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "uri"}


def fixed_provider(
    workspace: Path, language: str, responses: dict[str, Any], *, provider_id: str,
    log_name: str = "protocol.log", incoming_name: str | None = None,
) -> tuple[LspProvider, Path]:
    """Write a deterministic provider whose replies come from one fixed response map."""

    provider_root = workspace.parent / f"provider-{workspace.name}-{log_name.replace('.', '-')}"
    provider_root.mkdir()
    log = provider_root / log_name
    entrypoint = provider_root / "server.py"
    source = (Path(__file__).parent / "fixtures" / "persistent_lsp_server.py").read_text(encoding="utf-8")
    source = source.replace('"hoverProvider": True,',
                            '"hoverProvider": True, "workspaceSymbolProvider": True, "documentSymbolProvider": True,')
    anchor = '        result: dict[str, object] = {"method": method, "initialized": initialized}'
    assert source.count(anchor) == 1
    incoming_filter = ""
    if incoming_name is not None:
        incoming_filter = (
            f'            if method == "callHierarchy/incomingCalls" '
            f'and _dict(params.get("item")).get("name") != {incoming_name!r}:\n'
            "                result = []\n"
        )
    source = source.replace(anchor, f'''        semantic_responses = {responses!r}
        if method in semantic_responses:
            result = semantic_responses[method]
{incoming_filter}            _append_log(log_path, "semantic:" + method)
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
    provider = LspProvider(cast(Any, provider_id), (language,), executable,
                           hashlib.sha256(executable.read_bytes()).hexdigest(), entrypoint,
                           hashlib.sha256(entrypoint.read_bytes()).hexdigest(), "fixture", (workspace,))
    return provider, log


def semantic_provider(workspace: Path, *, sibling: bool = False) -> tuple[LspSessionManager, Path]:
    alpha = workspace / "alpha.ts"
    bravo = workspace / "bravo.ts"
    alpha.write_bytes(b"function alpha() {}\n")
    bravo.write_bytes(b"function bravo(){ alpha(); }\n")
    sibling_file = workspace / "alpha.tsx"
    if sibling:
        sibling_file.write_bytes(b"function alpha() {}\n")

    alpha_item, bravo_item = hierarchy_item(alpha, "alpha", 0), hierarchy_item(bravo, "bravo", 0)
    symbols = [symbol_row(alpha_item)]
    if sibling:
        symbols.append(symbol_row(hierarchy_item(sibling_file, "alpha", 0)))
    responses = {
        "workspace/symbol": symbols,
        "textDocument/definition": {"uri": alpha.as_uri(), "range": alpha_item["selectionRange"]},
        "textDocument/documentSymbol": [document_symbol(alpha_item)],
        "textDocument/references": [{"uri": bravo.as_uri(), "range": name_span(bravo, "alpha", 0)}],
        "textDocument/prepareCallHierarchy": [alpha_item],
        "callHierarchy/incomingCalls": [{"from": bravo_item, "fromRanges": [name_span(bravo, "alpha", 0)]}],
        "callHierarchy/outgoingCalls": [],
    }
    provider, log = fixed_provider(workspace, "typescript", responses, provider_id="typescript", incoming_name="alpha")
    return LspSessionManager((provider,), generation="context-fixture"), log


EXPECTED_TS_PAGES = (
    ("callers", "packages/ui/src/App.tsx", 3),
    ("callers", "packages/ui/src/helper.test.ts", 3),
    ("references", "packages/ui/src/App.tsx", 4),
    ("references", "packages/ui/src/helper.test.ts", 4),
)


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


def test_python_alias_tests_broken_source_and_delete(tmp_path: Path) -> None:
    """Declared import-bound edges are exact, broken syntax stays honest, and edits never rewrite bytes."""
    app = tmp_path / "app"
    app.mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()
    (app / "__init__.py").write_text("", encoding="utf-8")
    helpers = "def validate(value):\n    return value\n"
    (app / "helpers.py").write_text(helpers, encoding="utf-8")
    service = "from app.helpers import validate\n\ndef run(value):\n    return validate(value)\n"
    (app / "service.py").write_text(service, encoding="utf-8")
    caller = "from app import service as svc\n\ndef dispatch():\n    return svc.run(1)\n"
    (app / "caller.py").write_text(caller, encoding="utf-8")
    (app / "name_only.py").write_text(
        "def run(value):\n    return value\n\ndef invoke():\n    return run(2)\n\nalias = run\n", encoding="utf-8"
    )
    (tests / "test_service.py").write_text(
        "from app.service import run\n\ndef test_run():\n    run(1)\n", encoding="utf-8"
    )
    (tmp_path / "broken.py").write_text("def save(:\n    pass\n", encoding="utf-8")
    before = (app / "service.py").read_bytes()
    broken_before = (tmp_path / "broken.py").read_bytes()

    async def scenario() -> None:
        async with Client(context_server(), raise_exceptions=True) as client:
            result = await context_call(client, tmp_path, symbol="app.service.run", max_depth=1)
            assert [(row["file"], row["line"]) for row in result["definitions"]] == [("app/service.py", 3)]
            declared_callers = {
                ("app/caller.py", 4, "svc.run", "import_bound"),
                ("tests/test_service.py", 4, "run", "import_bound"),
            }
            assert {(row["file"], row["line"], row["call"], row["evidence"]) for row in result["callers"]} == declared_callers
            assert {(row["file"], row["line"], row["evidence"]) for row in result["tests"]} == {
                ("tests/test_service.py", 4, "import_bound")
            }
            assert {(row["file"], row["line"], row["evidence"]) for row in result["callees"]} == {
                ("app/helpers.py", 1, "import_bound")
            }
            assert {(row["file"], row["local"], row["target"]) for row in result["imports"]} == {
                ("app/service.py", "validate", "app.helpers.validate")
            }
            assert {(row["file"], row["line"], row["name"], row["evidence"]) for row in result["references"]} == {
                ("app/name_only.py", 5, "run", "name_only"),
                ("app/name_only.py", 7, "run", "name_only"),
                ("tests/test_service.py", 4, "run", "name_only"),
            }
            assert result["definitions"][0]["source"] == "def run(value):\n    return validate(value)\n"
            assert [row["file"] for row in result["parse_errors"]] == ["broken.py"]
            assert result["complete"] is False and result["semantic_complete"] is False
            assert "parse_errors" in result["incomplete_reasons"]
            assert result["snapshot"]["source_kind"] == "ast"
            assert (app / "service.py").read_bytes() == before
            assert (tmp_path / "broken.py").read_bytes() == broken_before
            (app / "caller.py").unlink()
            after_delete = await context_call(client, tmp_path, symbol="app.service.run", max_depth=1)
            assert {(row["file"], row["line"]) for row in after_delete["callers"]} == {("tests/test_service.py", 4)}
            assert "app/caller.py" not in {row["file"] for category in ("callers", "callees", "references", "tests")
                                           for row in after_delete[category]}
            assert (app / "service.py").read_bytes() == before

    asyncio.run(scenario())


def test_typescript_duplicate_helper_pages_and_incomplete_relations(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "packages" / "ui" / "src").mkdir(parents=True)
    (workspace / "packages" / "core" / "src").mkdir(parents=True)
    (workspace / "package.json").write_text('{"name": "mono", "private": true}\n', encoding="utf-8")
    (workspace / "packages" / "ui" / "package.json").write_text('{"name": "ui"}\n', encoding="utf-8")
    (workspace / "packages" / "core" / "package.json").write_text('{"name": "core"}\n', encoding="utf-8")
    ui_helper = workspace / "packages" / "ui" / "src" / "helper.ts"
    core_helper = workspace / "packages" / "core" / "src" / "helper.ts"
    app = workspace / "packages" / "ui" / "src" / "App.tsx"
    test = workspace / "packages" / "ui" / "src" / "helper.test.ts"
    ui_helper.write_text("export function helper() {\n    return 1;\n}\n", encoding="utf-8")
    core_helper.write_text("export function helper() {\n    return 2;\n}\n", encoding="utf-8")
    app.write_text('import { helper } from "./helper";\n\nexport function app() {\n    return helper();\n}\n', encoding="utf-8")
    test.write_text('import { helper } from "./helper";\n\nexport function check() {\n    return helper();\n}\n', encoding="utf-8")

    ui_item, core_item = hierarchy_item(ui_helper, "helper", 0), hierarchy_item(core_helper, "helper", 0)
    app_item, check_item = hierarchy_item(app, "app", 2), hierarchy_item(test, "check", 2)
    app_call, test_call = name_span(app, "helper", 3), name_span(test, "helper", 3)
    responses = {
        "workspace/symbol": [symbol_row(ui_item), symbol_row(core_item)],
        "textDocument/definition": {"uri": ui_helper.as_uri(), "range": ui_item["selectionRange"]},
        "textDocument/documentSymbol": [document_symbol(ui_item)],
        "textDocument/references": [
            {"uri": app.as_uri(), "range": app_call},
            {"uri": test.as_uri(), "range": test_call},
        ],
        "textDocument/prepareCallHierarchy": [ui_item],
        "callHierarchy/incomingCalls": [
            {"from": app_item, "fromRanges": [app_call]},
            {"from": check_item, "fromRanges": [test_call]},
        ],
        "callHierarchy/outgoingCalls": [],
    }
    provider, log = fixed_provider(workspace, "typescript", responses, provider_id="typescript")
    manager = LspSessionManager((provider,), generation="context-fixture")

    async def scenario() -> None:
        try:
            async with Client(context_server(manager), raise_exceptions=True) as client:
                ambiguous = await context_call(
                    client, workspace, symbol="helper", language_id="auto", include_source=False, max_depth=1
                )
                assert ambiguous["ambiguous"] is True and ambiguous["fallback_reason"] == "ambiguous_symbol"
                assert {row["file"] for row in ambiguous["definitions"]} == {
                    "packages/ui/src/helper.ts", "packages/core/src/helper.ts"
                }
                assert ambiguous["callers"] == ambiguous["callees"] == ambiguous["references"] == []
                options = {
                    "symbol": "helper", "language_id": "typescript", "backend": "semantic", "max_depth": 1,
                    "include_source": False, "include_callees": False, "include_imports": False, "include_tests": False,
                    "semantic_anchor": {"path": "packages/ui/src/helper.ts", "line": 0, "character": 17},
                }
                pages = [
                    await context_call(client, workspace, **options, max_relationships=1, offset=index)
                    for index in range(len(EXPECTED_TS_PAGES))
                ]
                combined = tuple(
                    (category, row["file"], row["line"])
                    for page in pages for category in ("callers", "references") for row in page[category]
                )
                assert combined == EXPECTED_TS_PAGES
                assert {page["total"] for page in pages} == {len(EXPECTED_TS_PAGES)}
                assert pages[0]["definitions"][0]["file"] == "packages/ui/src/helper.ts"
                assert pages[0]["definitions"][0]["symbol_key"]["relative_uri"] == "packages/ui/src/helper.ts"
                assert pages[0]["snapshot"]["document_digest"] == hashlib.sha256(ui_helper.read_bytes()).hexdigest()
                assert "packages/core/src/helper.ts" not in {row["file"] for page in pages for category in ("callers", "references")
                                                             for row in page[category]}
                dispatches = log.read_text(encoding="utf-8")
                full = await context_call(
                    client, workspace, **{**options, "include_imports": True, "include_tests": True, "max_relationships": 100}
                )
                assert full["complete"] is False and full["semantic_complete"] is False
                assert {"unsupported_relation:import", "unsupported_relation:related_test"} <= set(full["incomplete_reasons"])
                assert log.read_text(encoding="utf-8") == dispatches
        finally:
            await asyncio.to_thread(manager.close_all)

    asyncio.run(scenario())
    events = log.read_text(encoding="utf-8").splitlines()
    assert events.count("semantic:textDocument/documentSymbol") == 1
    assert events.count("initialize") == events.count("shutdown") == 1
    assert manager.snapshot()["active"] == 0


def test_php_manifest_and_monorepo_language_isolation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "app").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / "web").mkdir()
    (workspace / "package.json").write_text('{"name": "mixed"}\n', encoding="utf-8")
    (workspace / "composer.json").write_text('{"require-dev": {"phpunit/phpunit": "^10.0"}}\n', encoding="utf-8")
    php_service = workspace / "app" / "Service.php"
    php_test = workspace / "tests" / "ServiceTest.php"
    php_service.write_text("<?php\nfunction handle($value) {\n    return $value;\n}\n", encoding="utf-8")
    php_test.write_text("<?php\nfunction testHandle() {\n    return handle(1);\n}\n", encoding="utf-8")
    web_helper = workspace / "web" / "helper.ts"
    web_app = workspace / "web" / "App.tsx"
    web_helper.write_text("export function helper() {\n    return 1;\n}\n", encoding="utf-8")
    web_app.write_text('import { helper } from "./helper";\n\nexport function app() {\n    return helper();\n}\n', encoding="utf-8")
    inventory = detect_project_types(workspace)
    assert inventory.complete
    assert any(
        manifest.kind == "php" and manifest.path.name == "composer.json"
        for package in inventory.packages for manifest in package.manifests
    )

    handle_item = hierarchy_item(php_service, "handle", 1)
    php_caller = hierarchy_item(php_test, "testHandle", 1)
    php_call = name_span(php_test, "handle", 2)
    helper_item = hierarchy_item(web_helper, "helper", 0)
    ts_caller = hierarchy_item(web_app, "app", 2)
    ts_call = name_span(web_app, "helper", 3)
    php_responses = {
        "workspace/symbol": [symbol_row(handle_item)],
        "textDocument/definition": {"uri": php_service.as_uri(), "range": handle_item["selectionRange"]},
        "textDocument/documentSymbol": [document_symbol(handle_item)],
        "textDocument/references": [{"uri": php_test.as_uri(), "range": php_call}],
        "textDocument/prepareCallHierarchy": [handle_item],
        "callHierarchy/incomingCalls": [{"from": php_caller, "fromRanges": [php_call]}],
        "callHierarchy/outgoingCalls": [],
    }
    ts_responses = {
        "workspace/symbol": [symbol_row(helper_item)],
        "textDocument/definition": {"uri": web_helper.as_uri(), "range": helper_item["selectionRange"]},
        "textDocument/documentSymbol": [document_symbol(helper_item)],
        "textDocument/references": [{"uri": web_app.as_uri(), "range": ts_call}],
        "textDocument/prepareCallHierarchy": [helper_item],
        "callHierarchy/incomingCalls": [{"from": ts_caller, "fromRanges": [ts_call]}],
        "callHierarchy/outgoingCalls": [],
    }
    php_provider, php_log = fixed_provider(workspace, "php", php_responses, provider_id="phpactor", log_name="php.log")
    ts_provider, ts_log = fixed_provider(workspace, "typescript", ts_responses, provider_id="typescript", log_name="typescript.log")
    manager = LspSessionManager((php_provider, ts_provider), generation="context-fixture")

    async def scenario() -> None:
        try:
            async with Client(context_server(manager), raise_exceptions=True) as client:
                php = await context_call(
                    client, workspace, language_id="php", symbol="handle", include_source=False, max_depth=1,
                )
                assert [(row["file"], row["line"]) for row in php["definitions"]] == [("app/Service.php", 2)]
                assert [(row["file"], row["line"]) for row in php["callers"]] == [("tests/ServiceTest.php", 2)]
                assert [(row["file"], row["line"], row["name"]) for row in php["references"]] == [
                    ("tests/ServiceTest.php", 3, "handle")
                ]
                assert php["callees"] == php["imports"] == php["tests"] == []
                assert php["complete"] is False and "unsupported_relation:related_test" in php["incomplete_reasons"]
                assert php_log.read_text(encoding="utf-8").count("initialize") == 1
                assert not ts_log.exists()
                ts = await context_call(
                    client, workspace, language_id="typescript", symbol="helper", include_source=False, max_depth=1,
                )
                assert [(row["file"], row["line"]) for row in ts["definitions"]] == [("web/helper.ts", 1)]
                assert [(row["file"], row["line"]) for row in ts["callers"]] == [("web/App.tsx", 3)]
                assert [(row["file"], row["line"], row["name"]) for row in ts["references"]] == [
                    ("web/App.tsx", 4, "helper")
                ]
                assert ts["callees"] == ts["imports"] == ts["tests"] == []
                assert not [row for row in ts["definitions"] + ts["callers"] + ts["references"]
                            if row["file"].endswith(".php")]
                assert ts["complete"] is False and "unsupported_relation:import" in ts["incomplete_reasons"]
                php_events = php_log.read_text(encoding="utf-8")
                assert php_events.count("semantic:workspace/symbol") == 1
                automatic = await context_call(client, workspace, language_id="auto", include_source=False)
                assert automatic["ok"] is False and automatic["error"] == "language_required"
        finally:
            await asyncio.to_thread(manager.close_all)

    asyncio.run(scenario())
    for path in (php_log, ts_log):
        events = path.read_text(encoding="utf-8").splitlines()
        assert events.count("initialize") == events.count("shutdown") == 1
    assert manager.snapshot()["active"] == 0


def test_foreign_language_provider_reply_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "app").mkdir(parents=True)
    (workspace / "web").mkdir()
    (workspace / "composer.json").write_text("{}\n", encoding="utf-8")
    (workspace / "package.json").write_text('{"name": "mixed"}\n', encoding="utf-8")
    php_service = workspace / "app" / "Service.php"
    php_service.write_text("<?php\nfunction handle($value) {\n    return $value;\n}\n", encoding="utf-8")
    web_helper = workspace / "web" / "helper.ts"
    web_helper.write_text("export function helper() {\n    return 1;\n}\n", encoding="utf-8")
    foreign_range = name_span(web_helper, "helper", 0)
    responses = {
        "workspace/symbol": [{
            "name": "handle", "kind": 12,
            "location": {"uri": web_helper.as_uri(), "range": foreign_range},
        }],
    }
    provider, log = fixed_provider(workspace, "php", responses, provider_id="phpactor", log_name="php.log")
    manager = LspSessionManager((provider,), generation="context-fixture")

    async def scenario() -> None:
        try:
            async with Client(context_server(manager), raise_exceptions=True) as client:
                result = await context_call(
                    client, workspace, language_id="php", symbol="handle", include_source=False, max_depth=1
                )
                assert result["fallback_reason"] == "unsupported_language"
                assert result["definitions"] == result["callers"] == result["references"] == []
                assert result["complete"] is False and result["semantic_complete"] is False
                assert "helper.ts" not in json.dumps(result)
        finally:
            await asyncio.to_thread(manager.close_all)

    asyncio.run(scenario())
    events = log.read_text(encoding="utf-8").splitlines()
    assert events.count("initialize") == events.count("shutdown") == 1
    assert manager.snapshot()["active"] == 0


def test_worktree_context_never_crosses_roots(tmp_path: Path) -> None:
    trees: list[tuple[Path, str, Path]] = []
    providers: list[LspProvider] = []
    for name, value, symbol_name in (("tree-a", "a", "alpha"), ("tree-b", "b", "beta")):
        root = tmp_path / name
        root.mkdir()
        (root / "package.json").write_text(f'{{"name": "{name}"}}\n', encoding="utf-8")
        source = root / f"{symbol_name}.ts"
        source.write_text(f'export function {symbol_name}() {{\n    return "{value}";\n}}\n', encoding="utf-8")
        item = hierarchy_item(source, symbol_name, 0)
        responses = {
            "workspace/symbol": [symbol_row(item)],
            "textDocument/definition": {"uri": source.as_uri(), "range": item["selectionRange"]},
            "textDocument/documentSymbol": [document_symbol(item)],
            "textDocument/references": [],
            "textDocument/prepareCallHierarchy": [item],
            "callHierarchy/incomingCalls": [],
            "callHierarchy/outgoingCalls": [],
        }
        provider, log = fixed_provider(root, "typescript", responses, provider_id="typescript", log_name=f"{name}.log")
        providers.append(provider)
        trees.append((root, symbol_name, log))
    manager = LspSessionManager(tuple(providers), generation="context-fixture")

    async def scenario() -> None:
        try:
            async with Client(context_server(manager), raise_exceptions=True) as client:
                first, second = [
                    await context_call(client, root, language_id="typescript", symbol=symbol_name, include_source=False,
                                       max_depth=1, include_callees=False, include_references=False)
                    for root, symbol_name, _log in trees
                ]
                first_workspace = first["definitions"][0]["symbol_key"]["canonical_workspace"]
                second_workspace = second["definitions"][0]["symbol_key"]["canonical_workspace"]
                assert first_workspace == canonical_path(trees[0][0])
                assert second_workspace == canonical_path(trees[1][0])
                assert first_workspace != second_workspace
                assert first["snapshot"]["worktree_id"] != second["snapshot"]["worktree_id"]
                assert [row["file"] for row in first["definitions"]] == ["alpha.ts"]
                assert [row["file"] for row in second["definitions"]] == ["beta.ts"]
                assert "beta.ts" not in json.dumps(first) and "alpha.ts" not in json.dumps(second)
                assert first["snapshot"]["document_digest"] == hashlib.sha256((trees[0][0] / "alpha.ts").read_bytes()).hexdigest()
                assert second["snapshot"]["document_digest"] == hashlib.sha256((trees[1][0] / "beta.ts").read_bytes()).hexdigest()
                counts = [log.read_text(encoding="utf-8").count("semantic:workspace/symbol") for _root, _symbol, log in trees]
                assert counts == [1, 1]
                warm = await context_call(client, trees[0][0], language_id="typescript", symbol=trees[0][1],
                                          include_source=False, max_depth=1, include_callees=False,
                                          include_references=False)
                assert warm["definitions"][0]["symbol_key"]["canonical_workspace"] == first_workspace
                assert [log.read_text(encoding="utf-8").count("semantic:workspace/symbol")
                        for _root, _symbol, log in trees] == counts
        finally:
            await asyncio.to_thread(manager.close_all)

    asyncio.run(scenario())
    for _root, _symbol, log in trees:
        events = log.read_text(encoding="utf-8").splitlines()
        assert events.count("initialize") == events.count("shutdown") == 1
    assert manager.snapshot()["active"] == 0
