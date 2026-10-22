"""Versioned internal evidence for project intelligence; existing AST rows stay unchanged."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from urllib.parse import unquote_to_bytes, urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from pydantic.dataclasses import dataclass

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
