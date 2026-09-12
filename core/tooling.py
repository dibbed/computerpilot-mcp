"""MCP annotations, schema aliases, and compact exception handling."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Annotated, Any, TypeVar

from mcp.types import ToolAnnotations
from pydantic import Field

from core.response import failure
from core.timings import tool_timing

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
MUTATING = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)
OPEN_WORLD_READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
OPEN_WORLD_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True)

PathArg = Annotated[str, Field(min_length=1, max_length=32_767, description="Absolute or server-root-relative path.")]
SmallText = Annotated[str, Field(max_length=100_000)]
OffsetArg = Annotated[int, Field(ge=0, le=10_000_000)]
LimitArg = Annotated[int, Field(ge=1, le=5_000)]
TimeoutArg = Annotated[float, Field(gt=0, le=3_600)]


F = TypeVar("F", bound=Callable[..., Any])


def compact_errors(operation: str) -> Callable[[F], F]:
    """Convert tool exceptions into small structured failures while preserving schemas."""

    def decorate(fn: F) -> F:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    with tool_timing(operation):
                        return await fn(*args, **kwargs)
                except Exception as exc:  # boundary: errors become MCP data
                    return failure(operation, exc)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                with tool_timing(operation):
                    return fn(*args, **kwargs)
            except Exception as exc:  # boundary: errors become MCP data
                return failure(operation, exc)

        return sync_wrapper  # type: ignore[return-value]

    return decorate
