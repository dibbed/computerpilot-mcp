"""Bounded semantic context from the existing language-session owner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections import deque
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from urllib.parse import quote, unquote

import anyio
from pydantic import Field, ValidationError

from core.config import resolve_path
from core.errors import ToolError
from core.resource_locks import canonical_path
from tools.language.lsp import position_to_offset
from tools.language.requests import HierarchyItem, Range, _document_path, validate_request
from tools.language.sessions import LspSessionManager, SessionReply, _disk_document, session_generation
from tools.project.intelligence_models import (
    CodePosition,
    ContextEvidence,
    EvidenceEdge,
    LanguageId,
    RelationKind,
    SnapshotEvidence,
    SymbolKey,
)
from tools.project.project_types import detect_project_types, language_for_path

_LANGUAGES = frozenset({"python", "typescript", "typescriptreact", "javascript", "javascriptreact", "php"})
_RELATIONS: tuple[RelationKind, ...] = ("definition", "reference", "import", "caller", "callee", "inheritance", "related_test")


class SemanticSnapshot(ContextEvidence):
    edges: Annotated[list[EvidenceEdge], Field(max_length=10_000)] = Field(default_factory=list)
    cache_generation: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$", exclude=True)] = None


def _error(code: str = "lsp_result_invalid") -> ToolError:
    return ToolError(code, "Semantic context is unavailable or its evidence could not be validated.")


def _empty(language_id: str, reason: str) -> SemanticSnapshot:
    return SemanticSnapshot(
        language_id=cast(LanguageId, language_id) if language_id in _LANGUAGES else "unsupported",
        backend="lsp" if language_id in _LANGUAGES else "unsupported", fallback_reason=reason,
        missing_inputs=[reason], completeness_by_relation={kind: False for kind in _RELATIONS},
    )


class _Batch:
    def __init__(self, manager: LspSessionManager, root: Path, language: LanguageId, deadline: float, file_limit: int) -> None:
        self.manager, self.root, self.language, self.deadline = manager, root, language, deadline
        self.reply: SessionReply | None = None
        self.files: dict[str, tuple[str, int | None, int]] = {}
        self.symbols: dict[tuple[str, int, int, str, str], SymbolKey] = {}
        self.ranges: dict[SymbolKey, Range] = {}
        self.edges: dict[tuple[SymbolKey, SymbolKey, RelationKind, str], EvidenceEdge] = {}
        self.missing: set[str] = set()
        self.completeness: dict[RelationKind, bool] = {}
        self.limited = False
        self.file_limit = file_limit

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _error("lsp_timeout")
        return remaining

    def limit(self, reason: str) -> None:
        self.missing.add(reason)
        self.limited = True

    def rows(self, value: object, *, limit: int = 100) -> list[Any]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise _error()
        if len(value) > limit:
            self.limit("fanout_limit")
        return value[:limit]

    async def read(self, uri: str, version: int | None = None, expected: str | None = None) -> tuple[Path, str, str]:
        self.remaining()
        path = _document_path(uri, self.root)
        path, digest, text, size = await asyncio.to_thread(_disk_document, path.as_uri(), self.root, self.deadline)
        canonical_uri = path.as_uri()
        previous = self.files.get(canonical_uri)
        if (expected is not None and expected != digest) or (previous is not None and previous[0] != digest):
            raise _error("lsp_snapshot_changed")
        if previous is not None and version is not None and previous[1] is not None and previous[1] != version:
            raise _error("lsp_snapshot_changed")
        if previous is None and (
            len(self.files) >= self.file_limit or sum(row[2] for row in self.files.values()) + size > 16 * 1024 * 1024
        ):
            raise _error("lsp_evidence_capacity")
        self.files[canonical_uri] = (digest, version if version is not None else previous[1] if previous else None, size)
        self.remaining()
        return path, digest, text

    async def check_files(self) -> None:
        for uri in tuple(self.files):
            await self.read(uri)

    async def request(self, method: str, params: dict[str, object]) -> object:
        await self.check_files()
        reply = await self.manager.arequest_with_evidence(
            self.root, self.language, method, params, timeout_sec=min(300.0, self.remaining()),
        )
        if self.reply is None:
            self.deadline = min(self.deadline, time.monotonic() + 5)
        self.remaining()
        if self.reply is not None and (self.reply.key, self.reply.incarnation) != (reply.key, reply.incarnation):
            raise _error("lsp_snapshot_changed")
        if reply.key.workspace_root != canonical_path(self.root) or reply.key.language_id != self.language:
            raise _error("lsp_snapshot_changed")
        self.reply = reply
        if len(reply.documents) > 16:
            raise _error("lsp_evidence_capacity")
        for uri, version, digest in reply.documents:
            await self.read(uri, version, digest)
        await self.check_files()
        return reply.value

    def supports(self, capability: str, *relations: RelationKind) -> bool:
        supported = self.reply is not None and capability in self.reply.capabilities
        if not supported:
            self.missing.add("capability_missing:" + capability)
            for relation in relations:
                self.completeness[relation] = False
        return supported

    async def key(self, uri: str, location: object, name: str, kind: object, enclosing: object = None) -> SymbolKey:
        selected = Range.model_validate(location)
        path, digest, text = await self.read(uri)
        positions = [selected.start, selected.end]
        if enclosing is not None:
            outer = Range.model_validate(enclosing)
            if not (
                (outer.start.line, outer.start.character) <= (selected.start.line, selected.start.character)
                <= (selected.end.line, selected.end.character) <= (outer.end.line, outer.end.character)
            ):
                raise _error()
            positions.extend((outer.start, outer.end))
        for position in positions:
            position_to_offset(text, {"line": position.line, "character": position.character})
        language = language_for_path(path)
        if language == "unsupported":
            raise _error("unsupported_language")
        # A provider for one language cannot attest to a different-language document.
        families = {"typescriptreact": "typescript", "javascriptreact": "javascript"}
        if families.get(language, language) != families.get(self.language, self.language):
            raise _error("unsupported_language")
        if type(kind) is int and 1 <= kind <= 26:
            kind = str(kind)
        elif kind != "reference":
            raise _error()
        if not isinstance(name, str) or not name or len(name) > 500:
            raise _error()
        identity = path.as_uri(), selected.start.line, selected.start.character, kind, name
        previous = self.symbols.get(identity)
        if previous is not None:
            return previous
        if len(self.symbols) >= 1000:
            raise _error("lsp_evidence_capacity")
        key = SymbolKey(
            canonical_workspace=canonical_path(self.root), language_id=language,
            relative_uri=quote(path.relative_to(self.root).as_posix(), safe="/"), qualified_name=name, kind=kind,
            start_line=selected.start.line, start_character=selected.start.character, content_digest=digest,
        )
        self.symbols[identity] = key
        self.ranges[key] = selected
        return key

    def evidence(self, key: SymbolKey) -> SnapshotEvidence:
        if self.reply is None:
            raise _error()
        uri = next(identity[0] for identity, value in self.symbols.items() if value == key)
        return SnapshotEvidence(
            source_sha=None, worktree_id=hashlib.sha256(
                (canonical_path(self.root) + "\0" + self.reply.key.worktree_marker_digest).encode("utf-8")
            ).hexdigest(), document_digest=key.content_digest, document_version=self.files[uri][1],
            index_generation=session_generation(self.reply.key, self.reply.incarnation),
            provider_id=self.reply.key.provider_id, source_kind="lsp",
        )

    def position_params(self, key: SymbolKey) -> dict[str, object]:
        uri = next(identity[0] for identity, value in self.symbols.items() if value == key)
        return {"textDocument": {"uri": uri}, "position": {"line": key.start_line, "character": key.start_character}}

    def add_edge(
        self, source: SymbolKey, target: SymbolKey, kind: RelationKind, direction: Literal["incoming", "outgoing"], depth: int,
    ) -> None:
        identity = source, target, kind, direction
        if identity in self.edges:
            return
        if len(self.edges) >= 10_000:
            self.limit("edge_limit")
            return
        self.edges[identity] = EvidenceEdge(
            from_symbol=source, to_symbol=target, kind=kind, direction=direction, depth=depth,
            confidence="semantic_reported", evidence=self.evidence(target if direction == "outgoing" else source),
        )

    async def hierarchy_item(self, value: object, selected: SymbolKey | None = None) -> tuple[dict[str, object], SymbolKey]:
        if not isinstance(value, dict):
            raise _error()
        validated = await asyncio.to_thread(validate_request, self.root, "callHierarchy/incomingCalls", {"item": value})
        item = HierarchyItem.model_validate(validated["item"])
        key = await self.key(item.uri, item.selectionRange.model_dump(), item.name, item.kind)
        if selected is not None:
            same_position = (key.start_line, key.start_character) == (selected.start_line, selected.start_character)
            if (
                (key.relative_uri, key.kind) != (selected.relative_uri, selected.kind)
                or not (same_position or item.range == self.ranges[selected])
                or item.name not in {selected.qualified_name, selected.qualified_name.rsplit(".", 1)[-1]}
            ):
                raise _error("lsp_symbol_mismatch")
            canonical = await self.key(item.uri, item.selectionRange.model_dump(), selected.qualified_name, item.kind)
            for identity, candidate in tuple(self.symbols.items()):
                if candidate == key:
                    self.symbols[identity] = canonical
            key = canonical
        return cast(dict[str, object], validated["item"]), key

    async def hierarchy_root(self, initial: object, selected: SymbolKey) -> SymbolKey:
        roots = set()
        for raw in self.rows(initial):
            _item, key = await self.hierarchy_item(raw, selected)
            roots.add(key)
        if len(roots) > 1:
            raise _error("ambiguous_symbol")
        return roots.pop() if roots else selected

    async def traverse(
        self, initial: object, method: str, kind: RelationKind, incoming: bool, depth: int, selected: SymbolKey,
    ) -> None:
        queue: deque[tuple[dict[str, object], SymbolKey, int]] = deque()
        for raw in self.rows(initial):
            item, key = await self.hierarchy_item(raw, selected)
            queue.append((item, key, 0))
        visited: set[SymbolKey] = set()
        complete = True
        while queue and len(self.edges) < 10_000:
            self.remaining()
            item, current, hop = queue.popleft()
            if current in visited or hop >= depth:
                continue
            visited.add(current)
            response = await self.request(method, {"item": item})
            for row in self.rows(response):
                if not isinstance(row, dict):
                    raise _error()
                raw = row.get("from" if incoming else "to") if kind in {"caller", "callee"} else row
                other_item, other = await self.hierarchy_item(raw)
                if kind in {"caller", "callee"}:
                    if not isinstance(row.get("fromRanges"), list):
                        raise _error()
                    ranges = self.rows(row["fromRanges"])
                    caller_uri = cast(str, other_item["uri"] if incoming else item["uri"])
                    _, _, text = await self.read(caller_uri)
                    for raw_range in ranges:
                        reported = Range.model_validate(raw_range)
                        for position in (reported.start, reported.end):
                            position_to_offset(text, position.model_dump())
                self.add_edge(other if incoming else current, current if incoming else other, kind,
                              "incoming" if incoming else "outgoing", hop + 1)
                if hop + 1 < depth and other not in visited:
                    if len(queue) < 1000:
                        queue.append((other_item, other, hop + 1))
                    else:
                        self.limit("vertex_limit")
                        complete = False
        if queue:
            self.limit("edge_limit")
            complete = False
        self.completeness[kind] = self.completeness.get(kind, True) and complete and not self.limited


def _matches(name: object, container: object, symbol: str, match: Literal["exact", "contains"]) -> bool:
    if not isinstance(name, str):
        return False
    qualified = container + "." + name if isinstance(container, str) and container else name
    return symbol.casefold() in qualified.casefold() if match == "contains" else symbol in {name, qualified}


def _file_matches(relative: str, needle: str | None, exact: bool) -> bool:
    if not needle:
        return True
    normalized = relative.replace("\\", "/").casefold()
    return normalized == needle if exact else needle in normalized


async def _select(
    batch: _Batch, symbol: str, anchor: CodePosition | None, match: Literal["exact", "contains"], file_filter: str | None,
    *, file_filter_exact: bool = False,
) -> list[SymbolKey]:
    candidates: list[SymbolKey] = []
    needle = file_filter.replace("\\", "/").casefold() if file_filter else None
    if anchor is None:
        response = await batch.request("workspace/symbol", {"query": symbol})
        if not batch.supports("workspaceSymbolProvider", "definition"):
            return []
        for item in batch.rows(response):
            if not isinstance(item, dict):
                raise _error()
            if not _matches(item.get("name"), item.get("containerName"), symbol, match):
                continue
            location = item.get("location")
            if not isinstance(location, dict) or not isinstance(location.get("uri"), str) or "range" not in location:
                raise _error("lsp_symbol_unresolved")
            path = _document_path(location["uri"], batch.root)
            if not _file_matches(path.relative_to(batch.root).as_posix(), needle, file_filter_exact):
                continue
            container = item.get("containerName")
            name = container + "." + item["name"] if isinstance(container, str) and container else item["name"]
            candidates.append(await batch.key(location["uri"], location["range"], name, item.get("kind")))
    else:
        path = Path(anchor.path)
        path = path if path.is_absolute() else batch.root / path
        params = {"textDocument": {"uri": path.as_uri()}, "position": {"line": anchor.line, "character": anchor.character}}
        await asyncio.to_thread(validate_request, batch.root, "textDocument/definition", params)
        response = await batch.request("textDocument/documentSymbol", {"textDocument": params["textDocument"]})
        if not batch.supports("documentSymbolProvider", "definition"):
            return []
        queue = deque((item, "", 0) for item in batch.rows(response))
        examined = 0
        while queue and examined < 1000:
            item, container, depth = queue.popleft()
            batch.remaining()
            examined += 1
            if not isinstance(item, dict):
                raise _error()
            name = item.get("name")
            if not isinstance(name, str):
                raise _error()
            location = item.get("location")
            uri = location.get("uri") if isinstance(location, dict) else path.as_uri()
            raw_range = location.get("range") if isinstance(location, dict) else item.get("selectionRange")
            if _matches(name, item.get("containerName", container), symbol, match):
                selected = Range.model_validate(raw_range)
                point = anchor.line, anchor.character
                start, end = (selected.start.line, selected.start.character), (selected.end.line, selected.end.character)
                if start <= point < end or start == end == point:
                    if not isinstance(uri, str) or _document_path(uri, batch.root) != path.resolve():
                        raise _error("lsp_anchor_mismatch")
                    owner = item.get("containerName", container)
                    qualified = owner + "." + name if isinstance(owner, str) and owner else name
                    enclosing = None if isinstance(location, dict) else Range.model_validate(item.get("range")).model_dump()
                    candidates.append(await batch.key(uri, raw_range, qualified, item.get("kind"), enclosing))
            children = item.get("children")
            if children:
                if depth >= 32 or len(queue) >= 1000:
                    batch.limit("symbol_limit")
                else:
                    children = batch.rows(children)
                    if len(queue) + len(children) > 1000:
                        batch.limit("symbol_limit")
                    queue.extend(
                        (child, container + "." + name if container else name, depth + 1)
                        for child in children[:1000 - len(queue)]
                    )
        if queue:
            batch.limit("symbol_limit")
    if file_filter:
        candidates = [key for key in candidates if _file_matches(unquote(key.relative_uri), needle, file_filter_exact)]
    return list(dict.fromkeys(candidates))


async def lookup_semantic_snapshot(
    manager: LspSessionManager, root: Path, symbol: str, *, language_id: str, anchor: CodePosition | None,
    max_depth: int, deadline: float, match: Literal["exact", "contains"] = "exact", file_filter: str | None = None,
    file_filter_exact: bool = False, max_files: int = 1000,
) -> SemanticSnapshot:
    """Read one provider/session snapshot; never guess a candidate or replay on failure."""
    if (
        not isinstance(symbol, str) or not symbol or len(symbol) > 500
        or type(max_depth) is not int or not 0 <= max_depth <= 2
        or type(deadline) not in {float, int} or not math.isfinite(deadline)
        or match not in {"exact", "contains"} or type(max_files) is not int or not 1 <= max_files <= 50_000
        or type(file_filter_exact) is not bool
        or (file_filter is not None and (not isinstance(file_filter, str) or len(file_filter) > 32_767))
    ):
        raise _error("invalid_argument")
    if language_id not in _LANGUAGES:
        return _empty(language_id, "unsupported_language")
    batch = _Batch(manager, root.resolve(), cast(LanguageId, language_id), min(deadline, time.monotonic() + 30), min(max_files, 1000))
    try:
        candidates = await _select(batch, symbol, anchor, match, file_filter, file_filter_exact=file_filter_exact)
        if len(candidates) != 1 or batch.limited:
            reason = "ambiguous_symbol" if candidates or batch.limited else next(iter(sorted(batch.missing)), "not_found")
            result = _empty(language_id, reason)
            await batch.check_files()
            return result.model_copy(update={
                "definitions": candidates[:100], "missing_inputs": sorted(batch.missing | set(result.missing_inputs)),
            })
        selected = candidates[0]
        batch.completeness["definition"] = False
        definitions = [selected]
        params = batch.position_params(selected)
        if batch.supports("definitionProvider", "definition"):
            response = await batch.request("textDocument/definition", params)
            locations = batch.rows(response if isinstance(response, list) or response is None else [response])
            resolved: list[SymbolKey] = []
            for location in locations:
                if not isinstance(location, dict):
                    raise _error()
                target_uri = location.get("targetUri", location.get("uri"))
                target_range = location.get("targetSelectionRange", location.get("range"))
                if not isinstance(target_uri, str):
                    raise _error()
                enclosing = Range.model_validate(location.get("targetRange")).model_dump() if "targetUri" in location else None
                target = await batch.key(target_uri, target_range, selected.qualified_name, int(selected.kind), enclosing)
                resolved.append(target)
                if target != selected:
                    batch.add_edge(selected, target, "definition", "outgoing", 0)
            definitions = list(dict.fromkeys(resolved)) or definitions
            batch.completeness["definition"] = not batch.limited
            if len(definitions) != 1 or batch.limited:
                await batch.check_files()
                return _empty(language_id, "ambiguous_symbol").model_copy(update={"definitions": definitions[:100]})
            selected = definitions[0]
            params = batch.position_params(selected)
        if max_depth >= 1:
            calls_supported = batch.supports("callHierarchyProvider", "caller", "callee")
            types_supported = batch.supports("typeHierarchyProvider", "inheritance")
            initial_calls = initial_types = None
            if calls_supported:
                initial_calls = await batch.request("textDocument/prepareCallHierarchy", params)
                selected = await batch.hierarchy_root(initial_calls, selected)
                params = batch.position_params(selected)
            if types_supported:
                initial_types = await batch.request("textDocument/prepareTypeHierarchy", params)
                selected = await batch.hierarchy_root(initial_types, selected)
                params = batch.position_params(selected)
            definitions = [selected]
            if batch.supports("referencesProvider", "reference"):
                response = await batch.request("textDocument/references", {**params, "context": {"includeDeclaration": False}})
                for location in batch.rows(response):
                    if not isinstance(location, dict) or not isinstance(location.get("uri"), str):
                        raise _error()
                    other = await batch.key(location["uri"], location.get("range"), selected.qualified_name, "reference")
                    batch.add_edge(selected, other, "reference", "outgoing", 1)
                batch.completeness["reference"] = not batch.limited
            if calls_supported:
                await batch.traverse(initial_calls, "callHierarchy/incomingCalls", "caller", True, max_depth, selected)
                await batch.traverse(initial_calls, "callHierarchy/outgoingCalls", "callee", False, max_depth, selected)
            if types_supported:
                await batch.traverse(initial_types, "typeHierarchy/supertypes", "inheritance", False, max_depth, selected)
                await batch.traverse(initial_types, "typeHierarchy/subtypes", "inheritance", True, max_depth, selected)
            # Python imports/tests remain owned by the AST adapter; providers cannot invent them.
            for relation in ("import", "related_test"):
                batch.completeness[relation] = False
                batch.missing.add("unsupported_relation:" + relation)
        await batch.check_files()
        edges = sorted(batch.edges.values(), key=lambda edge: (
            edge.kind, edge.direction, edge.depth, edge.from_symbol.relative_uri, edge.from_symbol.start_line,
            edge.from_symbol.start_character or 0, edge.to_symbol.relative_uri, edge.to_symbol.start_line,
            edge.to_symbol.start_character or 0, edge.from_symbol.qualified_name, edge.from_symbol.kind,
            edge.to_symbol.qualified_name, edge.to_symbol.kind,
        ))
        semantic_complete = not batch.limited and all(
            value for kind, value in batch.completeness.items() if kind not in {"import", "related_test"}
        )
        return SemanticSnapshot(
            language_id=batch.language, backend="lsp", definitions=definitions, edges=edges, snapshot=batch.evidence(selected),
            completeness_by_relation=batch.completeness, missing_inputs=sorted(batch.missing),
            complete=semantic_complete and all(batch.completeness.values()), semantic_complete=semantic_complete,
            total=len(edges), total_exact=not batch.limited, truncated=batch.limited,
            cache_generation=session_generation(batch.reply.key, batch.reply.incarnation, batch.reply.documents) if batch.reply else None,
        )
    except ToolError as exc:
        return _empty(language_id, exc.code)
    except (ValidationError, ValueError, TypeError, KeyError):
        return _empty(language_id, "lsp_result_invalid")


async def lookup_semantic_context(
    manager: LspSessionManager, root: Path, symbol: str, *, language_id: str, anchor: CodePosition | None,
    max_depth: int, max_relationships: int, offset: int, deadline: float,
) -> ContextEvidence:
    if type(max_relationships) is not int or not 1 <= max_relationships <= 1000 or type(offset) is not int or offset < 0:
        raise _error("invalid_argument")
    snapshot = await lookup_semantic_snapshot(
        manager, root, symbol, language_id=language_id, anchor=anchor, max_depth=max_depth, deadline=deadline,
    )
    page = snapshot.edges[offset:offset + max_relationships]
    truncated = snapshot.truncated or offset + len(page) < snapshot.total
    return ContextEvidence.model_validate({
        **snapshot.model_dump(), "edges": page, "offset": offset, "truncated": truncated,
        "complete": snapshot.complete and not truncated, "semantic_complete": snapshot.semantic_complete and not truncated,
    })


async def _offload(call: Any) -> Any:
    return await anyio.to_thread.run_sync(call, abandon_on_cancel=False)


def _family(language: str) -> str:
    return {"typescriptreact": "typescript", "javascriptreact": "javascript"}.get(language, language)


def _semantic_row(key: SymbolKey, evidence: SnapshotEvidence | None) -> dict[str, Any]:
    return {
        "file": unquote(key.relative_uri), "module": None, "qualified_name": key.qualified_name,
        "kind": {"5": "class", "6": "method", "12": "function"}.get(key.kind, key.kind),
        "line": key.start_line + 1, "end_line": None, "signature": None,
        "evidence": "lsp", "confidence": "semantic_reported", "source_kind": "lsp",
        "symbol_key": asdict(key), "snapshot": asdict(evidence) if evidence else None,
    }


def _ast_supplement(root: Path, selected: SymbolKey, options: dict[str, Any], offset: int) -> dict[str, Any] | None:
    from tools.project.context import build_context_index, lookup_code_context

    index = build_context_index(root, options["max_files"])
    candidates = [definition for definition in index.definitions if (
        definition.file == unquote(selected.relative_uri)
        and definition.qualified_name.rsplit(".", 1)[-1] == selected.qualified_name.rsplit(".", 1)[-1]
        and definition.line <= selected.start_line + 1 <= (definition.end_line or definition.line)
    )]
    if len(candidates) != 1:
        return None
    return lookup_code_context(
        root, candidates[0].qualified_name, file_filter=unquote(selected.relative_uri), file_filter_exact=True,
        include_callers=False, include_callees=False, include_references=False,
        include_imports=options["include_imports"], include_tests=options["include_tests"], include_source=options["include_source"],
        max_files=options["max_files"], max_depth=options["max_depth"], source_max_chars=options["source_max_chars"],
        max_relationships=options["max_relationships"], offset=offset,
    )


async def _render_context(
    snapshot: SemanticSnapshot, root: Path, symbol: str, options: dict[str, Any],
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {name: [] for name in (
        "callers", "callees", "references", "imports", "tests", "inheritance",
    )}
    for edge in snapshot.edges:
        if edge.kind not in {"caller", "callee", "reference", "inheritance"}:
            continue
        category = {"caller": "callers", "callee": "callees", "reference": "references", "inheritance": "inheritance"}[edge.kind]
        if category != "inheritance" and not options["include_" + category]:
            continue
        key = edge.from_symbol if edge.direction == "incoming" else edge.to_symbol
        row = _semantic_row(key, edge.evidence)
        row.update(depth=edge.depth, direction=edge.direction, semantic_edge=edge.model_dump(mode="json", by_alias=True))
        if category == "references":
            row.update(name=key.qualified_name, column=key.start_character, coordinate_system="lsp_utf16")
        if category == "callers":
            row["call"] = edge.to_symbol.qualified_name
        groups[category].append(row)
    primary = [(kind, row) for kind in ("callers", "callees", "references") for row in groups[kind]]
    offset, limit = options["offset"], options["max_relationships"]
    missing = set(snapshot.missing_inputs)
    supplement = None
    if snapshot.language_id == "python" and len(snapshot.definitions) == 1 and any(
        options[name] for name in ("include_imports", "include_tests", "include_source")
    ):
        supplement = await _offload(partial(_ast_supplement, root, snapshot.definitions[0], options, max(0, offset - len(primary))))
        if supplement is None:
            missing.add("ast_symbol_unresolved")
    ast_total = supplement["total"] if supplement else 0
    supplement_incomplete: set[str] = set()
    if supplement:
        supplement_incomplete = set(supplement["incomplete_reasons"]) & {
            "scan_truncated", "relationships_truncated", "parse_errors",
            "symlink_outside_root", "index_deadline", "context_stale",
        }
        if supplement["freshness"] != "current":
            supplement_incomplete.add("context_stale")
        if supplement["scan_truncated"]:
            supplement_incomplete.add("scan_truncated")
        if supplement["parse_errors"]:
            supplement_incomplete.add("parse_errors")
        if not supplement["total_exact"] or supplement["traversal_truncated"]:
            supplement_incomplete.add("relationships_truncated")
        missing.update(supplement_incomplete)
    page = primary[offset:offset + limit]
    if supplement:
        ast_page = [(kind, row) for kind in ("imports", "tests") for row in supplement[kind]]
        page.extend(ast_page[:max(0, limit - len(page))])
    inheritance_offset = max(0, offset - len(primary) - ast_total)
    page.extend(("inheritance", row) for row in groups["inheritance"][inheritance_offset:inheritance_offset + max(0, limit - len(page))])
    total = len(primary) + ast_total + len(groups["inheritance"])
    paged: dict[str, list[dict[str, Any]]] = {name: [] for name in groups}
    for category, row in page:
        paged[category].append(row)
    definitions = [_semantic_row(key, snapshot.snapshot if len(snapshot.definitions) == 1 else None) for key in snapshot.definitions]
    source_remaining = options["source_max_chars"]
    source_incomplete = False
    if options["include_source"]:
        ast_rows = supplement["definitions"] if supplement else []
        for row, key in zip(definitions, snapshot.definitions, strict=True):
            known = next((item for item in ast_rows if item["file"] == row["file"]), None)
            if known is not None:
                for field in ("source", "source_truncated", "end_line", "signature", "module"):
                    if field in known:
                        row[field] = known[field]
            else:
                path = root / unquote(key.relative_uri)
                _, digest, text, _size = await _offload(partial(_disk_document, path.as_uri(), root, time.monotonic() + 5))
                if digest != key.content_digest:
                    raise _error("lsp_snapshot_changed")
                lines = text.split("\n")
                excerpt = lines[key.start_line] if key.start_line < len(lines) else ""
                row.update(source=excerpt[:source_remaining], source_truncated=True)
                source_remaining = max(0, source_remaining - len(row["source"]))
            source_incomplete |= bool(row.get("source_truncated"))
    if source_incomplete:
        missing.add("source_extent_unavailable")
    requested: dict[RelationKind, bool] = {"definition": True}
    if options["max_depth"]:
        requested.update({"caller": options["include_callers"], "callee": options["include_callees"],
                          "reference": options["include_references"], "import": options["include_imports"],
                          "related_test": options["include_tests"], "inheritance": True})
    completeness = {kind: snapshot.completeness_by_relation.get(kind, False) for kind, active in requested.items() if active}
    for relation, flag in (("import", "include_imports"), ("related_test", "include_tests")):
        if not options[flag] or options["max_depth"] == 0:
            missing.discard("unsupported_relation:" + relation)
        elif supplement:
            missing.discard("unsupported_relation:" + relation)
            completeness[cast(RelationKind, relation)] = not supplement_incomplete
    if not options["include_callers"] and not options["include_callees"]:
        missing.discard("capability_missing:callHierarchyProvider")
    if not options["include_references"]:
        missing.discard("capability_missing:referencesProvider")
    truncated = snapshot.truncated or offset + len(page) < total
    total_exact = snapshot.total_exact and (supplement["total_exact"] if supplement else not (
        snapshot.language_id == "python" and options["max_depth"] > 0 and (options["include_imports"] or options["include_tests"])
    ))
    semantic_complete = not truncated and all(value for kind, value in completeness.items() if kind not in {"import", "related_test"})
    confidence: dict[str, int] = {}
    for _category, row in page:
        label = str(row.get("confidence", "inferred"))
        confidence[label] = confidence.get(label, 0) + 1
    reasons = sorted(missing | ({"relationships_truncated"} if truncated else set()))
    freshness = "current" if snapshot.snapshot else "unverified"
    if supplement is not None and supplement["freshness"] != "current":
        freshness = str(supplement["freshness"])
    return {
        "ok": True, "root": str(root), "symbol": symbol, "ambiguous": snapshot.fallback_reason == "ambiguous_symbol",
        "definitions": definitions, **paged, "count": len(page), "offset": offset, "limit": limit, "total": total,
        "files_scanned": len({key.relative_uri for key in snapshot.definitions} | {
            key.relative_uri for edge in snapshot.edges for key in (edge.from_symbol, edge.to_symbol)
        }), "scan_truncated": snapshot.truncated, "relationships_truncated": truncated,
        "traversal_truncated": snapshot.truncated, "query_depth": options["max_depth"], "total_exact": total_exact,
        "complete": bool(completeness) and all(completeness.values()) and not reasons,
        "semantic_complete": semantic_complete, "freshness": freshness,
        "snapshot_id": snapshot.snapshot.index_generation if snapshot.snapshot else "",
        "snapshot": asdict(snapshot.snapshot) if snapshot.snapshot else None, "language_id": snapshot.language_id,
        "backend": "lsp", "completeness_by_relation": completeness, "missing_inputs": sorted(missing),
        "fallback_reason": snapshot.fallback_reason, "incomplete_reasons": reasons,
        "confidence_summary": confidence, "parse_errors": supplement["parse_errors"] if supplement else [], "version": 1,
    }


async def lookup_project_context(
    path: str, symbol: str, *, language_id: str | None, backend: Literal["auto", "ast", "semantic"],
    anchor: CodePosition | None, manager: LspSessionManager | None, options: dict[str, Any],
) -> dict[str, Any]:
    from tools.project.context import lookup_code_context
    from tools.project.index import PYTHON_METADATA_CACHE

    target, is_file = await _offload(lambda: (resolve_path(path), resolve_path(path).is_file()))
    root = target.parent if is_file else target
    selected_language = language_id or ("auto" if anchor else "python")
    anchor_language = None
    if anchor:
        anchor_path = resolve_path(anchor.path, base=root)
        _document_path(anchor_path.as_uri(), root)
        anchor_language = language_for_path(anchor_path)
    if selected_language == "auto":
        if anchor_language is not None or is_file:
            selected_language = anchor_language or language_for_path(target)
        else:
            inventory = await _offload(partial(detect_project_types, root))
            if not inventory.complete:
                raise _error("language_required")
            languages = {_family(language) for language in inventory.language_ids}
            if len(languages) > 1:
                raise _error("language_required")
            selected_language = next(iter(languages), "unsupported")
    if selected_language not in _LANGUAGES:
        raise _error("unsupported_language")
    if anchor_language is not None:
        if _family(anchor_language) != _family(selected_language):
            raise _error("lsp_language_mismatch")
        selected_language = anchor_language
    if backend == "ast" and selected_language != "python":
        raise _error("unsupported_language")
    if backend == "ast" or (backend == "auto" and selected_language == "python" and anchor is None):
        return cast(dict[str, Any], await _offload(partial(lookup_code_context, target, symbol, **options)))
    semantic_options = dict(options)
    if is_file:
        semantic_options["file_filter"] = target.relative_to(root).as_posix()
        semantic_options["file_filter_exact"] = True
    else:
        semantic_options["file_filter_exact"] = False
    fingerprint_missing = False
    last_fingerprint: str | None = None

    async def fingerprint() -> str | None:
        nonlocal fingerprint_missing, last_fingerprint

        def scan() -> str | None:
            before = PYTHON_METADATA_CACHE.invalidation_epoch
            current = detect_project_types(root, fingerprint_sources=True)
            after = PYTHON_METADATA_CACHE.invalidation_epoch
            if before != after or current.source_fingerprint is None:
                return None
            return hashlib.sha256(f"{before}:{current.source_fingerprint}".encode("ascii")).hexdigest()

        try:
            last_fingerprint = await _offload(scan)
        except (ToolError, OSError, RuntimeError):
            last_fingerprint = None
        fingerprint_missing |= last_fingerprint is None
        return last_fingerprint

    async def build() -> tuple[bytes, str | None]:
        value = await lookup_semantic_snapshot(
            cast(LspSessionManager, manager), root, symbol, language_id=selected_language, anchor=anchor,
            max_depth=semantic_options["max_depth"], deadline=time.monotonic() + 30,
            match=semantic_options["match"], file_filter=semantic_options["file_filter"],
            file_filter_exact=semantic_options["file_filter_exact"], max_files=semantic_options["max_files"],
        )
        return value.model_dump_json().encode("utf-8"), value.cache_generation

    reason = "language_session_unavailable"
    if manager is not None:
        query = {key: semantic_options[key] for key in ("match", "file_filter", "file_filter_exact", "max_depth", "max_files")}
        query.update(symbol=symbol, anchor=anchor.model_dump() if anchor else None, language_id=selected_language)
        request_key = hashlib.sha256(json.dumps(query, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
        try:
            payload = await manager.acontext_result(root, selected_language, request_key, fingerprint=fingerprint, build=build)
            snapshot = SemanticSnapshot.model_validate_json(payload)
            fingerprint_at_build = last_fingerprint
            result = await _render_context(snapshot, root, symbol, options)
            final_fingerprint = await fingerprint()
            if fingerprint_at_build is not None and final_fingerprint != fingerprint_at_build:
                raise _error("lsp_snapshot_changed")
            if fingerprint_missing:
                result.update(complete=False, semantic_complete=False, freshness="unverified")
                result["missing_inputs"] = sorted(set(result["missing_inputs"]) | {"workspace_fingerprint_unavailable"})
                result["incomplete_reasons"] = sorted(set(result["incomplete_reasons"]) | {"workspace_fingerprint_unavailable"})
            if snapshot.fallback_reason is None or selected_language != "python":
                return result
            reason = snapshot.fallback_reason
        except ToolError as exc:
            reason = exc.code
        except (ValidationError, ValueError, TypeError):
            reason = "lsp_result_invalid"
    if selected_language == "python":
        result = await _offload(partial(lookup_code_context, target, symbol, **options))
        result.update(fallback_reason=reason, semantic_complete=False, complete=False)
        result["missing_inputs"] = sorted(set(result["missing_inputs"]) | {reason})
        result["incomplete_reasons"] = sorted(set(result["incomplete_reasons"]) | {reason})
        return cast(dict[str, Any], result)
    result = await _render_context(_empty(selected_language, reason), root, symbol, options)
    result["hint"] = "Configure a pinned local language provider and enable persistent sessions through local operator settings."
    return result
