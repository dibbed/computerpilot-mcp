"""Common diagnostic schema, normalization, and deterministic merging."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class Diagnostic(TypedDict, total=False):
    file: str
    line: int
    column: int
    end_line: int
    end_column: int
    severity: Literal["error", "warning", "note"]
    code: str
    message: str
    source: str


def normalize_diagnostic(value: dict[str, Any], *, source: str) -> Diagnostic:
    severity = value.get("severity", "error")
    if isinstance(severity, int):
        severity = {1: "error", 2: "warning", 3: "note", 4: "note"}.get(severity, "note")
    if severity not in {"error", "warning", "note"}:
        severity = "note"
    result: Diagnostic = {
        "severity": severity,
        "message": str(value.get("message", "")),
        "source": str(value.get("source") or source),
    }
    if value.get("file") is not None:
        result["file"] = str(value["file"])
    if value.get("code") is not None:
        result["code"] = str(value["code"])
    if value.get("line") is not None:
        result["line"] = int(value["line"])
    if value.get("column") is not None:
        result["column"] = int(value["column"])
    if value.get("end_line") is not None:
        result["end_line"] = int(value["end_line"])
    if value.get("end_column") is not None:
        result["end_column"] = int(value["end_column"])
    return result


def merge_diagnostics(groups: list[list[dict[str, Any]]], *, cap: int = 200) -> tuple[list[Diagnostic], bool]:
    unique: dict[tuple[object, ...], Diagnostic] = {}
    for group in groups:
        for raw in group:
            item = normalize_diagnostic(raw, source=str(raw.get("source") or "unknown"))
            key = tuple(item.get(field) for field in ("file", "line", "column", "severity", "code", "message", "source"))
            unique.setdefault(key, item)
    severity_order = {"error": 0, "warning": 1, "note": 2}
    ordered = sorted(
        unique.values(),
        key=lambda item: (
            str(item.get("file", "")).casefold(),
            int(item.get("line", 0)),
            int(item.get("column", 0)),
            severity_order[item["severity"]],
            item["source"],
            item["message"],
        ),
    )
    return ordered[:cap], len(ordered) > cap
