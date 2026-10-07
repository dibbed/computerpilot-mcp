"""Shared execution metadata used by discovery, routing, and workflows."""

from __future__ import annotations

from dataclasses import dataclass

from core.errors import ToolError
from core.execution_models import RecoverabilityClass, RiskClass, RouteClass


@dataclass(frozen=True, slots=True)
class ToolExecutionMetadata:
    name: str
    domain: str
    route: RouteClass
    determinism: int
    risk: RiskClass
    recoverability: RecoverabilityClass
    preserves_macros: bool = False


@dataclass(frozen=True, slots=True)
class IntentRouteRule:
    intent: str
    route: RouteClass
    representative_tool: str
    determinism: int
    risk: RiskClass
    recoverability: RecoverabilityClass
    capability_confidence: int
    cost_tier: int
    latency_tier: int
    fallback_level: int
    required_capabilities: tuple[str, ...] = ()
    preserves_macros: bool = False


def _meta(
    name: str,
    domain: str,
    route: RouteClass,
    determinism: int,
    risk: RiskClass,
    recoverability: RecoverabilityClass,
    *,
    preserves_macros: bool = False,
) -> ToolExecutionMetadata:
    return ToolExecutionMetadata(
        name,
        domain,
        route,
        determinism,
        risk,
        recoverability,
        preserves_macros,
    )


TOOL_EXECUTION_METADATA: dict[str, ToolExecutionMetadata] = {
    # Filesystem
    "read_file": _meta("read_file", "filesystem", RouteClass.NATIVE_FILESYSTEM, 5, RiskClass.LOW, RecoverabilityClass.STRONG),
    "write_file": _meta("write_file", "filesystem", RouteClass.NATIVE_FILESYSTEM, 5, RiskClass.MEDIUM, RecoverabilityClass.STRONG),
    "create_file": _meta("create_file", "filesystem", RouteClass.NATIVE_FILESYSTEM, 5, RiskClass.MEDIUM, RecoverabilityClass.STRONG),
    "delete_file": _meta("delete_file", "filesystem", RouteClass.NATIVE_FILESYSTEM, 5, RiskClass.HIGH, RecoverabilityClass.STRONG),
    "replace_exact": _meta("replace_exact", "filesystem", RouteClass.NATIVE_FILESYSTEM, 5, RiskClass.MEDIUM, RecoverabilityClass.STRONG),
    "apply_patch": _meta("apply_patch", "filesystem", RouteClass.NATIVE_FILESYSTEM, 5, RiskClass.MEDIUM, RecoverabilityClass.STRONG),
    # Code / LSP
    "code_context": _meta("code_context", "project", RouteClass.NATIVE_CODE, 5, RiskClass.LOW, RecoverabilityClass.STRONG),
    "rename_symbol": _meta("rename_symbol", "language", RouteClass.NATIVE_CODE, 5, RiskClass.MEDIUM, RecoverabilityClass.STRONG),
    "apply_code_action": _meta("apply_code_action", "language", RouteClass.NATIVE_CODE, 5, RiskClass.MEDIUM, RecoverabilityClass.STRONG),
    "affected_tests": _meta("affected_tests", "testing", RouteClass.NATIVE_CODE, 5, RiskClass.LOW, RecoverabilityClass.STRONG),
    "verify_changes": _meta("verify_changes", "testing", RouteClass.NATIVE_CODE, 5, RiskClass.LOW, RecoverabilityClass.STRONG),
    # Git
    "git_status": _meta("git_status", "git", RouteClass.NATIVE_GIT, 5, RiskClass.LOW, RecoverabilityClass.STRONG),
    "git_diff": _meta("git_diff", "git", RouteClass.NATIVE_GIT, 5, RiskClass.LOW, RecoverabilityClass.STRONG),
    "git_stage": _meta("git_stage", "git", RouteClass.NATIVE_GIT, 5, RiskClass.MEDIUM, RecoverabilityClass.STRONG),
    "git_commit": _meta("git_commit", "git", RouteClass.NATIVE_GIT, 5, RiskClass.MEDIUM, RecoverabilityClass.STRONG),
    # Process/system
    "run_process": _meta("run_process", "process", RouteClass.NATIVE_PROCESS, 5, RiskClass.MEDIUM, RecoverabilityClass.MEDIUM),
    "submit_job": _meta("submit_job", "jobs", RouteClass.NATIVE_PROCESS, 5, RiskClass.MEDIUM, RecoverabilityClass.STRONG),
    "system_info": _meta("system_info", "system", RouteClass.NATIVE_SYSTEM, 5, RiskClass.LOW, RecoverabilityClass.STRONG),
    "installed_software": _meta("installed_software", "system", RouteClass.NATIVE_SYSTEM, 5, RiskClass.LOW, RecoverabilityClass.STRONG),
    # Excel
    "excel_inspect": _meta(
        "excel_inspect", "documents", RouteClass.NATIVE_EXCEL, 5, RiskClass.LOW, RecoverabilityClass.STRONG, preserves_macros=True
    ),
    "excel_read_range": _meta(
        "excel_read_range", "documents", RouteClass.NATIVE_EXCEL, 5, RiskClass.LOW, RecoverabilityClass.STRONG, preserves_macros=True
    ),
    "excel_find": _meta(
        "excel_find", "documents", RouteClass.NATIVE_EXCEL, 5, RiskClass.LOW, RecoverabilityClass.STRONG, preserves_macros=True
    ),
    "excel_write_range": _meta(
        "excel_write_range", "documents", RouteClass.NATIVE_EXCEL, 5, RiskClass.MEDIUM, RecoverabilityClass.STRONG, preserves_macros=True
    ),
    "excel_clear_range": _meta(
        "excel_clear_range", "documents", RouteClass.NATIVE_EXCEL, 5, RiskClass.MEDIUM, RecoverabilityClass.STRONG, preserves_macros=True
    ),
    "excel_set_formula": _meta(
        "excel_set_formula", "documents", RouteClass.NATIVE_EXCEL, 5, RiskClass.MEDIUM, RecoverabilityClass.STRONG, preserves_macros=True
    ),
    # DOCX
    "docx_inspect": _meta("docx_inspect", "documents", RouteClass.NATIVE_DOCX, 5, RiskClass.LOW, RecoverabilityClass.STRONG),
    "docx_read": _meta("docx_read", "documents", RouteClass.NATIVE_DOCX, 5, RiskClass.LOW, RecoverabilityClass.STRONG),
    "docx_find": _meta("docx_find", "documents", RouteClass.NATIVE_DOCX, 5, RiskClass.LOW, RecoverabilityClass.STRONG),
    "docx_replace_text": _meta("docx_replace_text", "documents", RouteClass.NATIVE_DOCX, 5, RiskClass.MEDIUM, RecoverabilityClass.STRONG),
    "docx_insert_paragraph": _meta(
        "docx_insert_paragraph", "documents", RouteClass.NATIVE_DOCX, 5, RiskClass.MEDIUM, RecoverabilityClass.STRONG
    ),
    # PDF
    "pdf_inspect": _meta("pdf_inspect", "documents", RouteClass.NATIVE_PDF, 5, RiskClass.LOW, RecoverabilityClass.STRONG),
    "pdf_extract_text": _meta("pdf_extract_text", "documents", RouteClass.NATIVE_PDF, 5, RiskClass.LOW, RecoverabilityClass.STRONG),
    "pdf_create_from_text": _meta(
        "pdf_create_from_text", "documents", RouteClass.NATIVE_PDF, 5, RiskClass.MEDIUM, RecoverabilityClass.STRONG
    ),
    "pdf_create_from_markdown": _meta(
        "pdf_create_from_markdown", "documents", RouteClass.NATIVE_PDF, 5, RiskClass.MEDIUM, RecoverabilityClass.STRONG
    ),
    # Semantic browser
    "browser_click_semantic": _meta(
        "browser_click_semantic", "browser", RouteClass.SEMANTIC_BROWSER, 4, RiskClass.MEDIUM, RecoverabilityClass.MEDIUM
    ),
    "browser_fill_semantic": _meta(
        "browser_fill_semantic", "browser", RouteClass.SEMANTIC_BROWSER, 4, RiskClass.MEDIUM, RecoverabilityClass.MEDIUM
    ),
    "browser_click": _meta("browser_click", "browser", RouteClass.SEMANTIC_BROWSER, 4, RiskClass.MEDIUM, RecoverabilityClass.MEDIUM),
    "browser_fill": _meta("browser_fill", "browser", RouteClass.SEMANTIC_BROWSER, 4, RiskClass.MEDIUM, RecoverabilityClass.MEDIUM),
    # Windows semantic UIA
    "ui_invoke": _meta("ui_invoke", "windows", RouteClass.SEMANTIC_WINDOWS_UIA, 4, RiskClass.MEDIUM, RecoverabilityClass.MEDIUM),
    "ui_set_value": _meta("ui_set_value", "windows", RouteClass.SEMANTIC_WINDOWS_UIA, 4, RiskClass.MEDIUM, RecoverabilityClass.MEDIUM),
    "ui_select": _meta("ui_select", "windows", RouteClass.SEMANTIC_WINDOWS_UIA, 4, RiskClass.MEDIUM, RecoverabilityClass.MEDIUM),
    # Raw desktop
    "mouse_click": _meta("mouse_click", "desktop", RouteClass.RAW_DESKTOP, 1, RiskClass.MEDIUM, RecoverabilityClass.WEAK),
    "keyboard_type": _meta("keyboard_type", "desktop", RouteClass.RAW_DESKTOP, 1, RiskClass.MEDIUM, RecoverabilityClass.WEAK),
    "hotkey": _meta("hotkey", "desktop", RouteClass.RAW_DESKTOP, 1, RiskClass.MEDIUM, RecoverabilityClass.WEAK),
}


def _rule(
    intent: str,
    route: RouteClass,
    tool: str,
    determinism: int,
    risk: RiskClass,
    recoverability: RecoverabilityClass,
    confidence: int,
    cost: int,
    latency: int,
    fallback: int,
    *,
    required_capabilities: tuple[str, ...] = (),
    preserves_macros: bool = False,
) -> IntentRouteRule:
    return IntentRouteRule(
        intent,
        route,
        tool,
        determinism,
        risk,
        recoverability,
        confidence,
        cost,
        latency,
        fallback,
        required_capabilities,
        preserves_macros,
    )


def _native_semantic_raw(
    intent: str,
    native_route: RouteClass,
    native_tool: str,
    risk: RiskClass,
    *,
    preserves_macros: bool = False,
) -> tuple[IntentRouteRule, ...]:
    return (
        _rule(
            intent,
            native_route,
            native_tool,
            5,
            risk,
            RecoverabilityClass.STRONG,
            5,
            1,
            1,
            0,
            preserves_macros=preserves_macros,
        ),
        _rule(
            intent,
            RouteClass.SEMANTIC_WINDOWS_UIA,
            "ui_set_value" if risk is not RiskClass.LOW else "ui_invoke",
            4,
            risk,
            RecoverabilityClass.MEDIUM,
            4,
            3,
            3,
            1,
            required_capabilities=("semantic_ui",),
        ),
        _rule(
            intent,
            RouteClass.RAW_DESKTOP,
            "keyboard_type" if risk is not RiskClass.LOW else "mouse_click",
            1,
            risk,
            RecoverabilityClass.WEAK,
            2,
            5,
            5,
            2,
            required_capabilities=("desktop_input",),
        ),
    )


INTENT_ROUTE_RULES: dict[str, tuple[IntentRouteRule, ...]] = {
    "filesystem.read": _native_semantic_raw(
        "filesystem.read",
        RouteClass.NATIVE_FILESYSTEM,
        "read_file",
        RiskClass.LOW,
    ),
    "filesystem.write": _native_semantic_raw(
        "filesystem.write",
        RouteClass.NATIVE_FILESYSTEM,
        "replace_exact",
        RiskClass.MEDIUM,
    ),
    "code.rename": _native_semantic_raw(
        "code.rename",
        RouteClass.NATIVE_CODE,
        "rename_symbol",
        RiskClass.MEDIUM,
    ),
    "git.read": (
        _rule("git.read", RouteClass.NATIVE_GIT, "git_status", 5, RiskClass.LOW, RecoverabilityClass.STRONG, 5, 1, 1, 0),
        _rule("git.read", RouteClass.NATIVE_PROCESS, "run_process", 4, RiskClass.LOW, RecoverabilityClass.MEDIUM, 4, 2, 2, 1),
    ),
    "git.write": (
        _rule("git.write", RouteClass.NATIVE_GIT, "git_commit", 5, RiskClass.MEDIUM, RecoverabilityClass.STRONG, 5, 1, 1, 0),
        _rule("git.write", RouteClass.NATIVE_PROCESS, "run_process", 4, RiskClass.MEDIUM, RecoverabilityClass.MEDIUM, 4, 2, 2, 1),
        _rule(
            "git.write",
            RouteClass.RAW_DESKTOP,
            "keyboard_type",
            1,
            RiskClass.MEDIUM,
            RecoverabilityClass.WEAK,
            2,
            5,
            5,
            2,
            required_capabilities=("desktop_input",),
        ),
    ),
    "process.run": (
        _rule("process.run", RouteClass.NATIVE_PROCESS, "run_process", 5, RiskClass.MEDIUM, RecoverabilityClass.MEDIUM, 5, 1, 1, 0),
    ),
    "system.inspect": (
        _rule("system.inspect", RouteClass.NATIVE_SYSTEM, "system_info", 5, RiskClass.LOW, RecoverabilityClass.STRONG, 5, 1, 1, 0),
        _rule("system.inspect", RouteClass.NATIVE_PROCESS, "run_process", 4, RiskClass.LOW, RecoverabilityClass.MEDIUM, 4, 2, 2, 1),
    ),
    "document.excel.read": _native_semantic_raw(
        "document.excel.read",
        RouteClass.NATIVE_EXCEL,
        "excel_read_range",
        RiskClass.LOW,
        preserves_macros=True,
    ),
    "document.excel.write": _native_semantic_raw(
        "document.excel.write",
        RouteClass.NATIVE_EXCEL,
        "excel_write_range",
        RiskClass.MEDIUM,
        preserves_macros=True,
    ),
    "document.docx.read": _native_semantic_raw(
        "document.docx.read",
        RouteClass.NATIVE_DOCX,
        "docx_read",
        RiskClass.LOW,
    ),
    "document.docx.write": _native_semantic_raw(
        "document.docx.write",
        RouteClass.NATIVE_DOCX,
        "docx_replace_text",
        RiskClass.MEDIUM,
    ),
    "document.pdf.read": _native_semantic_raw(
        "document.pdf.read",
        RouteClass.NATIVE_PDF,
        "pdf_extract_text",
        RiskClass.LOW,
    ),
    "document.pdf.create": _native_semantic_raw(
        "document.pdf.create",
        RouteClass.NATIVE_PDF,
        "pdf_create_from_text",
        RiskClass.MEDIUM,
    ),
    "browser.interact": (
        _rule(
            "browser.interact",
            RouteClass.SEMANTIC_BROWSER,
            "browser_click_semantic",
            4,
            RiskClass.MEDIUM,
            RecoverabilityClass.MEDIUM,
            5,
            2,
            2,
            0,
            required_capabilities=("browser",),
        ),
        _rule(
            "browser.interact",
            RouteClass.RAW_DESKTOP,
            "mouse_click",
            1,
            RiskClass.MEDIUM,
            RecoverabilityClass.WEAK,
            2,
            5,
            5,
            1,
            required_capabilities=("desktop_input",),
        ),
    ),
    "desktop.semantic.interact": (
        _rule(
            "desktop.semantic.interact",
            RouteClass.SEMANTIC_WINDOWS_UIA,
            "ui_invoke",
            4,
            RiskClass.MEDIUM,
            RecoverabilityClass.MEDIUM,
            5,
            2,
            2,
            0,
            required_capabilities=("semantic_ui",),
        ),
        _rule(
            "desktop.semantic.interact",
            RouteClass.RAW_DESKTOP,
            "mouse_click",
            1,
            RiskClass.MEDIUM,
            RecoverabilityClass.WEAK,
            2,
            5,
            5,
            1,
            required_capabilities=("desktop_input",),
        ),
    ),
    "desktop.raw.interact": (
        _rule(
            "desktop.raw.interact",
            RouteClass.RAW_DESKTOP,
            "mouse_click",
            1,
            RiskClass.MEDIUM,
            RecoverabilityClass.WEAK,
            5,
            4,
            4,
            0,
            required_capabilities=("desktop_input",),
        ),
    ),
}


def tool_execution_metadata(name: str) -> ToolExecutionMetadata | None:
    return TOOL_EXECUTION_METADATA.get(name)


def route_for_tool(name: str) -> str | None:
    metadata = tool_execution_metadata(name)
    return metadata.route.value if metadata is not None else None


def intent_route_rules(intent: str) -> tuple[IntentRouteRule, ...]:
    normalized = intent.strip().casefold()
    rules = INTENT_ROUTE_RULES.get(normalized)
    if rules is None:
        raise ToolError(
            "unknown_execution_intent",
            f"Unknown execution intent {intent!r}.",
            hint=f"Use one of: {', '.join(sorted(INTENT_ROUTE_RULES))}.",
        )
    return rules
