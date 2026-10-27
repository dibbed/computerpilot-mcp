"""Versioned internal evidence for project intelligence; existing AST rows stay unchanged."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from urllib.parse import unquote_to_bytes, urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from pydantic.dataclasses import dataclass

from core.response_models import SuccessWireModel

LanguageId = Literal["python", "typescript", "typescriptreact", "javascript", "javascriptreact", "php"]
RelationKind = Literal["definition", "reference", "import", "caller", "callee", "inheritance", "related_test"]
Version = Annotated[StrictInt, Field(ge=1, le=1)]
Position = Annotated[StrictInt, Field(ge=0, le=2**31 - 1)]
Digest = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
Label = Annotated[str, Field(strict=True, min_length=1, max_length=500)]
RelativeUri = Annotated[str, Field(strict=True, min_length=1, max_length=32_767)]


@dataclass(frozen=True, slots=True, config=ConfigDict(extra="forbid"))
class SymbolKey:
    canonical_workspace: Annotated[str, Field(strict=True, min_length=1, max_length=32_767)]
    language_id: LanguageId
    relative_uri: RelativeUri
    qualified_name: Label
    kind: Label
    start_line: Position
    start_character: Position | None
    content_digest: Digest
    coordinate_system: Literal["lsp_utf16", "python_legacy"] = "lsp_utf16"
    version: Version = 1

    @model_validator(mode="after")
    def validate_identity(self) -> SymbolKey:
        if not Path(self.canonical_workspace).is_absolute():
            raise ValueError("Workspace identity must be absolute")
        parsed = urlsplit(self.relative_uri)
        decoded = unquote_to_bytes(parsed.path).decode("utf-8", errors="strict")
        if (
            parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
            or re.search(r"%(?![0-9a-fA-F]{2})", self.relative_uri)
            or "\\" in decoded or "\x00" in decoded or ":" in decoded
            or PurePosixPath(decoded).is_absolute() or ".." in PurePosixPath(decoded).parts
            or not PurePosixPath(decoded).parts
        ):
            raise ValueError("Symbol URI must stay relative to its workspace")
        if self.start_character is None and self.coordinate_system != "python_legacy":
            raise ValueError("An unknown AST column cannot claim UTF-16 coordinates")
        return self


class CodePosition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    line: Annotated[StrictInt, Field(ge=0, le=2**31 - 1)]
    character: Annotated[StrictInt, Field(ge=0, le=2**31 - 1)]
    path: Annotated[str, Field(min_length=1, max_length=32_767)]


@dataclass(frozen=True, slots=True, config=ConfigDict(extra="forbid"))
class SnapshotEvidence:
    source_sha: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")] | None
    worktree_id: Annotated[str, Field(strict=True, min_length=1, max_length=32_767)]
    document_digest: Digest
    document_version: Position | None
    index_generation: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    provider_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)] | None
    source_kind: Literal["ast", "lsp"]
    version: Version = 1


class EvidenceEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    from_symbol: SymbolKey = Field(validation_alias="from", serialization_alias="from")
    to_symbol: SymbolKey = Field(validation_alias="to", serialization_alias="to")
    kind: RelationKind
    direction: Literal["incoming", "outgoing"]
    confidence: Literal["static_resolved", "semantic_reported", "inferred"]
    evidence: SnapshotEvidence
    depth: Annotated[StrictInt, Field(ge=0, le=2)]
    version: Version = 1


class ContextEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    language_id: LanguageId | Literal["unsupported"]
    backend: Literal["ast", "lsp", "unsupported"]
    definitions: Annotated[list[SymbolKey], Field(max_length=1_000)] = Field(default_factory=list)
    edges: Annotated[list[EvidenceEdge], Field(max_length=1_000)] = Field(default_factory=list)
    snapshot: SnapshotEvidence | None = None
    completeness_by_relation: Annotated[dict[RelationKind, StrictBool], Field(max_length=7)] = Field(default_factory=dict)
    missing_inputs: Annotated[list[Label], Field(max_length=1_000)] = Field(default_factory=list)
    fallback_reason: Label | None = None
    complete: StrictBool = False
    semantic_complete: StrictBool = False
    offset: Annotated[StrictInt, Field(ge=0)] = 0
    total: Annotated[StrictInt, Field(ge=0)] = 0
    total_exact: StrictBool = False
    truncated: StrictBool = False
    version: Version = 1


class ContextRow(BaseModel):
    """One bounded definition or relationship row from either AST or LSP evidence."""

    model_config = ConfigDict(extra="forbid")

    file: Annotated[str, Field(min_length=1, max_length=32_767)]
    line: Annotated[StrictInt, Field(ge=0, le=2**31 - 1)] | None = None
    evidence: Annotated[str, Field(min_length=1, max_length=128)]
    confidence: Annotated[str, Field(min_length=1, max_length=128)]
    source_kind: Annotated[str, Field(min_length=1, max_length=128)]
    module: Annotated[str, Field(max_length=32_767)] | None = None
    qualified_name: Annotated[str, Field(max_length=32_767)] | None = None
    kind: Annotated[str, Field(max_length=128)] | None = None
    end_line: Annotated[StrictInt, Field(ge=0, le=2**31 - 1)] | None = None
    signature: Annotated[str, Field(max_length=32_767)] | None = None
    source: Annotated[str, Field(max_length=200_000)] | None = None
    source_truncated: StrictBool | None = None
    depth: Annotated[StrictInt, Field(ge=0, le=2)] | None = None
    via_qualified_name: Annotated[str, Field(max_length=32_767)] | None = None
    call: Annotated[str, Field(max_length=32_767)] | None = None
    name: Annotated[str, Field(max_length=500)] | None = None
    column: Annotated[StrictInt, Field(ge=0, le=2**31 - 1)] | None = None
    local: Annotated[str, Field(max_length=500)] | None = None
    target: Annotated[str, Field(max_length=32_767)] | None = None
    direction: Literal["incoming", "outgoing"] | None = None
    coordinate_system: Literal["lsp_utf16", "python_legacy"] | None = None
    semantic_edge: EvidenceEdge | None = None
    symbol_key: SymbolKey | None = None
    snapshot: SnapshotEvidence | None = None


class ParseIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: Annotated[str, Field(max_length=32_767)]
    error: Annotated[str, Field(max_length=32_767)]


class CodeContextResult(SuccessWireModel):
    """Wire contract for code_context; optional fields stay absent, not null."""

    nullable_success_fields = frozenset({"snapshot", "fallback_reason"})

    success_required_fields = frozenset({
        "root", "symbol", "ambiguous", "definitions", "callers", "callees", "references",
        "imports", "tests", "files_scanned", "scan_truncated", "relationships_truncated",
        "complete", "incomplete_reasons", "query_depth", "traversal_truncated", "total_exact",
        "snapshot_id", "freshness", "semantic_complete", "language_id", "backend", "snapshot",
        "completeness_by_relation", "missing_inputs", "fallback_reason", "version",
        "confidence_summary", "parse_errors", "count", "offset", "limit", "total",
    })

    root: Annotated[str, Field(min_length=1, max_length=32_767)] | None = None
    symbol: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    ambiguous: StrictBool | None = None
    definitions: Annotated[list[ContextRow], Field(max_length=1_000)] | None = None
    callers: Annotated[list[ContextRow], Field(max_length=1_000)] | None = None
    callees: Annotated[list[ContextRow], Field(max_length=1_000)] | None = None
    references: Annotated[list[ContextRow], Field(max_length=1_000)] | None = None
    imports: Annotated[list[ContextRow], Field(max_length=1_000)] | None = None
    tests: Annotated[list[ContextRow], Field(max_length=1_000)] | None = None
    inheritance: Annotated[list[ContextRow], Field(max_length=1_000)] | None = None
    files_scanned: Annotated[StrictInt, Field(ge=0, le=50_000)] | None = None
    scan_truncated: StrictBool | None = None
    relationships_truncated: StrictBool | None = None
    complete: StrictBool | None = None
    incomplete_reasons: Annotated[list[str], Field(max_length=100)] | None = None
    query_depth: Annotated[StrictInt, Field(ge=0, le=2)] | None = None
    traversal_truncated: StrictBool | None = None
    total_exact: StrictBool | None = None
    snapshot_id: Annotated[str, Field(max_length=128)] | None = None
    freshness: Annotated[str, Field(max_length=128)] | None = None
    semantic_complete: StrictBool | None = None
    language_id: Annotated[str, Field(max_length=128)] | None = None
    backend: Literal["ast", "lsp", "unsupported"] | None = None
    snapshot: SnapshotEvidence | None = None
    completeness_by_relation: Annotated[dict[RelationKind, StrictBool], Field(max_length=7)] | None = None
    missing_inputs: Annotated[list[str], Field(max_length=1_000)] | None = None
    fallback_reason: Annotated[str, Field(max_length=500)] | None = None
    version: Literal[1] | None = None
    confidence_summary: Annotated[dict[str, StrictInt], Field(max_length=8)] | None = None
    parse_errors: Annotated[list[ParseIssue], Field(max_length=20)] | None = None
    count: Annotated[StrictInt, Field(ge=0, le=1_000)] | None = None
    offset: Annotated[StrictInt, Field(ge=0, le=10_000_000)] | None = None
    limit: Annotated[StrictInt, Field(ge=1, le=1_000)] | None = None
    total: Annotated[StrictInt, Field(ge=0, le=10_000_000)] | None = None

    @model_validator(mode="after")
    def _validate_context_page(self) -> CodeContextResult:
        if not self.ok:
            return self
        if self.complete is False and not self.incomplete_reasons:
            raise ValueError("Incomplete context output requires a reason.")
        if self.count is not None and self.total is not None and self.count > self.total:
            raise ValueError("Context page count cannot exceed its total.")
        return self
