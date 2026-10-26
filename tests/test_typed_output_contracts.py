"""Typed output contracts must describe real wire shapes without changing them."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest
from mcp import types
from mcp.server import MCPServer
from pydantic import ValidationError


@pytest.fixture(autouse=True)
def _isolated_runtime_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    from core import audit, config, heartbeat, registry
    from tools.testing import verification
    from tools.workflows import registry as workflows

    runtime = tmp_path.with_name(tmp_path.name + "-runtime")
    runtime.mkdir()
    settings = replace(config.SETTINGS, state_dir=runtime / "state", memory_dir=runtime / "memory",
                       tool_profile="full", persistent_enabled=False, extensions_enabled=False)
    for module in (config, audit, heartbeat, registry, verification, workflows):
        monkeypatch.setattr(module, "SETTINGS", settings)
    monkeypatch.setenv("MCP_TIMINGS_FILE", str(runtime / "timings.jsonl"))


def _structured(result: Any) -> dict[str, Any]:
    assert isinstance(result, types.CallToolResult)
    assert result.structured_content is not None
    return result.structured_content


def _text(result: Any) -> Any:
    assert isinstance(result, types.CallToolResult)
    block = result.content[0]
    assert isinstance(block, types.TextContent)
    return json.loads(block.text)


def _write_python_project(root: Path) -> None:
    for directory in (root / "app", root / "tests"):
        directory.mkdir(parents=True, exist_ok=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "service.py").write_text("def create() -> int:\n    return 1\n", encoding="utf-8")
    (root / "app" / "api.py").write_text("from app.service import create\n\nvalue = create()\n", encoding="utf-8")
    (root / "tests" / "test_service.py").write_text(
        "from app.service import create\n\ndef test_create() -> None:\n    assert create() == 1\n",
        encoding="utf-8",
    )


def _context_result(root: Path) -> dict[str, Any]:
    from tools.project.context import lookup_code_context

    return lookup_code_context(
        root,
        "create",
        include_callers=True,
        include_callees=True,
        include_references=True,
        include_imports=True,
        include_tests=True,
        include_source=False,
        max_depth=1,
    )


def _affected_result(root: Path) -> dict[str, Any]:
    from tools.testing.impact import select_affected_tests

    return select_affected_tests(root, changed_paths=["app/service.py"], max_depth=3)


def test_output_schema_describes_context_fields() -> None:
    from tools.project.intelligence_models import CodeContextResult

    schema = CodeContextResult.model_json_schema()
    properties = schema["properties"]
    assert {"definitions", "callers", "callees", "references", "imports", "tests", "snapshot"} <= set(properties)
    assert {"complete", "semantic_complete", "incomplete_reasons", "total", "count", "offset", "limit"} <= set(properties)
    assert "result" not in properties
    assert "EvidenceEdge" in schema.get("$defs", {})


def test_context_error_uses_legacy_failure_shape(tmp_path: Path) -> None:
    from tools.project.intelligence_models import CodeContextResult
    from tools.project.registry import register

    server = MCPServer("typed-context-failure")
    register(server)

    async def scenario() -> None:
        listed = await server.list_tools()
        tool = next(item for item in listed if item.name == "code_context")
        assert tool.output_schema is not None
        assert "result" not in tool.output_schema["properties"]
        result = await server.call_tool(
            "code_context",
            {"path": str(tmp_path / "missing"), "symbol": "create"},
        )
        content = _structured(result)
        assert content["ok"] is False
        assert content["operation"] == "code_context"
        assert isinstance(content["error"], str) and content["error"]
        assert isinstance(content["message"], str) and content["message"]
        assert "hint" not in content
        assert "result" not in content
        CodeContextResult.model_validate(content)
        assert _text(result) == content

    import asyncio

    asyncio.run(scenario())


def test_missing_required_field_fails_internal_validation(tmp_path: Path) -> None:
    from core.errors import ToolError
    from core.response_models import validate_tool_output
    from tools.project.intelligence_models import CodeContextResult

    _write_python_project(tmp_path)
    result = _context_result(tmp_path)
    result.pop("symbol")
    with pytest.raises(ToolError) as raised:
        validate_tool_output(CodeContextResult, result, operation="code_context")
    assert raised.value.code == "output_contract_violation"
    assert "create" not in raised.value.message


def test_unknown_evidence_rejected(tmp_path: Path) -> None:
    from tools.project.intelligence_models import CodeContextResult

    _write_python_project(tmp_path)
    result = copy.deepcopy(_context_result(tmp_path))
    result["definitions"][0]["semantic_edge"] = {"kind": "invented_relation"}
    with pytest.raises(ValidationError):
        CodeContextResult.model_validate(result)


def test_output_roundtrip_preserves_optional_absence(tmp_path: Path) -> None:
    from core.response import failure
    from core.response_models import validate_tool_output
    from tools.project.intelligence_models import CodeContextResult

    _write_python_project(tmp_path)
    result = _context_result(tmp_path)
    wire = validate_tool_output(CodeContextResult, result, operation="code_context")
    assert set(wire) == set(result)
    assert "source" not in wire["definitions"][0]
    assert wire["fallback_reason"] is None
    assert json.loads(json.dumps(wire)) == wire

    failed = failure("code_context", ValueError("missing"))
    failed_wire = validate_tool_output(CodeContextResult, failed, operation="code_context")
    assert failed_wire == failed
    assert "hint" not in failed_wire


def test_sdk_does_not_wrap_output(tmp_path: Path) -> None:
    from tools.project.intelligence_models import CodeContextResult
    from tools.project.registry import register as register_project
    from tools.testing.models import AffectedTestsResult
    from tools.testing.registry import register as register_testing

    _write_python_project(tmp_path)
    server = MCPServer("typed-output")
    register_project(server)
    register_testing(server)
    expected_context = _context_result(tmp_path)
    expected_affected = _affected_result(tmp_path)
    from core.response_models import validate_tool_output

    async def scenario() -> None:
        listed = await server.list_tools()
        by_name = {item.name: item for item in listed}
        for name in ("code_context", "affected_tests"):
            schema = by_name[name].output_schema
            assert schema is not None
            assert "result" not in schema.get("properties", {})
        context = await server.call_tool(
            "code_context",
            {"path": str(tmp_path), "symbol": "create", "include_source": False},
        )
        context_wire = _structured(context)
        assert "result" not in context_wire
        assert context_wire == validate_tool_output(
            CodeContextResult, expected_context, operation="code_context"
        )
        affected = await server.call_tool(
            "affected_tests",
            {"repo": str(tmp_path), "changed_paths": ["app/service.py"], "max_depth": 3},
        )
        affected_wire = _structured(affected)
        assert "result" not in affected_wire
        assert affected_wire == validate_tool_output(
            AffectedTestsResult, expected_affected, operation="affected_tests"
        )
        assert _text(context) == context_wire
        assert _text(affected) == affected_wire

    import asyncio

    asyncio.run(scenario())


def test_affected_tests_model_preserves_counts_and_optional_absence(tmp_path: Path) -> None:
    from core.response_models import validate_tool_output
    from tools.testing.models import AffectedTestsResult

    _write_python_project(tmp_path)
    result = _affected_result(tmp_path)
    wire = validate_tool_output(AffectedTestsResult, result, operation="affected_tests")
    assert set(wire) == set(result)
    assert wire["test_count"] == len(wire["tests"])
    assert wire["total_exact"] is True
    assert wire["total"] >= wire["test_count"]
    assert wire["base"] is None
    assert "package_selections" in wire and wire["package_selections"] == []


def test_affected_tests_rejects_inconsistent_exact_total(tmp_path: Path) -> None:
    from tools.testing.models import AffectedTestsResult

    _write_python_project(tmp_path)
    result = _affected_result(tmp_path)
    result["total"] = max(0, result["test_count"] - 1)
    with pytest.raises(ValidationError):
        AffectedTestsResult.model_validate(result)


class _ToolCapture:
    def __init__(self) -> None:
        self.functions: dict[str, Any] = {}

    def tool(self, **kwargs: Any) -> Any:
        def decorate(function: Any) -> Any:
            self.functions[function.__name__] = function
            return function

        return decorate


def _search_functions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from tools.filesystem import registry
    from tools.filesystem.search_snapshots import SearchSnapshotStore

    store = SearchSnapshotStore(
        tmp_path / "snapshots",
        ttl_sec=60,
        max_bytes=1_000_000,
        max_count=8,
        clock=lambda: 100.0,
    )
    monkeypatch.setattr(registry, "SEARCH_SNAPSHOTS", store)
    server = _ToolCapture()
    registry.register(server)  # type: ignore[arg-type]
    return server.functions


def _search_fixture(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for index in range(4):
        (root / f"hit_{index}.txt").write_text("plain\n", encoding="utf-8")
    (root / "secret.txt").write_text("needle secret-canary-value\n", encoding="utf-8")


def test_search_files_variants_validate_and_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.response_models import validate_tool_output
    from tools.filesystem.models import SearchFilesResult

    root = tmp_path / "workspace"
    _search_fixture(root)
    functions = _search_functions(tmp_path, monkeypatch)
    search = functions["search_files"]

    def check(result: dict[str, Any]) -> dict[str, Any]:
        wire = validate_tool_output(SearchFilesResult, result, operation="search_files")
        assert wire == result
        return wire

    normal = check(search(str(root), "hit_", search_type="name", max_results=2))
    assert normal["count"] == 2 and normal["total_count"] == 4
    assert "from_snapshot" not in normal
    first = check(search(str(root), "hit_", search_type="name", snapshot=True, max_results=2))
    assert first["snapshot_created"] is True and first["from_snapshot"] is True
    assert first["snapshot_id"] and first["cursor"]
    second = check(search(str(root), "hit_", search_type="name", cursor=first["cursor"], max_results=2))
    assert second["scanned_files"] == 0 and second["from_snapshot"] is True
    stream = check(search(str(root), "hit_", search_type="name", count_mode="none", max_results=2))
    assert stream["count_mode"] == "none" and stream["total_count"] is None
    stream_snapshot = check(
        search(str(root), "hit_", search_type="name", count_mode="none", snapshot=True, max_results=2)
    )
    assert stream_snapshot["snapshot_created"] is True and stream_snapshot["total_count"] is None
    empty = check(search(str(root), "absent", search_type="name"))
    assert empty["items"] == [] and empty["count"] == empty["total_count"] == 0
    content = check(search(str(root), "secret-canary", search_type="content"))
    assert content["items"] and content["items"][0]["matched_in"] == ["content"]
    assert "secret-canary" not in json.dumps(content)
    stale = search(str(root), "other", search_type="name", cursor=first["cursor"])
    assert stale["ok"] is False and stale["error"] == "search_cursor_mismatch"
    SearchFilesResult.model_validate(stale)


def test_search_files_registry_schema_and_wire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.filesystem.models import SearchFilesResult
    from tools.filesystem.registry import register

    root = tmp_path / "workspace"
    _search_fixture(root)
    _search_functions(tmp_path, monkeypatch)
    server = MCPServer("typed-search")
    register(server)
    from core.response_models import validate_tool_output

    async def scenario() -> None:
        listed = await server.list_tools()
        tool = next(item for item in listed if item.name == "search_files")
        schema = tool.output_schema
        assert schema is not None
        assert "result" not in schema.get("properties", {})
        result = await server.call_tool(
            "search_files",
            {"path": str(root), "query": "hit_", "search_type": "name", "max_results": 2},
        )
        wire = _structured(result)
        assert "result" not in wire
        assert wire == validate_tool_output(SearchFilesResult, wire, operation="search_files")
        assert wire["count"] == 2

    import asyncio

    asyncio.run(scenario())


def test_project_summary_variants_validate_and_roundtrip(tmp_path: Path) -> None:
    from core.response_models import validate_tool_output
    from tools.project import service
    from tools.project.models import ProjectSummaryResult

    def check(root: Path) -> dict[str, Any]:
        wire = validate_tool_output(
            ProjectSummaryResult,
            service.project_summary(root, 100),
            operation="project_summary",
        )
        assert set(wire) == set(service.project_summary(root, 100))
        return wire

    empty = tmp_path / "empty"
    empty.mkdir()
    empty_result = check(empty)
    assert empty_result["languages"] == [] and empty_result["dependencies"] == []

    mixed = tmp_path / "mixed"
    mixed.mkdir()
    (mixed / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (mixed / "app.ts").write_text("export const value = 1;\n", encoding="utf-8")
    (mixed / "package.json").write_text(
        json.dumps({"dependencies": {"react": "19.0.0", "express": "5.0.0"}}),
        encoding="utf-8",
    )
    (mixed / "tests").mkdir()
    (mixed / "tests" / "test_main.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    mixed_result = check(mixed)
    assert {item["name"] for item in mixed_result["languages"]} == {"Python", "TypeScript"}
    assert set(mixed_result["frameworks"]) >= {"React", "Express"}
    assert "tests" in mixed_result["test_paths"]
    assert mixed_result["dependency_count"] >= len(mixed_result["dependencies"])

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "package.json").write_text("{invalid", encoding="utf-8")
    (broken / "pyproject.toml").write_text("[invalid", encoding="utf-8")
    broken_result = check(broken)
    assert broken_result["ok"] is True


def test_project_summary_rejects_impossible_dependency_count(tmp_path: Path) -> None:
    from tools.project import service
    from tools.project.models import ProjectSummaryResult

    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"react": "19.0.0", "express": "5.0.0"}}),
        encoding="utf-8",
    )
    result = service.project_summary(tmp_path, 100)
    result["dependency_count"] = 0
    with pytest.raises(ValidationError):
        ProjectSummaryResult.model_validate(result)


@pytest.mark.parametrize("status", ["queued", "running", "orphaned", "succeeded", "failed", "cancelled", "timed_out", "interrupted"])
def test_job_status_variants_preserve_wire(status: str) -> None:
    from core.response_models import JobStatusResult, validate_tool_output

    wire = {"ok": True, "job_id": "a" * 32, "status": status, "created": 1.0, "updated": 2.0,
            "version": 1, "queue_deadline": None, "pid": None, "exit_code": None,
            "cancel_requested": 0, "error": None}
    assert validate_tool_output(JobStatusResult, wire, operation="job_status") == wire
    with pytest.raises(ValidationError):
        JobStatusResult.model_validate({**wire, "status": "future_state"})
    with pytest.raises(ValidationError):
        JobStatusResult.model_validate({**wire, "status": None})


def test_workflow_status_preserves_redaction_and_uncertainty(tmp_path: Path) -> None:
    from core.response_models import WorkflowStatusResult, validate_tool_output
    from core.workflow_models import StepDefinition, WorkflowDefinition, WorkflowState
    from core.workflow_store import WorkflowStore

    store = WorkflowStore(tmp_path / "workflow.sqlite3")
    wire = store.create(WorkflowDefinition("fixture", (StepDefinition("tests", "verify_changes", {"cwd": str(tmp_path)}),)),
                        {"password": "private-status-canary"})
    for state in WorkflowState:
        with store._connect() as connection:
            connection.execute("UPDATE workflows SET state=? WHERE workflow_id=?", (state.value, wire["workflow_id"]))
        wire = store.get(wire["workflow_id"])
        assert "ok" not in wire
        assert "private-status-canary" not in json.dumps(wire)
        assert validate_tool_output(WorkflowStatusResult, wire, operation="workflow_status") == wire
    with pytest.raises(ValidationError):
        WorkflowStatusResult.model_validate({**wire, "state": "future_state"})


def test_health_bounded_and_unavailable_wire_variants() -> None:
    from core.health_snapshot import MAX_RUNTIME_HEALTH_BYTES, bound_runtime_health, validate_runtime_health
    from core.response_models import HealthResult, validate_tool_output

    full = {"ok": True, "health_status": "healthy", "snapshot_schema_version": 1,
            "runtime_generation": "generation", "collected_at": "2026-10-25T00:00:00+00:00",
            "collected_monotonic": 1.0, "extensions": {"items": []}, "tool_count": 151,
            "server": "fixture", "version": "0.8.0", "tool_profile": "full", "unique_tool_names": True}
    summary = bound_runtime_health({**full, "large": "x" * MAX_RUNTIME_HEALTH_BYTES})
    fallback = bound_runtime_health({**full, "server": "x" * MAX_RUNTIME_HEALTH_BYTES})
    unavailable = {"ok": True, "health_status": "unavailable", "snapshot_reason": "runtime_not_started",
                   "server": "fixture", "version": "0.8.0", "tool_profile": "full", "tool_count": 151,
                   "unique_tool_names": True}
    for wire in [bound_runtime_health(full), summary, fallback, unavailable,
                 validate_runtime_health(full, "other", now=1.0)]:
        assert validate_tool_output(HealthResult, wire, operation="server_health") == wire
    assert fallback["ok"] is False and "operation" not in fallback
    for invalid in [{**full, "snapshot_schema_version": 2}, {**full, "collected_monotonic": float("inf")},
                    {**full, "collected_at": "invalid"}, {**full, "collected_at": "2026-10-25T00:00:00"}]:
        with pytest.raises(ValidationError):
            HealthResult.model_validate(invalid)


def test_operational_status_registry_schemas_and_future_status_denial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from core import registry as core_registry
    from tools.jobs.registry import register

    class Store:
        def get(self, job_id: str) -> dict[str, Any]:
            return {"job_id": job_id, "status": "future-state-private-canary"}

    server = MCPServer("typed-job")
    register(server, store_getter=lambda: Store())  # type: ignore[arg-type, return-value]
    main = core_registry.create_server()

    async def scenario() -> None:
        for item in [*await server.list_tools(), *await main.list_tools()]:
            if item.name in {"job_status", "workflow_status", "server_health"}:
                assert item.output_schema is not None
                assert "result" not in item.output_schema.get("properties", {})
                assert item.output_schema.get("properties", {}).get("status") or item.output_schema.get("anyOf")
        denied = _structured(await server.call_tool("job_status", {"job_id": "a" * 32}))
        assert denied["ok"] is False and denied["error"] == "output_version_unsupported"
        assert "private-canary" not in json.dumps(denied)
        health = _structured(await main.call_tool("server_health", {}))
        assert health["health_status"] == "unavailable" and "result" not in health
        main.runtime_started = True  # type: ignore[attr-defined]

        async def bounded(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"ok": False, "health_status": "unavailable", "snapshot_truncated": True, "snapshot_reason": "payload_limit"}

        monkeypatch.setattr(core_registry, "collect_server_health", bounded)
        fallback = _structured(await main.call_tool("server_health", {}))
        assert fallback == await bounded()
        missing = _structured(await main.call_tool("workflow_status", {"workflow_id": "absent"}))
        assert missing["ok"] is False and missing["error"] == "workflow_not_found"

    asyncio.run(scenario())


@pytest.mark.parametrize("unknown", [False, True])
def test_failed_verification_remains_valid_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unknown: bool) -> None:
    from core.response_models import validate_tool_output
    from tools.testing import verification
    from tools.testing.models import VerificationResult

    (tmp_path / "broken.py").write_text("x =\n", encoding="utf-8")
    if unknown:
        monkeypatch.setattr(verification, "run_ruff_summary", lambda *a, **kw: {
            "ok": False, "exit_code": None, "required_unknown": True, "diagnostics": [], "summary": "unavailable",
        })
    result = verification.verify_changed_repository(tmp_path, changed_paths=["broken.py"], checks=["ruff"] if unknown else ["syntax"])
    assert result["ok"] is False and result["required_unknown"] is unknown
    assert validate_tool_output(VerificationResult, result, operation="verify_changes") == result
    with pytest.raises(ValidationError):
        VerificationResult.model_validate({**result, "ok": True})


@pytest.mark.parametrize("reason", [None, ""])
def test_advertised_verification_schema_requires_skip_reason(reason: str | None) -> None:
    import asyncio

    from jsonschema import Draft202012Validator

    from tools.testing import registry
    from tools.testing.models import VerificationStage

    server = MCPServer("verification-schema-conditions")
    registry.register(server)

    async def scenario() -> None:
        tool = next(tool for tool in await server.list_tools() if tool.name == "verify_changes")
        assert tool.output_schema is not None
        stage = {"name": "pytest", "status": "skipped", "duration_ms": 0, "targets": []}
        if reason is not None:
            stage["reason"] = reason
        with pytest.raises(ValidationError):
            VerificationStage.model_validate(stage)
        validator = Draft202012Validator({"$ref": "#/$defs/VerificationStage", "$defs": tool.output_schema["$defs"]})
        assert not validator.is_valid(stage), "A skipped stage must advertise its required reason."

    asyncio.run(scenario())


@pytest.mark.parametrize("report", [
    {"kind": "full"},
    {"kind": "full", "items": None},
    {"kind": "unchanged"},
    {"kind": "unchanged", "resultId": None},
    {"kind": "unchanged", "resultId": "v1", "items": []},
    {"kind": "unchanged", "resultId": "v1", "items": None},
])
def test_advertised_diagnostics_schema_rejects_incomplete_reports(report: dict[str, Any]) -> None:
    import asyncio

    from jsonschema import Draft202012Validator

    from tools.language import registry
    from tools.language.requests import DiagnosticsResult

    server = MCPServer("diagnostic-schema-conditions")
    registry.register(server)

    async def scenario() -> None:
        tool = next(tool for tool in await server.list_tools() if tool.name == "language_diagnostics")
        validator = Draft202012Validator(tool.output_schema)
        for payload in [report, {"kind": "full", "items": [], "relatedDocuments": {"file:///fixture.py": report}}]:
            wire = {"ok": True, "method": "textDocument/diagnostic", "result": payload}
            with pytest.raises(ValidationError):
                DiagnosticsResult.model_validate(wire)
            assert not validator.is_valid(wire), "Advertised schema accepted a report rejected at runtime."

    asyncio.run(scenario())


def test_diagnostics_partial_report_is_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from tools.language import registry
    from tools.language.requests import DiagnosticsResult

    payload: dict[str, Any] = {"kind": "full", "items": [{
        "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 2}},
        "severity": 1, "source": "fixture", "message": "diagnostic", "data": {"token": "diagnostic-secret-canary"},
    }], "complete": False}

    async def request(*args: Any, **kwargs: Any) -> Any:
        return payload

    monkeypatch.setattr(registry, "_request", request)
    server = MCPServer("typed-diagnostics")
    registry.register(server)

    async def scenario() -> None:
        args = {"root": str(tmp_path), "params": {"textDocument": {"uri": (tmp_path / "a.py").as_uri()}}}
        result = _structured(await server.call_tool("language_diagnostics", args))
        assert result["result"]["complete"] is False
        assert "diagnostic-secret-canary" not in json.dumps(result)
        assert DiagnosticsResult.model_validate(result).model_dump(mode="json") == result
        payload.clear()
        payload.update({"kind": "unchanged", "resultId": "cached"})
        unchanged = _structured(await server.call_tool("language_diagnostics", args))
        assert unchanged == {"ok": True, "method": "textDocument/diagnostic", "result": payload}
        payload.clear()
        payload.update({"kind": "full", "items": [{"message": "invalid-private-canary"}]})
        invalid = _structured(await server.call_tool("language_diagnostics", args))
        assert invalid["ok"] is False and invalid["error"] == "output_contract_violation"
        assert "private-canary" not in json.dumps(invalid)

    asyncio.run(scenario())


def test_router_output_cannot_authorize_dispatch() -> None:
    from core.execution_models import ExecutionIntent
    from core.execution_router import ExecutionRouter
    from core.platform import detect_capabilities
    from core.response_models import RouteRecommendation, validate_tool_output

    for available in [frozenset(), frozenset({"read_file"})]:
        router = ExecutionRouter(registered_tools=available, capabilities=detect_capabilities())
        for explain in [False, True]:
            wire = router.recommend(ExecutionIntent(name="filesystem.read"), explain=explain)
            assert validate_tool_output(RouteRecommendation, wire, operation="execution_recommend") == wire
            assert "permission_granted" not in wire and "execute" not in wire
            with pytest.raises(ValidationError):
                RouteRecommendation.model_validate({**wire, "permission_granted": True})


def test_ten_tool_output_schemas_preserve_catalog_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from jsonschema import Draft202012Validator

    from core import registry

    names = {"code_context", "affected_tests", "search_files", "project_summary", "job_status", "workflow_status",
             "server_health", "verify_changes", "language_diagnostics", "execution_recommend"}

    async def scenario() -> None:
        for profile in ["full", "coding"]:
            from dataclasses import replace

            monkeypatch.setattr(registry, "SETTINGS", replace(registry.SETTINGS, tool_profile=profile))
            server = registry.create_server()
            tools = {tool.name: tool for tool in await server.list_tools()}
            assert names <= tools.keys()
            if profile == "full":
                # Windows-only tool domains are excluded by capability negotiation on POSIX.
                assert len(tools) == (151 if os.name == "nt" else 137)
            for name in names:
                schema = tools[name].output_schema
                assert schema is not None
                assert schema["type"] == "object"
                Draft202012Validator.check_schema(schema)
                assert set(schema.get("properties", {})) != {"result"}
                assert len(json.dumps(schema).encode()) < 50_000
            assert "execution_run" not in tools

    asyncio.run(scenario())



def test_diagnostics_real_owned_protocol_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import sys

    from jsonschema import Draft202012Validator

    from tools.language import registry

    target = tmp_path / "a.py"
    target.write_text("'😀'\n", encoding="utf-8")
    fixture = Path(__file__).parent / "fixtures" / "strict_lsp_server.py"
    monkeypatch.setattr(registry, "_command", lambda: [sys.executable, str(fixture)])
    server = MCPServer("typed-diagnostic-protocol")
    registry.register(server)

    async def scenario() -> None:
        tool = next(tool for tool in await server.list_tools() if tool.name == "language_diagnostics")
        validator = Draft202012Validator(tool.output_schema)
        params: dict[str, Any] = {"textDocument": {"uri": target.as_uri()}}
        for kind in ["full", "unchanged"]:
            wire = _structured(await server.call_tool("language_diagnostics", {"root": str(tmp_path), "params": params}))
            validator.validate(wire)
            assert wire["ok"] and wire["result"]["kind"] == kind
            assert "private-protocol-canary" not in json.dumps(wire)
            if kind == "full":
                assert wire["result"]["items"][0]["range"]["end"]["character"] == 2
            params["previousResultId"] = "fixture-v1"

    asyncio.run(scenario())


@pytest.mark.parametrize("query,offset", [("hit_", 4), ("hit_", 100), ("absent", 100)])
def test_search_empty_page_past_total_is_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, query: str, offset: int) -> None:
    root = tmp_path / "workspace"
    _search_fixture(root)
    search = _search_functions(tmp_path, monkeypatch)["search_files"]
    result = search(str(root), query, search_type="name", offset=offset)
    assert result["ok"] is True, result
    assert result["items"] == [] and result["count"] == 0
    assert result["offset"] == offset and not result["has_more"]
    assert result["next_offset"] is None


@pytest.mark.parametrize("kind", ["context", "affected", "search", "summary"])
def test_success_schema_and_runtime_reject_missing_or_null_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    from jsonschema import Draft202012Validator

    from tools.filesystem.models import SearchFilesResult
    from tools.project import service
    from tools.project.intelligence_models import CodeContextResult
    from tools.project.models import ProjectSummaryResult
    from tools.testing.models import AffectedTestsResult

    _write_python_project(tmp_path)
    cases: dict[str, tuple[Any, dict[str, Any], str]] = {
        "context": (CodeContextResult, _context_result(tmp_path), "definitions"),
        "affected": (AffectedTestsResult, _affected_result(tmp_path), "tests"),
        "search": (SearchFilesResult, _search_functions(tmp_path, monkeypatch)["search_files"](
            str(tmp_path), "service", search_type="name"), "items"),
        "summary": (ProjectSummaryResult, service.project_summary(tmp_path, 100), "languages"),
    }
    model, wire, field = cases[kind]
    schema = Draft202012Validator(model.model_json_schema())
    assert schema.is_valid(wire)
    for malformed in [{"ok": True}, {"ok": False}, {**wire, field: None}]:
        assert not schema.is_valid(malformed), (kind, malformed)
        with pytest.raises(ValidationError):
            model.model_validate(malformed)
    for suffix in ["\n", "\r", "\u2028", "\u2029", ".legacy"]:
        candidate = {**wire, field + suffix: None}
        schema.validate(candidate)
        assert model.model_validate(candidate).model_dump(mode="json", exclude_unset=True) == candidate


@pytest.mark.parametrize("extra", [object(), float("nan"), float("inf")])
def test_output_serialization_failure_is_safe_contract_error(extra: Any) -> None:
    from core.errors import ToolError
    from core.response_models import JobStatusResult, validate_tool_output

    value = {"ok": False, "operation": "job_status", "error": "fixture", "message": "safe", "opaque": extra}
    with pytest.raises(ToolError) as raised:
        validate_tool_output(JobStatusResult, value, operation="job_status")
    assert raised.value.code == "output_contract_violation"
    assert raised.value.message == "job_status output does not satisfy its declared contract."


def test_compact_output_schema_preserves_literal_data_and_nullable_constraints() -> None:
    from jsonschema import Draft202012Validator
    from pydantic import ConfigDict

    from core.response_models import WireRootModel

    literal = {"title": "keep", "description": "keep", "default": {"examples": [1]}}
    original: dict[str, Any] = {
        "type": "object", "title": "annotation", "description": "annotation", "additionalProperties": False,
        "properties": {
            "title": {"type": "string", "const": "keep", "title": "annotation"},
            "default": {"const": literal},
            "description": {"type": "string", "enum": ["keep", None]},
            "nullable": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
            "guarded": {"type": "integer", "anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
            "nested": {"type": "array", "items": {"type": "object",
                       "properties": {"title": {"const": "nested", "type": "string"}},
                       "required": ["title"], "additionalProperties": False}},
        },
        "required": ["title", "default", "description", "nullable", "guarded", "nested"],
    }

    class Contract(WireRootModel[dict[str, Any]]):
        model_config = ConfigDict(json_schema_extra=copy.deepcopy(original))

    compact = Contract.model_json_schema()
    assert Contract.model_config["json_schema_extra"] == original
    assert "title" not in compact and "description" not in compact
    assert compact["properties"]["default"]["const"] == literal
    assert set(compact["properties"]) == set(original["properties"])
    assert set(compact.get("$defs", {})) == set(original.get("$defs", {}))
    assert Contract.model_json_schema() == compact
    before, after = Draft202012Validator(original), Draft202012Validator(compact)
    Draft202012Validator.check_schema(compact)
    valid = {"title": "keep", "default": literal, "description": "keep",
             "nullable": 1, "guarded": 1, "nested": [{"title": "nested"}]}
    cases = [
        (valid, True), ({**valid, "nullable": None}, True),
        ({**valid, "nullable": 0}, False), ({**valid, "nullable": False}, False),
        ({**valid, "guarded": None}, False), ({**valid, "description": None}, False),
        ({**valid, "title": "changed"}, False), ({**valid, "default": {"title": "lost"}}, False),
        ({**valid, "nested": [{"title": "changed"}]}, False), ({**valid, "unknown": None}, False),
    ]
    for payload, expected in cases:
        assert before.is_valid(payload) is expected
        assert after.is_valid(payload) is expected


def test_compact_output_schema_preserves_recursive_local_refs() -> None:
    from jsonschema import Draft202012Validator
    from pydantic import BaseModel, ConfigDict, Field, RootModel

    from core.response_models import WireRootModel

    class TreeNode(BaseModel):
        name: str = Field(min_length=1, description="Node name")
        children: list[TreeNode] = Field(default_factory=list)

    TreeNode.model_rebuild()

    class Original(RootModel[TreeNode]):
        model_config = ConfigDict(json_schema_extra={"type": "object"})

    class Compact(WireRootModel[TreeNode]):
        pass

    original, compact = Original.model_json_schema(), Compact.model_json_schema()
    assert compact["$ref"] == original["$ref"] == "#/$defs/TreeNode"
    assert set(compact["$defs"]) == set(original["$defs"]) == {"TreeNode"}
    assert compact["$defs"]["TreeNode"]["properties"]["children"]["items"] == {"$ref": "#/$defs/TreeNode"}
    Draft202012Validator.check_schema(compact)
    before, after = Draft202012Validator(original), Draft202012Validator(compact)
    for payload, expected in [
        ({"name": "root", "children": [{"name": "branch", "children": [{"name": "leaf"}]}]}, True),
        ({"name": "root"}, True),
        ({"name": "root", "children": [{"name": ""}]}, False),
        ({"name": "root", "children": [None]}, False),
        ({"children": []}, False),
    ]:
        assert before.is_valid(payload) is expected
        assert after.is_valid(payload) is expected
        if expected:
            assert Compact.model_validate(payload).model_dump(mode="json") == payload
        else:
            with pytest.raises(ValidationError):
                Compact.model_validate(payload)


@pytest.mark.parametrize("branch,valid,invalid", [
    ({"type": "number", "minimum": 1, "exclusiveMaximum": 3, "multipleOf": 0.5},
     [1, 1.5, None], [0.5, 3, 1.25, False]),
    ({"type": "string", "minLength": 2, "maxLength": 4, "pattern": "^a"},
     ["ab", None], ["bbb", "a", "abcde", 1]),
    ({"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 1, "maxItems": 2,
      "uniqueItems": True, "contains": {"const": 2}, "minContains": 1, "maxContains": 1},
     [[1, 2], None], [[], [1], [2, 2], [2, 3, 4], [0, 2]]),
    ({"type": "object", "properties": {"label": {"type": "string", "minLength": 1},
                                       "count": {"type": "integer", "minimum": 1}},
      "required": ["label"], "minProperties": 1, "maxProperties": 2, "additionalProperties": False},
     [{"label": "a"}, {"label": "a", "count": 1}, None], [{}, {"label": ""}, {"label": "a", "count": 0}]),
    ({"type": "string", "enum": ["keep"]}, ["keep", None], ["changed", 1]),
    ({"type": "object", "if": {"properties": {"flag": {"const": True}}, "required": ["flag"]},
      "then": {"required": ["data"]}, "else": {"not": {"required": ["data"]}}},
     [{"flag": True, "data": 1}, {"flag": False}, None], [{"flag": True}, {"flag": False, "data": 1}]),
])
def test_compact_nullable_schemas_preserve_constraint_acceptance(
    branch: dict[str, Any], valid: list[Any], invalid: list[Any],
) -> None:
    from jsonschema import Draft202012Validator
    from pydantic import ConfigDict

    from core.response_models import WireRootModel

    original: dict[str, Any] = {
        "type": "object", "properties": {"value": {"anyOf": [{"type": "null"}, branch]}},
        "required": ["value"], "additionalProperties": False,
    }

    class Contract(WireRootModel[dict[str, Any]]):
        model_config = ConfigDict(json_schema_extra=copy.deepcopy(original))

    compact = Contract.model_json_schema()
    Draft202012Validator.check_schema(compact)
    before, after = Draft202012Validator(original), Draft202012Validator(compact)
    for values, expected in [(valid, True), (invalid, False)]:
        for value in values:
            assert before.is_valid({"value": value}) is expected
            assert after.is_valid({"value": value}) is expected
    assert not after.is_valid({})


def test_compact_success_conditions_preserve_explicit_nullable_fields() -> None:
    from jsonschema import Draft202012Validator

    from core.response_models import SuccessWireModel

    class Contract(SuccessWireModel):
        success_required_fields = frozenset({"value", "nullable"})
        nullable_success_fields = frozenset({"nullable"})
        value: str | None = None
        nullable: str | None = None

    schema = Draft202012Validator(Contract.model_json_schema())
    success = {"ok": True, "value": "present", "nullable": None}
    failure = {"ok": False, "operation": "fixture", "error": "fixture", "message": "safe", "value": None}
    for payload, expected in [
        (success, True), (failure, True), ({**success, "value\n": None}, True),
        ({**success, "value": None}, False), ({"ok": True, "value": "present"}, False),
        ({**failure, "error": None}, False), ({"ok": False}, False),
    ]:
        assert schema.is_valid(payload) is expected
        if expected:
            assert Contract.model_validate(payload).model_dump(mode="json") == payload
        else:
            with pytest.raises(ValidationError):
                Contract.model_validate(payload)


def test_portable_catalogue_excludes_only_platform_gated_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from dataclasses import replace

    from core import registry
    from core.platform import detect_capabilities

    fake_posix = replace(
        detect_capabilities(),
        system="linux", desktop_input=False, desktop_screenshot=False, semantic_ui=False,
    )
    monkeypatch.setattr(registry, "detect_capabilities", lambda: fake_posix)

    async def scenario() -> None:
        for profile, count in (("full", 137), ("coding", 96), ("coding_core", 43)):
            monkeypatch.setattr(registry, "SETTINGS", replace(registry.SETTINGS, tool_profile=profile))
            tools = await registry.create_server().list_tools()
            assert len(tools) == count
            assert len({tool.name for tool in tools}) == count
            assert {"symbol_definition", "project_summary", "language_diagnostics"} <= {tool.name for tool in tools}

    asyncio.run(scenario())


def test_project_summary_without_toml_parser_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.project import service

    monkeypatch.setattr(service, "_toml", None)
    (tmp_path / "pyproject.toml").write_text("[project]\ndependencies=['safe']\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("pass\n", encoding="utf-8")
    result = service.project_summary(tmp_path, 100)
    assert result["ok"] is True
    assert "Python" in {name["name"] for name in result["languages"]}
    assert "safe" not in result["dependencies"]


def test_p08_catalogue_budget_and_non_output_metadata_are_preserved() -> None:
    import asyncio
    import hashlib
    from dataclasses import replace

    from core import registry

    baseline = {
        "full": (151 if os.name == "nt" else 137, 161424, "dd943f86782309bdadfce181d99ff868ae7a1a496a2aeaadcb177ad3e47257e6"),
        "coding": (96, 100462, "63d7cefc60e4f461de32b37fa45d3aeca267ada484e074114f9824d0d570e593"),
        "coding_core": (43, 47156, "6bac11be7330500d44e2439fe434e216faf46e0ccd025b8c2cb7c7614b88275f"),
    }
    names = {"code_context", "affected_tests", "search_files", "project_summary", "job_status",
             "workflow_status", "server_health", "verify_changes", "language_diagnostics", "execution_recommend"}

    def encode(value: Any) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

    async def scenario() -> None:
        schemas: dict[str, Any] = {}
        for profile, (count, before_bytes, metadata_sha) in baseline.items():
            server_settings = replace(registry.SETTINGS, tool_profile=profile, persistent_enabled=False, extensions_enabled=False)
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(registry, "SETTINGS", server_settings)
                tools = await registry.create_server().list_tools()
            assert len(tools) == count
            metadata = [tool.model_dump(mode="json", by_alias=True, exclude_none=True, exclude={"output_schema"}) for tool in tools]
            # The frozen SHA and historical byte baseline were measured on
            # Windows. POSIX registrars expose different platform-specific
            # metadata, including in coding profiles, despite stable names.
            if os.name == "nt":
                assert hashlib.sha256(encode(metadata)).hexdigest() == metadata_sha
            catalogue = [tool.model_dump(mode="json", by_alias=True, exclude_none=True) for tool in tools]
            if os.name == "nt":
                # The frozen baseline includes historical generic output schemas.
                added_bytes = len(encode(catalogue)) - before_bytes
                assert 0 < added_bytes <= 32284, (profile, added_bytes)
            else:
                # No equivalent frozen POSIX historical baseline exists. Bound
                # absolute schema overhead instead of comparing incompatible OS
                # catalogues, while checking required schemas and names below.
                schema_overhead = len(encode(catalogue)) - len(encode(metadata))
                assert 0 < schema_overhead <= 50000, (profile, schema_overhead)
            expected = names - ({"workflow_status"} if profile == "coding_core" else set())
            assert expected <= {tool.name for tool in tools}
            for tool in tools:
                if tool.name in expected:
                    assert tool.output_schema is not None
                    if tool.name in schemas:
                        assert schemas[tool.name] == tool.output_schema
                    schemas[tool.name] = tool.output_schema

    asyncio.run(scenario())
