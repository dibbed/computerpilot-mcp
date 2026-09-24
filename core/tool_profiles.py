"""Named tool-domain profiles and compact tool-selection guidance."""

from __future__ import annotations

from dataclasses import dataclass

from core.errors import ToolError

ALL_DOMAINS = (
    "filesystem", "terminal", "process", "system", "windows", "project", "language", "testing", "git",
    "browser", "desktop", "memory", "jobs", "recovery", "workflows",
)

PROFILE_DOMAINS: dict[str, tuple[str, ...]] = {
    "minimal": ("filesystem", "project", "recovery"),
    "coding": ("filesystem", "terminal", "process", "system", "project", "language", "testing", "git", "memory", "jobs", "recovery", "workflows"),
    "git": ("filesystem", "project", "testing", "git", "recovery", "workflows"),
    "testing": ("terminal", "project", "language", "testing", "recovery", "workflows"),
    "desktop": ("process", "system", "windows", "desktop", "recovery", "workflows"),
    "browser": ("terminal", "browser", "recovery", "workflows"),
    "operations": ("terminal", "process", "system", "windows", "jobs", "recovery", "workflows"),
    "full": ALL_DOMAINS,
}

PREFERRED_USE: dict[str, str] = {
    "apply_patch": "Use for transactional multi-file unified-diff edits.",
    "replace_exact": "Use for one bounded exact replacement with an expected match count.",
    "write_file": "Use only for an intentional full-file replacement.",
    "rename_symbol": "Use semantic rename instead of text replacement when a language server supports it.",
    "run_process": "Use for a bounded foreground process with captured output.",
    "run_background": "Use for a long-running process that needs a managed session.",
    "submit_job": "Use for durable execution that must survive MCP runtime restarts.",
    "affected_tests": "Use to identify a conservative focused test set for changed code.",
    "verify_changes": "Use for change-aware syntax, Ruff, mypy, and pytest verification.",
    "ui_invoke": "Use a semantic locator for UI buttons instead of screen coordinates.",
    "mouse_click": "Use coordinates only when semantic UI Automation is unavailable.",
}


@dataclass(frozen=True, slots=True)
class ToolProfile:
    name: str
    domains: tuple[str, ...]


def resolve_profile(name: str | None) -> ToolProfile:
    normalized = (name or "full").strip().casefold()
    domains = PROFILE_DOMAINS.get(normalized)
    if domains is None:
        raise ToolError(
            "invalid_tool_profile",
            f"Unknown MCP tool profile {name!r}.",
            hint=f"Use one of: {', '.join(PROFILE_DOMAINS)}.",
        )
    return ToolProfile(normalized, domains)

