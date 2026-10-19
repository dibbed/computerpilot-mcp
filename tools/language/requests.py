"""Strict, bounded validation shared by public and in-process LSP callers."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote_to_bytes, urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError

from core.errors import ToolError
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


_MODELS: dict[str, type[_Request]] = {
    "textDocument/definition": PositionRequest,
    "textDocument/references": ReferencesRequest,
    "textDocument/documentSymbol": DocumentRequest,
    "workspace/symbol": WorkspaceSymbolRequest,
    "textDocument/hover": PositionRequest,
    "textDocument/prepareCallHierarchy": PositionRequest,
    "textDocument/diagnostic": DiagnosticRequest,
    "textDocument/rename": RenameRequest,
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
    if isinstance(request, DocumentRequest):
        path = _document_path(request.textDocument.uri, root)
        result["textDocument"] = {"uri": path.as_uri()}
        if isinstance(request, PositionRequest):
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
            position_to_offset(text, {"line": request.position.line, "character": request.position.character})
    return result
