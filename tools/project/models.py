"""Typed wire models for bounded project summaries."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from core.response_models import SuccessWireModel


class LanguageCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=128)]
    files: Annotated[StrictInt, Field(ge=0, le=500_000)]


class TopLevelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=32_767)]
    type: Literal["directory", "file"]


class ProjectSummaryResult(SuccessWireModel):
    """Wire contract for project_summary with its existing bounded list sizes."""

    success_required_fields = frozenset({
        "path", "files_scanned", "scan_truncated", "languages", "frameworks",
        "dependency_count", "dependencies", "dependencies_truncated", "entry_points",
        "databases", "test_paths", "markers", "top_level", "top_level_truncated",
    })

    path: Annotated[str, Field(min_length=1, max_length=32_767)] | None = None
    files_scanned: Annotated[StrictInt, Field(ge=0, le=500_000)] | None = None
    scan_truncated: StrictBool | None = None
    languages: Annotated[list[LanguageCount], Field(max_length=8)] | None = None
    frameworks: Annotated[list[str], Field(max_length=15)] | None = None
    dependency_count: Annotated[StrictInt, Field(ge=0, le=1_000_000)] | None = None
    dependencies: Annotated[list[str], Field(max_length=30)] | None = None
    dependencies_truncated: StrictBool | None = None
    entry_points: Annotated[list[str], Field(max_length=20)] | None = None
    databases: Annotated[list[str], Field(max_length=100)] | None = None
    test_paths: Annotated[list[str], Field(max_length=15)] | None = None
    markers: Annotated[list[str], Field(max_length=20)] | None = None
    top_level: Annotated[list[TopLevelEntry], Field(max_length=30)] | None = None
    top_level_truncated: StrictBool | None = None

    @model_validator(mode="after")
    def _validate_summary_counts(self) -> ProjectSummaryResult:
        if not self.ok:
            return self
        if self.dependency_count is not None and self.dependencies is not None:
            if self.dependency_count < len(self.dependencies):
                raise ValueError("dependency_count cannot be smaller than the returned dependencies.")
        if self.languages is not None and self.files_scanned is not None:
            if sum(item.files for item in self.languages) > self.files_scanned:
                raise ValueError("Language counts cannot exceed the scanned file total.")
        return self
