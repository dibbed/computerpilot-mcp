"""Structured response helpers with optional text limiting."""

from __future__ import annotations

import hashlib
from typing import Any, Literal


def ok(**data: Any) -> dict[str, Any]:
    return {"ok": True, **data}


def failure(operation: str, exc: Exception) -> dict[str, Any]:
    from core.errors import describe_error

    code, message, hint = describe_error(exc)
    result: dict[str, Any] = {
        "ok": False,
        "operation": operation,
        "error": code,
        "message": message,
    }
    if hint:
        result["hint"] = hint
    return result


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bounded_text(text: str, limit: int | None, mode: Literal["head", "tail", "both"] = "tail") -> dict[str, Any]:
    """Return full text by default or an explicitly limited view with metadata."""

    original = len(text)
    if limit is None or original <= limit:
        return {"text": text, "chars": original, "total_chars": original, "truncated": False}
    if mode == "head":
        value = text[:limit]
    elif mode == "tail":
        value = text[-limit:]
    else:
        marker = "\n...<truncated>...\n"
        if limit <= len(marker):
            value = marker[:limit]
        else:
            content_limit = limit - len(marker)
            head = content_limit // 2
            tail = content_limit - head
            value = text[:head] + marker + text[-tail:]
    return {"text": value, "chars": len(value), "total_chars": original, "truncated": True}


def page(items: list[Any], *, total: int, offset: int, limit: int) -> dict[str, Any]:
    next_offset = offset + len(items)
    has_more = next_offset < total
    return {
        "items": items,
        "count": len(items),
        "total_count": total,
        "offset": offset,
        "has_more": has_more,
        "next_offset": next_offset if has_more else None,
        "truncated": has_more,
    }
