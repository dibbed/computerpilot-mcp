from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from core import registry
from core.config import Settings
from core.errors import ToolError
from core.tool_profiles import PROFILE_DOMAINS, resolve_profile

CODING_CORE_NAMES = frozenset("""
discover_tool_domains recommend_tools execution_candidates execution_recommend server_health
read_file search_files list_directory get_file_info apply_patch replace_exact write_file create_file
project_summary code_context
symbol_definition symbol_references document_symbols workspace_symbols symbol_hover
call_hierarchy language_diagnostics rename_symbol apply_code_action
affected_tests verify_changes start_verification
git_status git_diff git_diff_summary git_changed_files git_stage git_commit git_conflicts
list_uncertain_operations inspect_uncertain_operation reconcile_operation acknowledge_uncertain_operation get_operation_history
job_status job_wait job_output cancel_job
""".split())


def test_coding_core_catalog_matches_exact_allowlist() -> None:
    assert len(CODING_CORE_NAMES) == 43
    assert _names("coding_core") == CODING_CORE_NAMES
    assert resolve_profile("coding_core").domains == resolve_profile("coding").domains
    assert resolve_profile("full").native_allowlist is None
    assert resolve_profile("coding").native_allowlist is None


def test_hidden_native_tool_is_denied_before_dispatch_even_if_registered() -> None:
    from mcp.server import MCPServer
    from mcp.types import CallToolResult

    from core.extensions.bridge import ExtensionMCPServer

    calls: list[bool] = []
    server = ExtensionMCPServer("hidden-dispatch", native_allowlist=frozenset({"read_file"}))

    def hidden_mutation() -> dict[str, bool]:
        calls.append(True)
        return {"ok": True}

    MCPServer.add_tool(server, hidden_mutation)
    result = asyncio.run(server.call_tool("hidden_mutation", {}))
    assert isinstance(result, CallToolResult)
    assert result.is_error
    assert result.structured_content and result.structured_content["error"] == "hidden_tool_unavailable"
    assert calls == []

    async def public_call() -> None:
        async with Client(server) as client:
            denied = await client.call_tool("hidden_mutation", {})
            assert denied.is_error
            assert denied.structured_content and denied.structured_content["error"] == "hidden_tool_unavailable"
    asyncio.run(public_call())
    assert calls == []


def test_coding_core_missing_required_registration_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "SETTINGS", replace(registry.SETTINGS, tool_profile="coding_core"))
    monkeypatch.setitem(registry.REGISTRARS, "git", lambda _server: None)
    with pytest.raises(ToolError) as raised:
        registry.create_server()
    assert raised.value.code == "profile_invalid"


def test_allowlist_public_registration_preserves_alias_and_metadata() -> None:
    from mcp.types import Icon, ToolAnnotations

    from core.extensions.bridge import ExtensionMCPServer

    server = ExtensionMCPServer("metadata", native_allowlist=frozenset({"alias"}))

    def original() -> dict[str, bool]:
        return {"ok": True}

    server.add_tool(
        original, name="alias", title="Visible alias", description="Bounded example.",
        annotations=ToolAnnotations(read_only_hint=True), icons=[Icon(src="data:image/png;base64,AA==")],
        meta={"example": True}, structured_output=True,
    )
    server.add_tool(original, name="hidden")
    tools = asyncio.run(server.list_tools())
    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "alias" and tool.title == "Visible alias"
    assert tool.description == "Bounded example."
    assert tool.annotations and tool.annotations.read_only_hint
    assert tool.icons and tool.icons[0].src == "data:image/png;base64,AA=="
    assert tool.meta == {"example": True}
    assert tool.output_schema
    assert server.native_registered_names == {"alias"}


def test_static_coding_core_journey_needs_no_catalog_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.jobs import JobStore

    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "example.py"
    before = 'def greet():\n    return "before"\n'
    source.write_bytes(before.encode("utf-8"))
    (repo / "test_example.py").write_text(
        'from example import greet\n\ndef test_greet():\n    assert greet() == "after"\n', encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    for args in (["git", "init", str(repo)], ["git", "-C", str(repo), "add", "example.py"]):
        subprocess.run(args, capture_output=True, check=True)
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")
    monkeypatch.setattr(registry, "JobStore", lambda: store)
    monkeypatch.setattr(registry, "SETTINGS", replace(registry.SETTINGS, tool_profile="coding_core"))
    patch = ('diff --git a/example.py b/example.py\n--- a/example.py\n+++ b/example.py\n@@ -1,2 +1,2 @@\n'
             ' def greet():\n-    return "before"\n+    return "after"\n')
    guards = {"example.py": hashlib.sha256(source.read_bytes()).hexdigest()}

    async def scenario() -> None:
        async with Client(registry.create_server(), raise_exceptions=True) as client:
            # A cached client lists once, then completes the whole fixed journey.
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert names == CODING_CORE_NAMES

            async def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                assert name in names
                result = await client.call_tool(name, arguments)
                assert not result.is_error, (result.structured_content, result.content)
                assert isinstance(result.structured_content, dict)
                assert result.structured_content.get("ok") is not False, result.structured_content
                return result.structured_content

            assert (await call("read_file", {"path": str(source)}))["content"] == before
            context = await call("code_context", {"path": str(repo), "symbol": "greet"})
            assert context["definitions"]
            arguments = {"root": str(repo), "patch": patch, "expected_sha256": guards}
            await call("apply_patch", {**arguments, "dry_run": True})
            assert source.read_text(encoding="utf-8") == before
            await call("apply_patch", arguments)
            assert 'return "after"' in source.read_text(encoding="utf-8")
            verified = await call("verify_changes", {
                "repo": str(repo), "changed_paths": ["example.py"], "checks": ["syntax"], "affected_only": False,
            })
            assert verified["stages"] and all(stage["status"] == "passed" for stage in verified["stages"])
            assert verified["required_unknown"] is False
            accepted = await call("start_verification", {
                "repo": str(repo), "changed_paths": ["example.py", "test_example.py"], "checks": ["pytest"],
                "total_timeout_sec": 30, "affected_only": False, "idempotency_key": "coding-core-journey",
            })
            assert accepted["accepted"]
            job_id = accepted["job_id"]
            status = await call("job_status", {"job_id": job_id})
            deadline = time.monotonic() + 30
            while status["status"] in {"queued", "running"}:
                assert time.monotonic() < deadline
                status = await call("job_wait", {"job_id": job_id, "after_version": status["version"], "timeout": 1})
            output = await call("job_output", {"job_id": job_id})
            assert status["status"] == "succeeded", output
            rows = [json.loads(line) for line in output["stdout"]["text"].splitlines()]
            assert rows[-1]["event"] == "result" and rows[-1]["ok"] and rows[-1]["freshness"] == "current"
            diff = await call("git_diff", {"path": str(repo)})
            assert '+    return "after"' in diff["patch"]

    asyncio.run(scenario())


@pytest.mark.parametrize("profile", list(PROFILE_DOMAINS))
def test_catalog_construction_has_no_disk_process_or_thread_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    from core import config, jobs
    from tools.jobs import registry as job_tools
    from tools.testing import registry as test_tools

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("catalog inspection attempted runtime activation")

    monkeypatch.setenv("MCP_TIMINGS", "0")
    monkeypatch.setattr(registry, "SETTINGS", replace(registry.SETTINGS, tool_profile=profile, state_dir=tmp_path / "absent"))
    monkeypatch.setattr(config, "ensure_runtime_dirs", forbidden)
    monkeypatch.setattr(registry, "ensure_runtime_dirs", forbidden, raising=False)
    for module in (jobs, job_tools, test_tools):
        monkeypatch.setattr(module, "JobStore", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(threading.Thread, "start", forbidden)

    async def scenario() -> None:
        server = registry.create_server()
        assert await server.list_tools()
        for name, args in (
            ("recommend_tools", {"query": "read file"}),
            ("execution_recommend", {"intent": "filesystem.read"}),
            ("server_health", {}),
        ):
            await server.call_tool(name, args)

    asyncio.run(scenario())
    assert not (tmp_path / "absent").exists()


def _names(profile: str) -> set[str]:
    original = registry.SETTINGS
    registry.SETTINGS = replace(original, tool_profile=profile)
    try:
        return {tool.name for tool in asyncio.run(registry.create_server().list_tools())}
    finally:
        registry.SETTINGS = original


def test_full_is_default_and_preserves_complete_catalog() -> None:
    assert resolve_profile(None).name == "full"
    names = _names("full")
    if os.name == "nt":
        assert len(names) == 151
    else:
        assert "run_shell" in names
        assert "run_cmd" not in names
        assert "installed_programs" not in names
        assert "windows_services" not in names
        assert "desktop_screenshot" not in names
    assert {
        "read_file",
        "run_process",
        "system_info",
        "installed_software",
        "system_services",
        *(["ui_invoke"] if os.name == "nt" else []),
        "reconcile_operation",
        "discover_tool_domains",
        "recommend_tools",
    } <= names
    assert {"workflow_execute", "workflow_operations", "workflow_reconcile", "workflow_acknowledge_operation"} <= names


def test_minimal_profile_does_not_register_workflow_tools() -> None:
    names = _names("minimal")
    assert not any(name.startswith("workflow_") for name in names)


def test_every_named_profile_is_nonempty_and_keeps_discovery() -> None:
    for profile in PROFILE_DOMAINS:
        names = _names(profile)
        assert {"discover_tool_domains", "recommend_tools", "execution_candidates", "execution_recommend", "server_health"} <= names


def test_invalid_profile_fails_startup_with_guidance() -> None:
    with pytest.raises(ToolError, match="Unknown MCP tool profile"):
        resolve_profile("mystery")


def test_discovery_reports_active_profile_and_recommends_registered_tools() -> None:
    original = registry.SETTINGS
    registry.SETTINGS = replace(original, tool_profile="coding")

    async def scenario() -> None:
        async with Client(registry.create_server()) as client:
            domains = await client.call_tool("discover_tool_domains", {})
            assert domains.structured_content and domains.structured_content["active_profile"] == "coding"
            recommendations = await client.call_tool("recommend_tools", {"query": "transactional patch"})
            assert recommendations.structured_content
            assert any(item["name"] == "apply_patch" for item in recommendations.structured_content["items"])

    try:
        asyncio.run(scenario())
    finally:
        registry.SETTINGS = original


def test_router_settings_have_safe_defaults() -> None:
    assert registry.SETTINGS.execution_router_enabled is True
    assert registry.SETTINGS.execution_router_policy == "deterministic-v1"
    assert registry.SETTINGS.execution_router_explain is True


def test_router_diagnostics_use_registered_profile_and_enrich_recommendations() -> None:
    original = registry.SETTINGS
    registry.SETTINGS = replace(original, tool_profile="documents")

    async def scenario() -> None:
        async with Client(registry.create_server()) as client:
            recommendation = await client.call_tool(
                "execution_recommend",
                {"intent": "document.excel.write", "allow_raw_desktop": True},
            )
            assert recommendation.structured_content
            assert recommendation.structured_content["selected_route"] == "native.excel"
            assert recommendation.structured_content["representative_tool"] == "excel_write_range"

            candidates = await client.call_tool(
                "execution_candidates",
                {"intent": "document.excel.write", "max_candidates": 25, "allow_raw_desktop": True},
            )
            assert candidates.structured_content
            by_route = {item["route"]: item for item in candidates.structured_content["items"]}
            assert by_route["native.excel"]["supported"] is True
            assert by_route["semantic.windows_uia"]["supported"] is False
            assert by_route["semantic.windows_uia"]["rejection_code"] == "tool_unavailable"

            recommendations = await client.call_tool("recommend_tools", {"query": "transactional patch"})
            assert recommendations.structured_content
            patch = next(
                item for item in recommendations.structured_content["items"]
                if item["name"] == "apply_patch"
            )
            assert patch["execution"]["route"] == "native.filesystem"
            assert patch["execution"]["determinism"] == 5
            assert patch["execution"]["recoverability"] == "strong"

    try:
        asyncio.run(scenario())
    finally:
        registry.SETTINGS = original


def test_disabled_and_unknown_policy_router_fail_as_structured_tool_errors() -> None:
    async def call_with(settings: Settings) -> tuple[dict[str, object], dict[str, object]]:
        original = registry.SETTINGS
        registry.SETTINGS = settings
        try:
            async with Client(registry.create_server()) as client:
                recommended = await client.call_tool("execution_recommend", {"intent": "filesystem.read"})
                candidates = await client.call_tool("execution_candidates", {"intent": "filesystem.read"})
                assert isinstance(recommended.structured_content, dict)
                assert isinstance(candidates.structured_content, dict)
                return recommended.structured_content, candidates.structured_content
        finally:
            registry.SETTINGS = original

    disabled = replace(registry.SETTINGS, execution_router_enabled=False)
    recommended, candidates = asyncio.run(call_with(disabled))
    assert recommended["ok"] is False
    assert recommended["error"] == "execution_router_disabled"
    assert candidates["ok"] is False
    assert candidates["error"] == "execution_router_disabled"

    unsupported = replace(registry.SETTINGS, execution_router_policy="mystery-v9")
    recommended, candidates = asyncio.run(call_with(unsupported))
    assert recommended["ok"] is False
    assert recommended["error"] == "unsupported_router_policy"
    assert candidates["ok"] is False
    assert candidates["error"] == "unsupported_router_policy"
