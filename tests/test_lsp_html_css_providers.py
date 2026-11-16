"""Pinned HTML/CSS LSP provider registration and language inference."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.language.lsp import _language_id
from tools.language.providers import command_for, load_lsp_providers, validate_provider
from tools.language.requests import language_for_uri


@pytest.mark.parametrize(
    ("suffix", "language"),
    [(".html", "html"), (".htm", "html"), (".css", "css"), (".scss", "scss"), (".less", "less")],
)
def test_html_css_document_suffixes(suffix: str, language: str, tmp_path: Path) -> None:
    file = tmp_path / ("view" + suffix)
    assert _language_id(file) == language
    assert language_for_uri(file.as_uri()) == language


def test_reviewed_html_and_css_providers_validate_and_pin(tmp_path: Path) -> None:
    executable = tmp_path / "node.exe"
    executable.write_bytes(b"pretend pinned node")
    config = {"schema_version": 1, "providers": []}
    for provider_id, languages in (
        ("html", ["html"]), ("css", ["css", "scss", "less"])
    ):
        entry = tmp_path / (provider_id + ".js")
        entry.write_bytes(("pinned " + provider_id).encode())
        config["providers"].append({
            "provider_id": provider_id, "languages": languages,
            "executable": str(executable),
            "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "entrypoint": str(entry),
            "entrypoint_sha256": hashlib.sha256(entry.read_bytes()).hexdigest(),
            "declared_version": "fixture", "trusted_roots": [str(tmp_path)],
        })
    file = tmp_path / "providers.json"
    file.write_text(json.dumps(config), encoding="utf-8")
    providers = load_lsp_providers(file)
    assert len(providers) == 2
    for language, provider_id in [("html", "html"), ("css", "css"), ("scss", "css"), ("less", "css")]:
        provider = validate_provider(tmp_path, language, providers=providers)
        assert provider.provider_id == provider_id
        assert command_for(provider) == (str(executable), str(tmp_path / (provider_id + ".js")), "--stdio")

    (tmp_path / "css.js").write_text("mutated", encoding="utf-8")
    from core.errors import ToolError

    with pytest.raises(ToolError) as err:
        validate_provider(tmp_path, "css", providers=providers)
    assert err.value.code == "lsp_provider_changed"


def test_html_css_provider_process_budgets_remain_bounded() -> None:
    from tools.language import providers, sessions

    assert providers.MAX_PROVIDERS >= 5
    assert sessions._MAX_RSS_BYTES == 768 * 1024 * 1024
    assert sessions._MAX_TOTAL_RSS_BYTES == 3 * 1024 * 1024 * 1024
    assert sessions._MAX_TOTAL_RSS_BYTES >= 3 * sessions._MAX_RSS_BYTES



@pytest.mark.parametrize(("language", "filename", "body"), [
    ("html", "index.html", "<!doctype html><html><body><div id='card'></div></body></html>"),
    ("css", "style.css", ".card { color: red; }"),
])
def test_optional_real_html_css_lsp_acceptance(
    tmp_path: Path, language: str, filename: str, body: str,
) -> None:
    """Exercise actual installed pinned servers when the local optional package exists."""
    import asyncio
    import uuid
    from dataclasses import replace

    from tools.language.sessions import LspSessionManager

    config = Path(__file__).resolve().parents[1] / ".agent_state/lsp-html-css-providers/provider-test-config.json"
    if not config.is_file():
        pytest.skip("Optional local pinned HTML/CSS provider is not installed")
    candidates = load_lsp_providers(config)
    installed = next((p for p in candidates if p.provider_id == language), None)
    if installed is None:
        pytest.skip("Optional provider is not configured")
    provider = replace(installed, trusted_roots=(tmp_path,))
    document = tmp_path / filename
    document.write_text(body, encoding="utf-8")
    manager = LspSessionManager((provider,), generation=uuid.uuid4().hex)

    async def scenario() -> None:
        try:
            symbols = await manager.arequest(
                tmp_path, language, "textDocument/documentSymbol",
                {"textDocument": {"uri": document.as_uri()}}, timeout_sec=25,
            )
            assert isinstance(symbols, list) and symbols
            diagnostics = await manager.arequest(
                tmp_path, language, "textDocument/diagnostic",
                {"textDocument": {"uri": document.as_uri()}}, timeout_sec=25,
            )
            assert isinstance(diagnostics, dict) and diagnostics.get("kind") == "full"
            assert isinstance(diagnostics.get("items"), list)
        finally:
            await asyncio.to_thread(manager.close_workspace, tmp_path)
        assert manager.snapshot()["active"] == 0
        assert manager.snapshot()["quarantined"] == 0

    asyncio.run(scenario())
