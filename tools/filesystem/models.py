"""Typed wire models for filesystem search results."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from core.response_models import SuccessWireModel


class SearchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Annotated[str, Field(min_length=1, max_length=32_767)]
    matched_in: Annotated[list[Literal["name", "content"]], Field(min_length=1, max_length=2)]


class SearchFilesResult(SuccessWireModel):
    """One envelope for exact, streaming, snapshot, and cursor search variants."""

    nullable_success_fields = frozenset({"total_count", "next_offset", "cursor"})

    success_required_fields = frozenset({
        "root", "scan_truncated", "count_mode", "result_order", "items", "count",
        "total_count", "offset", "has_more", "next_offset", "cursor", "truncated",
    })

    root: Annotated[str, Field(min_length=1, max_length=32_767)] | None = None
    scan_truncated: StrictBool | None = None
    scanned_files: Annotated[StrictInt, Field(ge=0, le=500_000)] | None = None
    count_mode: Literal["exact", "none"] | None = None
    result_order: Literal["path", "traversal"] | None = None
    items: Annotated[list[SearchItem], Field(max_length=500)] | None = None
    count: Annotated[StrictInt, Field(ge=0, le=500)] | None = None
    total_count: Annotated[StrictInt, Field(ge=0, le=500_000)] | None = None
    offset: Annotated[StrictInt, Field(ge=0, le=1_000_000)] | None = None
    has_more: StrictBool | None = None
    next_offset: Annotated[StrictInt, Field(ge=0, le=1_000_000)] | None = None
    cursor: Annotated[str, Field(max_length=128)] | None = None
    truncated: StrictBool | None = None
    snapshot_id: Annotated[str, Field(max_length=64)] | None = None
    snapshot_expires_at: Annotated[str, Field(max_length=64)] | None = None
    from_snapshot: StrictBool | None = None
    snapshot_consistent: StrictBool | None = None
    snapshot_created: StrictBool | None = None

    @model_validator(mode="after")
    def _validate_search_page(self) -> SearchFilesResult:
        if not self.ok:
            return self
        if self.items is not None and self.count != len(self.items):
            raise ValueError("Search count must match the returned items.")
        if self.total_count is not None and self.count is not None and self.offset is not None:
            if self.count and self.total_count < self.count + self.offset:
                raise ValueError("Search total_count cannot be smaller than the returned page.")
        if self.has_more and self.next_offset is None and self.cursor is None and not self.scan_truncated:
            raise ValueError("A search page with more results requires next_offset or a cursor.")
        if (self.from_snapshot or self.snapshot_created) and self.snapshot_id is None:
            raise ValueError("Snapshot search output requires snapshot_id.")
        return self
