"""Named tool-domain profiles and compact tool-selection guidance."""

from __future__ import annotations

from dataclasses import dataclass

from core.errors import ToolError

ALL_DOMAINS = (
    "filesystem", "terminal", "process", "system", "windows", "project", "language", "testing", "git",
    "browser", "desktop", "documents", "memory", "jobs", "recovery", "workflows",
)

PROFILE_DOMAINS: dict[str, tuple[str, ...]] = {
    "minimal": ("filesystem", "project", "recovery"),
    "coding": (
        "filesystem",
        "terminal",
        "process",
        "system",
        "project",
        "language",
        "testing",
        "git",
        "memory",
        "jobs",
        "recovery",
        "workflows",
    ),
    "git": ("filesystem", "project", "testing", "git", "recovery", "workflows"),
    "testing": ("terminal", "project", "language", "testing", "recovery", "workflows"),
    "desktop": ("process", "system", "windows", "desktop", "recovery", "workflows"),
    "documents": ("filesystem", "documents", "recovery", "workflows"),
    "browser": ("terminal", "browser", "recovery", "workflows"),
    "operations": ("terminal", "process", "system", "windows", "jobs", "recovery", "workflows"),
    "full": ALL_DOMAINS,
}
PROFILE_DOMAINS["coding_core"] = PROFILE_DOMAINS["coding"]
PROFILE_DOMAINS["read_batch"] = ("filesystem",)

READ_BATCH_TOOLS = frozenset({
    "native_read_batch", "read_file", "server_health", "discover_tool_domains", "recommend_tools",
    "execution_candidates", "execution_recommend",
})

CODING_CORE_TOOLS = frozenset("""
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

PREFERRED_USE: dict[str, str] = {
    "apply_patch": "Use for transactional multi-file unified-diff edits.",
    "replace_exact": "Use for one bounded exact replacement with an expected match count.",
    "write_file": "Use only for an intentional full-file replacement.",
    "rename_symbol": "Use semantic rename instead of text replacement when a language server supports it.",
    "run_process": "Use for a bounded foreground process with captured output.",
    "run_shell": "Use for intentional POSIX shell expansion on Linux/macOS; prefer run_process otherwise.",
    "run_cmd": "Use for intentional Windows CMD expansion; prefer run_process otherwise.",
    "run_background": "Use for a long-running process that needs a managed session.",
    "submit_job": "Use for durable execution that must survive MCP runtime restarts.",
    "affected_tests": "Use to identify a conservative focused test set for changed code.",
    "verify_changes": "Use for change-aware syntax, Ruff, mypy, and pytest verification.",
    "start_verification": "Start long verification, continue independent work, then collect the fresh result with job_wait/job_output.",
    "ui_invoke": "Use a semantic locator for UI buttons instead of screen coordinates.",
    "mouse_click": "Use coordinates only when semantic UI Automation is unavailable.",
}


@dataclass(frozen=True, slots=True)
class ToolProfile:
    name: str
    domains: tuple[str, ...]
    native_allowlist: frozenset[str] | None = None


def resolve_profile(name: str | None) -> ToolProfile:
    normalized = (name or "full").strip().casefold()
    domains = PROFILE_DOMAINS.get(normalized)
    if domains is None:
        raise ToolError(
            "invalid_tool_profile",
            f"Unknown MCP tool profile {name!r}.",
            hint=f"Use one of: {', '.join(PROFILE_DOMAINS)}.",
        )
    allowlist = {"coding_core": CODING_CORE_TOOLS, "read_batch": READ_BATCH_TOOLS}.get(normalized)
    return ToolProfile(normalized, domains, allowlist)

