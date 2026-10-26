"""Typed wire models for affected-test selection and verification outputs."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from core.response_models import NativeFailure, SuccessWireModel, WireRootModel

Decision = Literal["focused", "full_suite", "focused_plus_full_recommended", "none"]


class TestSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Annotated[str, Field(min_length=1, max_length=32_767)]
    node_ids: Annotated[list[str], Field(max_length=1_000)]
    evidence: Annotated[list[str], Field(min_length=1, max_length=100)]
    dependency_depth: Annotated[StrictInt, Field(ge=0, le=20)]


class PackageSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_root: Annotated[str, Field(min_length=1, max_length=32_767)]
    runner: Annotated[str, Field(min_length=1, max_length=128)]
    tests: Annotated[list[str], Field(max_length=5_000)]
    evidence: Annotated[list[str], Field(max_length=1_000)]
    confidence: Annotated[str, Field(min_length=1, max_length=128)]
    complete: StrictBool
    fallback_reason: Annotated[str, Field(max_length=500)] | None = None
    full_suite: StrictBool
    available: StrictBool


class AffectedTestParseIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: Annotated[str, Field(max_length=32_767)]
    error: Annotated[str, Field(max_length=32_767)]


class AffectedTestsResult(SuccessWireModel):
    """Wire contract for affected_tests; counts and optional absence stay exact."""

    nullable_success_fields = frozenset({"base", "base_sha", "head_sha", "worktree_id", "source_fingerprint"})

    success_required_fields = frozenset({
        "repo", "base", "changed_paths", "changed_count", "touched_paths", "tests",
        "test_count", "decision", "full_suite_reasons", "modules_traversed", "files_scanned",
        "scan_truncated", "results_truncated", "parse_errors", "complete", "test_backend",
        "package_selections", "unknown_coverage", "missing_inputs", "base_sha", "head_sha",
        "worktree_id", "source_fingerprint", "total", "total_exact",
    })

    repo: Annotated[str, Field(min_length=1, max_length=32_767)] | None = None
    base: Annotated[str, Field(max_length=500)] | None = None
    changed_paths: Annotated[list[str], Field(max_length=1_000)] | None = None
    changed_count: Annotated[StrictInt, Field(ge=0, le=1_000)] | None = None
    touched_paths: Annotated[list[str], Field(max_length=2_000)] | None = None
    tests: Annotated[list[TestSelection], Field(max_length=5_000)] | None = None
    test_count: Annotated[StrictInt, Field(ge=0, le=5_000)] | None = None
    decision: Decision | None = None
    full_suite_reasons: Annotated[list[str], Field(max_length=100)] | None = None
    modules_traversed: Annotated[StrictInt, Field(ge=0, le=1_000_000)] | None = None
    files_scanned: Annotated[StrictInt, Field(ge=0, le=50_000)] | None = None
    scan_truncated: StrictBool | None = None
    results_truncated: StrictBool | None = None
    parse_errors: Annotated[list[AffectedTestParseIssue], Field(max_length=20)] | None = None
    complete: StrictBool | None = None
    test_backend: Literal["python", "auto"] | None = None
    package_selections: Annotated[list[PackageSelection], Field(max_length=5_000)] | None = None
    unknown_coverage: StrictBool | None = None
    missing_inputs: Annotated[list[str], Field(max_length=1_000)] | None = None
    base_sha: Annotated[str, Field(max_length=128)] | None = None
    head_sha: Annotated[str, Field(max_length=128)] | None = None
    worktree_id: Annotated[str, Field(max_length=128)] | None = None
    source_fingerprint: Annotated[str, Field(max_length=128)] | None = None
    total: Annotated[StrictInt, Field(ge=0, le=10_000_000)] | None = None
    total_exact: StrictBool | None = None

    @model_validator(mode="after")
    def _validate_selection_counts(self) -> AffectedTestsResult:
        if not self.ok:
            return self
        if self.changed_paths is not None and self.changed_count != len(self.changed_paths):
            raise ValueError("changed_count must match the returned changed paths.")
        if self.tests is not None and self.test_count != len(self.tests):
            raise ValueError("test_count must match the returned test selections.")
        selected = (self.test_count or 0) + len(self.package_selections or ())
        if self.total is not None and self.total < selected:
            raise ValueError("total cannot be smaller than the returned selections.")
        if self.total_exact and self.total is not None and self.total < (self.test_count or 0):
            raise ValueError("An exact total cannot be smaller than the returned test count.")
        if self.unknown_coverage and self.complete:
            raise ValueError("Unknown coverage cannot claim completeness.")
        return self


class VerificationStage(BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_extra={
        "if": {"properties": {"status": {"const": "skipped"}}},
        "then": {"required": ["reason"], "properties": {"reason": {"type": "string", "minLength": 1}}},
    })

    name: str
    status: Literal["passed", "failed", "skipped", "timed_out", "unavailable"]
    duration_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    targets: list[str]
    reason: str | None = None
    exit_code: StrictInt | None = None
    summary: str | None = None
    required_unknown: StrictBool | None = None
    execution_uncertain: StrictBool | None = None
    tests_executed: StrictBool | None = None

    @model_validator(mode="after")
    def _require_skip_reason(self) -> VerificationStage:
        if self.status == "skipped" and not self.reason:
            raise ValueError("Skipped verification stages require a reason.")
        return self


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: StrictBool
    mode: Literal["full", "focused", "checks_only"]
    stages: list[VerificationStage]
    required_unknown: StrictBool
    duration_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    changed_paths: list[str]
    affected: dict[str, object]
    execution_uncertain: StrictBool
    tests_executed: StrictBool
    claim_testing: StrictBool

    @model_validator(mode="after")
    def _validate_verification_outcome(self) -> VerificationReport:
        unknown = any(stage.status in {"timed_out", "unavailable"} or stage.required_unknown for stage in self.stages)
        if unknown and not self.required_unknown:
            raise ValueError("Unknown stages require an unknown verification outcome.")
        passed = all(stage.status in {"passed", "skipped"} for stage in self.stages) and not self.required_unknown
        if self.ok != passed or (self.ok and self.execution_uncertain):
            raise ValueError("Failed or unknown verification cannot claim success.")
        if self.claim_testing != self.tests_executed:
            raise ValueError("Testing claims require actual test execution.")
        return self


class VerificationResult(WireRootModel[VerificationReport | NativeFailure]):
    pass
