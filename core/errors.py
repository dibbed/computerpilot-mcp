"""Compact, actionable tool errors."""

from __future__ import annotations


class ToolError(RuntimeError):
    """Expected tool failure that is safe to show to the calling agent."""

    def __init__(self, code: str, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def describe_error(exc: Exception) -> tuple[str, str, str | None]:
    """Map exceptions to stable, compact error fields."""

    if isinstance(exc, ToolError):
        return exc.code, exc.message, exc.hint
    if isinstance(exc, FileNotFoundError):
        return "not_found", str(exc), "Check the path and current working directory."
    if isinstance(exc, FileExistsError):
        return "already_exists", str(exc), "Use overwrite=true only when replacement is intended."
    if isinstance(exc, PermissionError):
        return "permission_denied", str(exc), "Run the server with the Windows privileges required by the target."
    if isinstance(exc, TimeoutError):
        return "timeout", str(exc), "Increase timeout_sec or narrow the operation."
    return type(exc).__name__.lower(), str(exc) or type(exc).__name__, None
