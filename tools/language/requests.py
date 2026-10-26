"""Strict, bounded validation shared by public and in-process LSP callers."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import unquote_to_bytes, urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, model_validator

from core.errors import ToolError
from core.response_models import NativeFailure, WireRootModel
from core.workflow_store import redact_inputs
from tools.language.lsp import position_to_offset, uri_to_path

_MAX_PARAMS_BYTES = 64 * 1024
_MAX_DOCUMENT_BYTES = 2 * 1024 * 1024


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Position(_Request):
    line: Annotated[StrictInt, Field(ge=0, le=2**31 - 1)]
    character: Annotated[StrictInt, Field(ge=0, le=2**31 - 1)]


class DocumentIdentifier(_Request):
    uri: Annotated[str, Field(min_length=1, max_length=32_767)]


class DocumentRequest(_Request):
    textDocument: DocumentIdentifier


class PositionRequest(DocumentRequest):
    position: Position


class Range(_Request):
    start: Position
    end: Position

    @model_validator(mode="after")
    def ordered(self) -> Range:
        if (self.start.line, self.start.character) > (self.end.line, self.end.character):
            raise ValueError("Range endpoints must be ordered")
        return self


class DiagnosticLocation(_Request):
    uri: Annotated[str, Field(min_length=1, max_length=32_767)]
    range: Range


class DiagnosticRelatedInformation(_Request):
    location: DiagnosticLocation
    message: Annotated[str, Field(max_length=32_767)]


class LanguageDiagnostic(_Request):
    range: Range
    message: Annotated[str, Field(max_length=32_767)]
    severity: Annotated[StrictInt, Field(ge=1, le=4)] | None = None
    source: Annotated[str, Field(max_length=500)] | None = None
    code: StrictInt | str | None = None
    codeDescription: dict[str, Annotated[str, Field(max_length=32_767)]] | None = None
    tags: Annotated[list[Annotated[StrictInt, Field(ge=1, le=2)]], Field(max_length=2)] | None = None
    relatedInformation: Annotated[list[DiagnosticRelatedInformation], Field(max_length=1_000)] | None = None
    data: Any = None


class DiagnosticReport(_Request):
    model_config = ConfigDict(json_schema_extra={"allOf": [
        {
            "if": {"properties": {"kind": {"const": "full"}}},
            "then": {"required": ["items"], "properties": {"items": {"type": "array"}}},
        },
        {
            "if": {"properties": {"kind": {"const": "unchanged"}}},
            "then": {
                "required": ["resultId"],
                "properties": {"resultId": {"type": "string"}},
                "not": {"required": ["items"]},
            },
        },
    ]})

    kind: Literal["full", "unchanged"]
    items: Annotated[list[LanguageDiagnostic], Field(max_length=10_000)] | None = None
    resultId: Annotated[str, Field(min_length=1, max_length=32_767)] | None = None
    relatedDocuments: dict[str, DiagnosticReport] | None = None
    complete: StrictBool | None = None

    @model_validator(mode="after")
    def _validate_report_kind(self) -> DiagnosticReport:
        if self.kind == "full" and self.items is None:
            raise ValueError("Full diagnostic reports require their item list.")
        if self.kind == "unchanged" and (self.resultId is None or "items" in self.model_fields_set):
            raise ValueError("Unchanged diagnostics require a result identity and no replacement items.")
        return self


class DiagnosticSuccess(_Request):
    ok: Literal[True]
    method: Literal["textDocument/diagnostic"]
    result: DiagnosticReport


class DiagnosticsResult(WireRootModel[DiagnosticSuccess | NativeFailure]):
    pass


def sanitize_diagnostic_report(value: Any) -> Any:
    """Redact opaque provider data before validating or publishing diagnostics."""
    if not isinstance(value, dict):
        return value
    result = dict(value)
    if isinstance(result.get("items"), list):
        result["items"] = [
            {**item, "data": redact_inputs(item["data"])} if isinstance(item, dict) and "data" in item else item
            for item in result["items"]
        ]
    if isinstance(result.get("relatedDocuments"), dict):
        result["relatedDocuments"] = {uri: sanitize_diagnostic_report(report) for uri, report in result["relatedDocuments"].items()}
    return result


class HierarchyItem(_Request):
    name: Annotated[str, Field(min_length=1, max_length=500)]
    kind: Annotated[StrictInt, Field(ge=1, le=26)]
    uri: Annotated[str, Field(min_length=1, max_length=32_767)]
    range: Range
    selectionRange: Range
    detail: Annotated[str | None, Field(max_length=500)] = None
    tags: Annotated[list[Annotated[StrictInt, Field(ge=1, le=1)]] | None, Field(max_length=1)] = None
    data: Any = None

    @model_validator(mode="after")
    def contained(self) -> HierarchyItem:
        def position(p: Position) -> tuple[int, int]:
            return p.line, p.character

        if not (
            position(self.range.start) <= position(self.selectionRange.start)
            <= position(self.selectionRange.end) <= position(self.range.end)
        ):
            raise ValueError("Selection must stay within its symbol range")
        return self


class HierarchyItemRequest(_Request):
    item: HierarchyItem


class ReferenceContext(_Request):
    includeDeclaration: StrictBool = True


class ReferencesRequest(PositionRequest):
    context: ReferenceContext = Field(default_factory=ReferenceContext)


class WorkspaceSymbolRequest(_Request):
    query: Annotated[str, Field(max_length=500)]


class DiagnosticRequest(DocumentRequest):
    identifier: Annotated[str | None, Field(max_length=128)] = None
    previousResultId: Annotated[str | None, Field(max_length=512)] = None


class RenameRequest(PositionRequest):
    newName: Annotated[str, Field(min_length=1, max_length=500)]


_SUFFIX_LANGUAGES: dict[str, str] = {
    # Provider-supported languages only; unknown suffixes retain the legacy python default.
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".php": "php",
}
_MAX_URI_CHARS = 32_767


def language_for_uri(uri: str) -> str | None:
    """Infer one supported language from a bounded document URI without filesystem access."""

    if len(uri) > _MAX_URI_CHARS:
        return None
    try:
        decoded = unquote_to_bytes(urlsplit(uri).path).decode("utf-8", errors="strict")
    except (UnicodeError, ValueError):
        return None
    return _SUFFIX_LANGUAGES.get(Path(decoded).suffix.casefold())


def select_request(
    params: Mapping[str, object] | None, request: BaseModel | None
) -> dict[str, Any]:
    """Select exactly one legacy params tree or typed request model."""

    if (params is None) == (request is None):
        raise ToolError("lsp_request_shape", "Provide exactly one of params or request.")
    if request is not None:
        if not isinstance(request, BaseModel):
            raise ToolError("lsp_request_shape", "Provide exactly one of params or request.")
        return request.model_dump(exclude_none=True)
    if not isinstance(params, Mapping):
        raise ToolError("lsp_request_shape", "Provide exactly one of params or request.")
    return dict(params)


_MODELS: dict[str, type[_Request]] = {
    "textDocument/definition": PositionRequest,
    "textDocument/references": ReferencesRequest,
    "textDocument/documentSymbol": DocumentRequest,
    "workspace/symbol": WorkspaceSymbolRequest,
    "textDocument/hover": PositionRequest,
    "textDocument/prepareCallHierarchy": PositionRequest,
    "textDocument/diagnostic": DiagnosticRequest,
    "textDocument/rename": RenameRequest,
    "textDocument/prepareTypeHierarchy": PositionRequest,
    "callHierarchy/incomingCalls": HierarchyItemRequest,
    "callHierarchy/outgoingCalls": HierarchyItemRequest,
    "typeHierarchy/supertypes": HierarchyItemRequest,
    "typeHierarchy/subtypes": HierarchyItemRequest,
}


def _invalid() -> ToolError:
    return ToolError("lsp_params_invalid", "The language-server request parameters are invalid or exceed their bounds.")


def _validate_tree(params: Mapping[str, object]) -> None:
    stack: list[tuple[object, int]] = [(params, 0)]
    budget = 0
    while stack:
        value, depth = stack.pop()
        if depth > 16:
            raise _invalid()
        if isinstance(value, Mapping):
            if len(value) > 100 or any(not isinstance(key, str) for key in value):
                raise _invalid()
            stack.extend((part, depth + 1) for pair in value.items() for part in pair)
            budget += 2 + len(value) * 2
        elif isinstance(value, list):
            if len(value) > _MAX_PARAMS_BYTES // 2:
                raise _invalid()
            stack.extend((item, depth + 1) for item in value)
            budget += 2 + len(value)
        elif isinstance(value, str):
            if len(value) > _MAX_PARAMS_BYTES or "\x00" in value:
                raise _invalid()
            try:
                budget += len(value.encode("utf-8")) + 2
            except UnicodeError:
                raise _invalid() from None
        elif value is None or type(value) is bool:
            budget += 5
        elif type(value) in {int, float}:
            if isinstance(value, float) and not math.isfinite(value):
                raise _invalid()
            try:
                budget += len(str(value))
            except ValueError:
                raise _invalid() from None
        else:
            raise _invalid()
        if budget > _MAX_PARAMS_BYTES:
            raise _invalid()
    try:
        payload = json.dumps(dict(params), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise _invalid() from None
    if len(payload) > _MAX_PARAMS_BYTES:
        raise _invalid()


def _document_path(uri: str, root: Path) -> Path:
    try:
        parsed = urlsplit(uri)
        if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError
        if re.search(r"%(?![0-9a-fA-F]{2})", uri):
            raise ValueError
        decoded = unquote_to_bytes(parsed.path).decode("utf-8", errors="strict")
        if "\x00" in decoded or not decoded.startswith("/"):
            raise ValueError
    except (ValueError, UnicodeError):
        raise ToolError("lsp_uri_unsupported", "A valid local file URI is required.") from None
    return uri_to_path(uri, root)


def validate_request(root: Path, method: str, params: Mapping[str, object]) -> dict[str, object]:
    """Validate without launching a server or exposing rejected source/input values."""
    model = _MODELS.get(method)
    if model is None:
        raise ToolError("lsp_method_unsupported", "The language-server method is not supported.")
    root = root.resolve(strict=False)
    if not root.is_dir():
        raise ToolError("lsp_workspace_not_found", "The language-server workspace is unavailable.")
    if not isinstance(params, Mapping):
        raise _invalid()
    _validate_tree(params)
    try:
        request = model.model_validate(dict(params))
    except ValidationError:
        raise _invalid() from None
    result = request.model_dump(exclude_none=True)
    positions: list[Position] = []
    if isinstance(request, (DocumentRequest, HierarchyItemRequest)):
        uri = request.textDocument.uri if isinstance(request, DocumentRequest) else request.item.uri
        path = _document_path(uri, root)
        if isinstance(request, DocumentRequest):
            result["textDocument"] = {"uri": path.as_uri()}
        else:
            result["item"]["uri"] = path.as_uri()
            positions = [
                request.item.range.start, request.item.range.end,
                request.item.selectionRange.start, request.item.selectionRange.end,
            ]
        if isinstance(request, PositionRequest):
            positions.append(request.position)
        if positions:
            try:
                with path.open("rb") as handle:
                    data = handle.read(_MAX_DOCUMENT_BYTES + 1)
            except OSError:
                raise ToolError("lsp_document_unavailable", "The language-server document is unavailable.") from None
            if len(data) > _MAX_DOCUMENT_BYTES:
                raise ToolError("lsp_document_too_large", "The language-server document exceeds the size limit.")
            try:
                text = data.decode("utf-8", errors="strict")
            except UnicodeError:
                raise ToolError("lsp_document_encoding", "Language-server documents must be UTF-8.") from None
            for position in positions:
                position_to_offset(text, {"line": position.line, "character": position.character})
    return result
