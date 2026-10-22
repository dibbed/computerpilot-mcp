"""Bounded semantic context from the existing language-session owner."""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from collections import deque
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from urllib.parse import quote

from pydantic import Field, ValidationError

from core.errors import ToolError
from core.resource_locks import canonical_path
from tools.language.lsp import position_to_offset
from tools.language.requests import HierarchyItem, Position, Range, _document_path, validate_request
from tools.language.sessions import LspSessionManager, SessionReply, _disk_document
from tools.project.intelligence_models import ContextEvidence, EvidenceEdge, LanguageId, RelationKind, SnapshotEvidence, SymbolKey
from tools.project.project_types import language_for_path

_LANGUAGES = frozenset({"python", "typescript", "typescriptreact", "javascript", "javascriptreact", "php"})
_RELATIONS: tuple[RelationKind, ...] = ("definition", "reference", "import", "caller", "callee", "inheritance", "related_test")


class CodePosition(Position):
    path: Annotated[str, Field(min_length=1, max_length=32_767)]


def _error(code: str = "lsp_result_invalid") -> ToolError:
    return ToolError(code, "Semantic context is unavailable or its evidence could not be validated.")


def _empty(language_id: str, reason: str, *, offset: int = 0) -> ContextEvidence:
    return ContextEvidence(
        language_id=cast(LanguageId, language_id) if language_id in _LANGUAGES else "unsupported",
        backend="lsp" if language_id in _LANGUAGES else "unsupported", fallback_reason=reason,
        missing_inputs=[reason], completeness_by_relation={kind: False for kind in _RELATIONS}, offset=offset,
    )


class _Batch:
    def __init__(self, manager: LspSessionManager, root: Path, language: LanguageId, deadline: float) -> None:
        self.manager, self.root, self.language, self.deadline = manager, root, language, deadline
        self.reply: SessionReply | None = None
        self.files: dict[str, tuple[str, int | None, int]] = {}
        self.symbols: dict[tuple[str, int, int, str, str], SymbolKey] = {}
        self.ranges: dict[SymbolKey, Range] = {}
        self.edges: dict[tuple[SymbolKey, SymbolKey, RelationKind, str], EvidenceEdge] = {}
        self.missing: set[str] = set()
        self.completeness: dict[RelationKind, bool] = {}
        self.limited = False

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
        if previous is None and (len(self.files) >= 1000 or sum(row[2] for row in self.files.values()) + size > 16 * 1024 * 1024):
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
        identity = repr((self.reply.key, self.reply.incarnation)).encode("utf-8")
        return SnapshotEvidence(
            source_sha=None, worktree_id=hashlib.sha256(
                (canonical_path(self.root) + "\0" + self.reply.key.worktree_marker_digest).encode("utf-8")
            ).hexdigest(), document_digest=key.content_digest, document_version=self.files[uri][1],
            index_generation=hashlib.sha256(identity).hexdigest(), provider_id=self.reply.key.provider_id, source_kind="lsp",
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


def _matches(name: object, container: object, symbol: str) -> bool:
    return isinstance(name, str) and (name == symbol or (isinstance(container, str) and container + "." + name == symbol))


async def _select(batch: _Batch, symbol: str, anchor: CodePosition | None) -> list[SymbolKey]:
    candidates: list[SymbolKey] = []
    if anchor is None:
        response = await batch.request("workspace/symbol", {"query": symbol})
        if not batch.supports("workspaceSymbolProvider", "definition"):
            return []
        for item in batch.rows(response):
            if not isinstance(item, dict):
                raise _error()
            if not _matches(item.get("name"), item.get("containerName"), symbol):
                continue
            location = item.get("location")
            if not isinstance(location, dict) or not isinstance(location.get("uri"), str) or "range" not in location:
                raise _error("lsp_symbol_unresolved")
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
            if _matches(name, item.get("containerName", container), symbol):
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
    return list(dict.fromkeys(candidates))


async def lookup_semantic_context(
    manager: LspSessionManager, root: Path, symbol: str, *, language_id: str, anchor: CodePosition | None,
    max_depth: int, max_relationships: int, offset: int, deadline: float,
) -> ContextEvidence:
    """Read one provider/session snapshot; never guess a candidate or replay on failure."""
    if (
        not isinstance(symbol, str) or not symbol or len(symbol) > 500
        or type(max_depth) is not int or not 0 <= max_depth <= 2
        or type(max_relationships) is not int or not 1 <= max_relationships <= 1000
        or type(offset) is not int or offset < 0
        or type(deadline) not in {float, int} or not math.isfinite(deadline)
    ):
        raise _error("invalid_argument")
    if language_id not in _LANGUAGES:
        return _empty(language_id, "unsupported_language", offset=offset)
    batch = _Batch(manager, root.resolve(), cast(LanguageId, language_id), min(deadline, time.monotonic() + 5))
    try:
        candidates = await _select(batch, symbol, anchor)
        if len(candidates) != 1 or batch.limited:
            reason = "ambiguous_symbol" if candidates or batch.limited else next(iter(sorted(batch.missing)), "not_found")
            result = _empty(language_id, reason, offset=offset)
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
                return _empty(language_id, "ambiguous_symbol", offset=offset).model_copy(update={"definitions": definitions[:100]})
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
        page = edges[offset:offset + max_relationships]
        truncated = batch.limited or offset + len(page) < len(edges)
        semantic_complete = not truncated and all(
            value for kind, value in batch.completeness.items() if kind not in {"import", "related_test"}
        )
        return ContextEvidence(
            language_id=batch.language, backend="lsp", definitions=definitions, edges=page, snapshot=batch.evidence(selected),
            completeness_by_relation=batch.completeness, missing_inputs=sorted(batch.missing),
            complete=semantic_complete and all(batch.completeness.values()), semantic_complete=semantic_complete,
            offset=offset, total=len(edges), total_exact=not batch.limited, truncated=truncated,
        )
    except ToolError as exc:
        return _empty(language_id, exc.code, offset=offset)
    except (ValidationError, ValueError, TypeError, KeyError):
        return _empty(language_id, "lsp_result_invalid", offset=offset)
