from __future__ import annotations

import importlib
from dataclasses import FrozenInstanceError
from types import ModuleType

import pytest

from core.errors import ToolError


def _modules() -> tuple[ModuleType, ModuleType]:
    return importlib.import_module("core.execution_models"), importlib.import_module("core.tool_catalog")


def test_execution_models_have_stable_values_and_normalize_intent() -> None:
    models, _ = _modules()

    assert [item.value for item in models.RouteClass] == [
        "native.filesystem",
        "native.code",
        "native.git",
        "native.process",
        "native.system",
        "native.excel",
        "native.docx",
        "native.pdf",
        "semantic.browser",
        "semantic.windows_uia",
        "visual.desktop",
        "raw.desktop",
    ]
    assert [item.value for item in models.RiskClass] == ["low", "medium", "high"]
    assert [item.value for item in models.RecoverabilityClass] == ["weak", "medium", "strong"]

    intent = models.ExecutionIntent(
        "  FILESYSTEM.READ ",
        allow_raw_desktop=True,
        preferred_tool=" read_file ",
    )
    assert intent.name == "filesystem.read"
    assert intent.preferred_tool == "read_file"
    assert intent.as_dict() == {
        "name": "filesystem.read",
        "destructive": False,
        "semantic_ambiguous": False,
        "semantic_stale": False,
        "requires_macro_preservation": False,
        "allow_raw_desktop": True,
        "preferred_tool": "read_file",
    }
    with pytest.raises(FrozenInstanceError):
        intent.name = "filesystem.write"


def test_candidate_and_decision_models_serialize_without_runtime_noise() -> None:
    models, _ = _modules()

    candidate = models.RouteCandidate(
        route=models.RouteClass.NATIVE_FILESYSTEM,
        representative_tool="read_file",
        determinism=5,
        risk=models.RiskClass.LOW,
        recoverability=models.RecoverabilityClass.STRONG,
        capability_confidence=5,
        cost_tier=1,
        latency_tier=1,
        fallback_level=0,
        capability_checks=("tool:read_file",),
        supported=True,
        reason="exact structured read",
    )
    assert candidate.as_dict() == {
        "route": "native.filesystem",
        "representative_tool": "read_file",
        "determinism": 5,
        "risk_class": "low",
        "recoverability": "strong",
        "capability_confidence": 5,
        "cost_tier": 1,
        "latency_tier": 1,
        "fallback_level": 0,
        "capability_checks": ["tool:read_file"],
        "supported": True,
        "reason": "exact structured read",
        "rejection_code": None,
        "preserves_macros": False,
    }

    decision = models.RouteDecision(
        intent="filesystem.read",
        selected_route=models.RouteClass.NATIVE_FILESYSTEM,
        representative_tool="read_file",
        candidate_count=2,
        selection_reason="highest deterministic valid route",
        fallback_level=0,
        capability_checks=("tool:read_file",),
        risk=models.RiskClass.LOW,
        router_policy_version="deterministic-v1",
    )
    assert decision.as_dict() == {
        "intent": "filesystem.read",
        "selected_route": "native.filesystem",
        "representative_tool": "read_file",
        "candidate_count": 2,
        "selection_reason": "highest deterministic valid route",
        "fallback_level": 0,
        "capability_checks": ["tool:read_file"],
        "risk_class": "low",
        "router_policy_version": "deterministic-v1",
    }


def test_shared_catalog_describes_representative_registered_tools() -> None:
    models, catalog = _modules()

    read = catalog.tool_execution_metadata("read_file")
    assert read is not None
    assert read.domain == "filesystem"
    assert read.route is models.RouteClass.NATIVE_FILESYSTEM
    assert read.determinism == 5
    assert read.risk is models.RiskClass.LOW
    assert read.recoverability is models.RecoverabilityClass.STRONG

    assert catalog.route_for_tool("rename_symbol") == "native.code"
    assert catalog.route_for_tool("git_commit") == "native.git"
    assert catalog.route_for_tool("excel_write_range") == "native.excel"
    assert catalog.route_for_tool("docx_replace_text") == "native.docx"
    assert catalog.route_for_tool("pdf_extract_text") == "native.pdf"
    assert catalog.route_for_tool("browser_click_semantic") == "semantic.browser"
    assert catalog.route_for_tool("ui_set_value") == "semantic.windows_uia"
    assert catalog.route_for_tool("mouse_click") == "raw.desktop"
    assert catalog.tool_execution_metadata("definitely_missing") is None
    assert catalog.route_for_tool("definitely_missing") is None


def test_excel_write_rules_include_native_then_semantic_then_raw_paths() -> None:
    _, catalog = _modules()

    rules = catalog.intent_route_rules("document.excel.write")
    assert [rule.route.value for rule in rules] == [
        "native.excel",
        "semantic.windows_uia",
        "raw.desktop",
    ]
    assert [rule.representative_tool for rule in rules] == [
        "excel_write_range",
        "ui_set_value",
        "keyboard_type",
    ]
    assert rules[0].preserves_macros is True
    assert rules[1].preserves_macros is False
    assert rules[2].preserves_macros is False
    assert rules[0].fallback_level == 0
    assert rules[1].fallback_level == 1
    assert rules[2].fallback_level == 2


def test_catalog_has_explicit_rules_for_every_initial_intent() -> None:
    _, catalog = _modules()

    intents = {
        "filesystem.read",
        "filesystem.write",
        "code.rename",
        "git.read",
        "git.write",
        "process.run",
        "system.inspect",
        "document.excel.read",
        "document.excel.write",
        "document.docx.read",
        "document.docx.write",
        "document.pdf.read",
        "document.pdf.create",
        "browser.interact",
        "desktop.semantic.interact",
        "desktop.raw.interact",
    }
    for intent in sorted(intents):
        rules = catalog.intent_route_rules(intent)
        assert rules
        assert all(rule.intent == intent for rule in rules)

    with pytest.raises(ToolError) as exc:
        catalog.intent_route_rules("unknown.intent")
    assert exc.value.code == "unknown_execution_intent"
