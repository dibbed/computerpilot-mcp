"""Bounded, evidence-qualified Python code context lookup."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from pydantic import ValidationError

from core.errors import ToolError
from core.resource_locks import canonical_path
from core.singleflight import SingleFlight
from tools.project import service
from tools.project.index import (
    CACHE_ENTRY_OVERHEAD_BYTES,
    CallMetadata,
    FileVersion,
    PythonFileMetadata,
    _deep_size,
    file_version,
)
from tools.project.intelligence_models import SnapshotEvidence, SymbolKey
from tools.project.project_types import ProjectInventory, detect_project_types

Evidence = Literal["exact_qualified", "import_bound", "same_module", "name_only"]
EVIDENCE_RANK: dict[Evidence, int] = {
    "exact_qualified": 0,
    "import_bound": 1,
    "same_module": 2,
    "name_only": 3,
}
Confidence = Literal["static_resolved", "inferred"]
Identity = tuple[str, str, int, str]
SourceSignature = tuple[tuple[str, int, int, str], ...]
SOURCE_KIND = "python_ast"
MAX_CONTEXT_DEFINITIONS = 1_000
MAX_TRAVERSAL_VERTICES = 1_000
MAX_TRAVERSAL_EDGES = 10_000
TRAVERSAL_SECONDS = 1.0
TRAVERSAL_BATCH = 64
CONTEXT_MAX_ENTRIES = 16
CONTEXT_MAX_BYTES = 64 * 1024 * 1024
CONTEXT_INDEX_DEADLINE_SECONDS = 5.0
MAX_ACTIVE_CONTEXT_BUILDS = 16
MAX_CONTEXT_BUILD_ATTEMPTS = 2


def _confidence(evidence: Evidence) -> Confidence:
    return "inferred" if evidence == "name_only" else "static_resolved"


@dataclass(frozen=True, slots=True)
class Definition:
    file: str
    module: str
    qualified_name: str
    local_name: str
    kind: Literal["function", "class"]
    line: int
    end_line: int | None
    signature: str | None


@dataclass(frozen=True, slots=True)
class IndexedFile:
    path: Path
    relative: str
    module: str
    metadata: PythonFileMetadata
    imports: tuple[tuple[str, str], ...]
    version: FileVersion


@dataclass(frozen=True, slots=True)
class ContextIndex:
    root: Path
    files: tuple[IndexedFile, ...]
    definitions: tuple[Definition, ...]
    scan_truncated: bool
    parse_errors: tuple[dict[str, str], ...]
    versions: tuple[tuple[str, FileVersion], ...] = ()
    outside_root: tuple[str, ...] = ()
    deadline_exceeded: bool = False
    snapshot_id: str = ""
    freshness: str = "current"
    inventory: ProjectInventory | None = None


@dataclass(slots=True)
class _ContextEntry:
    signature: SourceSignature
    index: ContextIndex
    estimated_bytes: int


def _file_signature(files: tuple[Path, ...], root: Path) -> tuple[SourceSignature, ProjectInventory]:
    rows: list[tuple[str, int, int, str]] = []
    for path in files:
        version = file_version(path)
        rows.append((canonical_path(path), version.mtime_ns, version.size, version.content_sha256))
    if root.is_file():
        # File-only callers retain their original scope; project markers need a directory scope.
        inventory = ProjectInventory(root.parent, (), hashlib.sha256(canonical_path(root.parent).encode()).hexdigest(),
                                     ("python",) if files else (), True, ())
    else:
        inventory = detect_project_types(root)
    metadata = [
        (manifest.path.relative_to(inventory.root).as_posix(), 0, 0, manifest.sha256)
        for package in inventory.packages for manifest in package.manifests
    ]
    metadata.extend((
        ("<worktree>", 0, 0, inventory.worktree_id),
        ("<inventory>", int(inventory.complete), 0, hashlib.sha256("\0".join(inventory.missing_inputs).encode("utf-8")).hexdigest()),
    ))
    rows.append(("<project_metadata>", 0, 0, _snapshot_id(canonical_path(root), tuple(metadata), 0)))
    return tuple(rows), inventory


def _snapshot_id(root: str, signature: SourceSignature, max_files: int) -> str:
    digest = hashlib.sha256()

    def frame(value: str) -> None:
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)

    frame(root)
    frame(str(max_files))
    for row in signature:
        for part in row:
            frame(str(part))
    return digest.hexdigest()


def _relative_import(module: str, source: str, level: int) -> str:
    if level == 0:
        return source
    parent = module.split(".")[:-1]
    keep = max(len(parent) - level + 1, 0)
    prefix = parent[:keep]
    if source:
        prefix.extend(source.split("."))
    return ".".join(prefix)


def _import_bindings(module: str, metadata: PythonFileMetadata) -> tuple[tuple[str, str], ...]:
    bindings: list[tuple[str, str]] = []
    for item in metadata.imports:
        base = _relative_import(module, item.source or "", item.level)
        for local, target in item.bindings:
            if item.source is not None and item.level:
                suffix = target[len(item.source) :].lstrip(".") if item.source else target
                resolved = ".".join(part for part in (base, suffix) if part)
            else:
                resolved = target
            bindings.append((local, resolved))
    return tuple(bindings)


def _build_context_index(
    root: Path,
    files: tuple[Path, ...],
    scan_truncated: bool,
    *,
    deadline: float | None = None,
) -> ContextIndex:
    indexed_files: list[IndexedFile] = []
    definitions: list[Definition] = []
    parse_errors: list[dict[str, str]] = []
    versions: list[tuple[str, FileVersion]] = []
    outside_root: list[str] = []
    deadline_exceeded = False
    root_resolved = root.resolve(strict=False)
    for path in files:
        if deadline is not None and time.monotonic() > deadline:
            deadline_exceeded = True
            break
        relative = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
        if path.is_symlink():
            try:
                target = path.resolve(strict=True)
            except OSError:
                target = None
            if target is None or not target.is_relative_to(root_resolved):
                outside_root.append(relative)
                continue
        module = service.module_name(root, path) if root.is_dir() else path.stem
        version = file_version(path)
        metadata = service.python_metadata(path)
        versions.append((relative, version))
        if metadata.parse_error:
            if len(parse_errors) < 20:
                parse_errors.append({"file": relative, "error": metadata.parse_error})
            continue
        indexed_files.append(
            IndexedFile(
                path=path,
                relative=relative,
                module=module,
                metadata=metadata,
                imports=_import_bindings(module, metadata),
                version=version,
            )
        )
        for function_item in metadata.functions:
            definitions.append(
                Definition(
                    file=relative,
                    module=module,
                    qualified_name=".".join(part for part in (module, function_item.qualified_name) if part),
                    local_name=function_item.qualified_name,
                    kind="function",
                    line=function_item.line,
                    end_line=function_item.end_line,
                    signature=function_item.signature,
                )
            )
        for class_item in metadata.classes:
            definitions.append(
                Definition(
                    file=relative,
                    module=module,
                    qualified_name=".".join(part for part in (module, class_item.qualified_name) if part),
                    local_name=class_item.qualified_name,
                    kind="class",
                    line=class_item.line,
                    end_line=class_item.end_line,
                    signature=None,
                )
            )
    if deadline_exceeded and not versions and files:
        raise ToolError("context_index_deadline", "Context index build exceeded its deadline before any file was read.")
    return ContextIndex(
        root=root,
        files=tuple(sorted(indexed_files, key=lambda item: item.relative.casefold())),
        definitions=tuple(sorted(definitions, key=lambda item: (item.qualified_name.casefold(), item.file, item.line))),
        scan_truncated=scan_truncated,
        parse_errors=tuple(parse_errors),
        versions=tuple(versions),
        outside_root=tuple(outside_root),
        deadline_exceeded=deadline_exceeded,
    )


class ContextIndexCache:
    def __init__(
        self,
        *,
        max_entries: int = CONTEXT_MAX_ENTRIES,
        max_bytes: int = CONTEXT_MAX_BYTES,
    ) -> None:
        if max_entries < 1 or max_bytes < 1:
            raise ValueError("Context index cache budgets must be positive.")
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._lock = threading.RLock()
        self._entries: OrderedDict[tuple[str, int], _ContextEntry] = OrderedDict()
        self._bytes = 0
        self._generations: dict[tuple[str, int], int] = {}
        self._active: dict[tuple[str, int], int] = {}
        self._active_builds = 0
        self._flights: SingleFlight[tuple[str, int, SourceSignature], ContextIndex] = SingleFlight()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "bytes": self._bytes,
                "max_entries": self.max_entries,
                "max_bytes": self.max_bytes,
                "active_builds": self._active_builds,
                "generations": len(self._generations),
            }

    def _begin_build(self, entry_key: tuple[str, int]) -> None:
        with self._lock:
            if self._active_builds >= MAX_ACTIVE_CONTEXT_BUILDS:
                raise ToolError(
                    "context_capacity_exceeded",
                    "Too many concurrent code_context index builds are in flight.",
                )
            self._active_builds += 1
            self._active[entry_key] = self._active.get(entry_key, 0) + 1
            self._generations.setdefault(entry_key, 0)

    def _end_build(self, entry_key: tuple[str, int]) -> None:
        with self._lock:
            remaining = self._active.get(entry_key, 1) - 1
            if remaining > 0:
                self._active[entry_key] = remaining
            else:
                self._active.pop(entry_key, None)
            self._active_builds = max(self._active_builds - 1, 0)
            if entry_key not in self._entries and entry_key not in self._active:
                self._generations.pop(entry_key, None)

    def get(self, root: Path, max_files: int) -> ContextIndex:
        resolved = root.resolve(strict=False)
        discovered, truncated = service.iter_python_files(resolved, max_files)
        files = tuple(sorted(discovered, key=lambda path: canonical_path(path)))
        try:
            signature, inventory = _file_signature(files, resolved)
        except ToolError:
            raise
        except OSError as exc:
            raise ToolError("context_stale", "Project sources changed while the context signature was computed.") from exc
        entry_key = (canonical_path(resolved), max_files)
        with self._lock:
            entry = self._entries.get(entry_key)
            if inventory.complete and entry is not None and entry.signature == signature:
                self._entries.move_to_end(entry_key)
                return entry.index
        self._begin_build(entry_key)
        try:
            flight_key = (*entry_key, signature)
            return self._flights.run(
                flight_key,
                lambda: self._build_verified(resolved, files, truncated, entry_key, signature, max_files),
            )
        finally:
            self._end_build(entry_key)

    def _build_verified(
        self,
        resolved: Path,
        files: tuple[Path, ...],
        truncated: bool,
        entry_key: tuple[str, int],
        signature: SourceSignature,
        max_files: int,
    ) -> ContextIndex:
        current_signature = signature
        last_error: ToolError | None = None
        for _attempt in range(MAX_CONTEXT_BUILD_ATTEMPTS):
            with self._lock:
                entry = self._entries.get(entry_key)
                if entry is not None and entry.signature == current_signature:
                    return entry.index
                generation = self._generations.get(entry_key, 0)
            deadline = time.monotonic() + CONTEXT_INDEX_DEADLINE_SECONDS
            try:
                index = _build_context_index(resolved, files, truncated, deadline=deadline)
            except ToolError as exc:
                if exc.code == "project_file_too_large":
                    raise
                last_error = exc
                continue
            except OSError:
                last_error = ToolError("context_stale", "Project sources changed while the context index was built.")
                continue
            if index.deadline_exceeded:
                return replace(index, freshness="stale")
            try:
                after, inventory = _file_signature(files, resolved)
            except ToolError as exc:
                if exc.code == "project_file_too_large":
                    raise
                last_error = exc
                continue
            except OSError:
                last_error = ToolError("context_stale", "Project sources changed while the context index was built.")
                continue
            if after != current_signature and (inventory.complete or after[:-1] != current_signature[:-1]):
                current_signature = after
                last_error = None
                continue
            index = replace(index, snapshot_id=_snapshot_id(entry_key[0], after, max_files),
                            freshness="current" if inventory.complete else "stale", inventory=inventory)
            if not inventory.complete:
                return index
            estimated = _deep_size(index) + CACHE_ENTRY_OVERHEAD_BYTES + 2 * len(entry_key[0])
            with self._lock:
                if self._generations.get(entry_key, 0) != generation:
                    return replace(index, freshness="stale")
                if estimated <= self.max_bytes:
                    existing = self._entries.pop(entry_key, None)
                    if existing is not None:
                        self._bytes -= existing.estimated_bytes
                    self._entries[entry_key] = _ContextEntry(
                        signature=after,
                        index=index,
                        estimated_bytes=estimated,
                    )
                    self._bytes += estimated
                    self._evict_locked()
            return index
        raise last_error or ToolError("context_stale", "Context index could not be verified against a stable source snapshot.")

    def _evict_locked(self) -> None:
        while self._entries and (len(self._entries) > self.max_entries or self._bytes > self.max_bytes):
            key, entry = self._entries.popitem(last=False)
            self._bytes -= entry.estimated_bytes
            if key not in self._active:
                self._generations.pop(key, None)

    def invalidate(self, path: str | Path | None = None) -> None:
        with self._lock:
            if path is None:
                self._generations = {key: self._generations.get(key, 0) + 1 for key in self._active}
                self._entries.clear()
                self._bytes = 0
                return
            target = canonical_path(path)
            prefix = target.rstrip("\\/") + os.sep
            affected = [
                key
                for key in set(self._entries) | set(self._generations) | set(self._active)
                if key[0] == target or key[0].startswith(prefix) or target.startswith(key[0].rstrip("\\/") + os.sep)
            ]
            for key in affected:
                if key in self._active:
                    self._generations[key] = self._generations.get(key, 0) + 1
                else:
                    self._generations.pop(key, None)
                entry = self._entries.pop(key, None)
                if entry is not None:
                    self._bytes -= entry.estimated_bytes

    def clear(self) -> None:
        self.invalidate()


CONTEXT_CACHE = ContextIndexCache()


def build_context_index(root: Path, max_files: int = 5_000) -> ContextIndex:
    return CONTEXT_CACHE.get(root, max_files)


def _definition_row(item: Definition, evidence: Evidence) -> dict[str, Any]:
    return {
        "file": item.file,
        "module": item.module,
        "qualified_name": item.qualified_name,
        "kind": item.kind,
        "line": item.line,
        "end_line": item.end_line,
        "signature": item.signature,
        "evidence": evidence,
        "confidence": _confidence(evidence),
        "source_kind": SOURCE_KIND,
    }


def _resolve_call(index: ContextIndex, file: IndexedFile, call: CallMetadata) -> tuple[Definition | None, Evidence]:
    imports = dict(file.imports)
    first, dot, remainder = call.name.partition(".")
    if first in imports:
        candidate = imports[first] + (dot + remainder if remainder else "")
        match = next((item for item in index.definitions if item.qualified_name == candidate), None)
        return match, "import_bound"
    same_module = f"{file.module}.{call.name}"
    match = next((item for item in index.definitions if item.qualified_name == same_module), None)
    if match is not None:
        return match, "same_module"
    exact = next((item for item in index.definitions if item.qualified_name == call.name), None)
    if exact is not None:
        return exact, "exact_qualified"
    short_matches = [item for item in index.definitions if item.qualified_name.rsplit(".", 1)[-1] == call.name.rsplit(".", 1)[-1]]
    return (short_matches[0], "name_only") if len(short_matches) == 1 else (None, "name_only")


def _enclosing_definition(file: IndexedFile, line: int) -> Definition | None:
    candidates: list[Definition] = []
    for item in file.metadata.functions:
        if item.line <= line <= (item.end_line or item.line):
            candidates.append(
                Definition(
                    file=file.relative,
                    module=file.module,
                    qualified_name=f"{file.module}.{item.qualified_name}",
                    local_name=item.qualified_name,
                    kind="function",
                    line=item.line,
                    end_line=item.end_line,
                    signature=item.signature,
                )
            )
    for class_item in file.metadata.classes:
        if class_item.line <= line <= (class_item.end_line or class_item.line):
            candidates.append(
                Definition(
                    file=file.relative,
                    module=file.module,
                    qualified_name=f"{file.module}.{class_item.qualified_name}",
                    local_name=class_item.qualified_name,
                    kind="class",
                    line=class_item.line,
                    end_line=class_item.end_line,
                    signature=None,
                )
            )
    return min(candidates, key=lambda item: ((item.end_line or item.line) - item.line, -item.line), default=None)


def _is_test_file(path: str) -> bool:
    parts = {part.casefold() for part in Path(path).parts}
    name = Path(path).name.casefold()
    return "tests" in parts or "test" in parts or name.startswith("test_") or name.endswith("_test.py")


def _identity(item: Definition) -> Identity:
    return (item.file, item.qualified_name, item.line, item.kind)


def _module_identity(file: IndexedFile) -> Identity:
    return (file.relative, f"<module:{file.module}>", 0, "module")


@dataclass(frozen=True, slots=True)
class _CallEdge:
    source: Identity
    target: Identity
    file: str
    line: int
    call: str
    evidence: Evidence
    source_definition: Definition | None
    target_definition: Definition


@dataclass(slots=True)
class _TraversalBudget:
    deadline: float
    vertices: int = 0
    truncated: bool = False
    reason: str | None = None

    def add_vertex(self) -> bool:
        if self.vertices >= MAX_TRAVERSAL_VERTICES:
            self.truncated = True
            self.reason = self.reason or "vertex_budget"
            return False
        self.vertices += 1
        return True

    def expire(self) -> bool:
        if time.monotonic() > self.deadline:
            self.truncated = True
            self.reason = self.reason or "time_budget"
            return True
        return False


def _build_call_adjacency(
    index: ContextIndex, deadline: float
) -> tuple[dict[Identity, tuple[_CallEdge, ...]], dict[Identity, tuple[_CallEdge, ...]], _TraversalBudget]:
    budget = _TraversalBudget(deadline)
    incoming: dict[Identity, list[_CallEdge]] = {}
    outgoing: dict[Identity, list[_CallEdge]] = {}
    scanned = 0
    stopped = False
    for file in index.files:
        for call in file.metadata.calls:
            if scanned % TRAVERSAL_BATCH == 0 and time.monotonic() > deadline:
                budget.truncated = True
                budget.reason = budget.reason or "time_budget"
                stopped = True
                break
            if scanned >= MAX_TRAVERSAL_EDGES:
                budget.truncated = True
                budget.reason = budget.reason or "edge_budget"
                stopped = True
                break
            scanned += 1
            target, evidence = _resolve_call(index, file, call)
            if target is None:
                continue
            owner = _enclosing_definition(file, call.line)
            edge = _CallEdge(
                source=_identity(owner) if owner is not None else _module_identity(file),
                target=_identity(target),
                file=file.relative,
                line=call.line,
                call=call.name,
                evidence=evidence,
                source_definition=owner,
                target_definition=target,
            )
            outgoing.setdefault(edge.source, []).append(edge)
            incoming.setdefault(edge.target, []).append(edge)
        if stopped:
            break

    def edge_key(edge: _CallEdge) -> tuple[str, int, str, Identity, Identity]:
        return (edge.file, edge.line, edge.call, edge.source, edge.target)

    return (
        {identity: tuple(sorted(edges, key=edge_key)) for identity, edges in incoming.items()},
        {identity: tuple(sorted(edges, key=edge_key)) for identity, edges in outgoing.items()},
        budget,
    )


def _traverse(
    starts: list[Definition],
    adjacency: dict[Identity, tuple[_CallEdge, ...]],
    direction: Literal["incoming", "outgoing"],
    max_depth: int,
    budget: _TraversalBudget,
) -> list[tuple[_CallEdge, int]]:
    if max_depth <= 0:
        return []
    queue: deque[tuple[Identity, int]] = deque()
    best: dict[Identity, int] = {}
    for item in starts:
        identity = _identity(item)
        if identity in best:
            continue
        if not budget.add_vertex():
            return []
        best[identity] = 0
        queue.append((identity, 0))
    results: list[tuple[_CallEdge, int]] = []
    processed = 0
    while queue:
        if not budget.truncated and processed % TRAVERSAL_BATCH == 0 and budget.expire():
            break
        processed += 1
        vertex, distance = queue.popleft()
        if distance >= max_depth:
            continue
        for edge in adjacency.get(vertex, ()):
            hop = distance + 1
            results.append((edge, hop))
            if hop < max_depth and edge.evidence != "name_only":
                neighbor = edge.source if direction == "incoming" else edge.target
                if neighbor in best:
                    continue
                if not budget.add_vertex():
                    return results
                best[neighbor] = hop
                queue.append((neighbor, hop))
    return results


def lookup_code_context(
    root: Path,
    symbol: str,
    *,
    match: Literal["exact", "contains"] = "exact",
    file_filter: str | None = None,
    file_filter_exact: bool = False,
    include_source: bool = True,
    include_callers: bool = True,
    include_callees: bool = True,
    include_references: bool = True,
    include_imports: bool = True,
    include_tests: bool = True,
    max_depth: int = 1,
    max_files: int = 5_000,
    max_relationships: int = 100,
    source_max_chars: int = 20_000,
    offset: int = 0,
) -> dict[str, Any]:
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth not in (0, 1, 2):
        raise ValueError("max_depth must be an integer between 0 and 2")
    index = build_context_index(root, max_files)
    query = symbol.casefold()
    exact_filter = file_filter.casefold().replace("\\", "/") if file_filter and file_filter_exact else None

    def matches(item: Definition) -> bool:
        if file_filter:
            if exact_filter is not None:
                if item.file.casefold().replace("\\", "/") != exact_filter:
                    return False
            elif file_filter.casefold() not in item.file.casefold():
                return False
        full = item.qualified_name.casefold()
        local = item.local_name.casefold()
        short = item.qualified_name.rsplit(".", 1)[-1].casefold()
        return query in full if match == "contains" else query in {full, local, short}

    matched = [item for item in index.definitions if matches(item)]
    definitions_truncated = len(matched) > MAX_CONTEXT_DEFINITIONS
    selected = matched[:MAX_CONTEXT_DEFINITIONS]
    definition_rows = [
        _definition_row(item, "exact_qualified" if item.qualified_name.casefold() == query else "name_only") for item in selected
    ]
    callers: list[dict[str, Any]] = []
    callees: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    seen_callers: set[tuple[str, int, str]] = set()
    seen_callees: set[str] = set()
    seen_tests: set[str] = set()
    traversal_truncated = False
    traversal_reason: str | None = None

    if max_depth >= 1 and (include_callers or include_callees or include_tests):
        deadline = time.monotonic() + TRAVERSAL_SECONDS
        incoming, outgoing, budget = _build_call_adjacency(index, deadline)
        traversal_truncated = budget.truncated
        traversal_reason = budget.reason
        if include_callers or include_tests:
            incoming_results = _traverse(selected, incoming, "incoming", max_depth, budget)
            if include_callers:
                for edge, hop in incoming_results:
                    key = (edge.file, edge.line, edge.call)
                    if key in seen_callers:
                        continue
                    seen_callers.add(key)
                    callers.append(
                        {
                            "file": edge.file,
                            "line": edge.line,
                            "call": edge.call,
                            "qualified_name": edge.source_definition.qualified_name if edge.source_definition else None,
                            "evidence": edge.evidence,
                            "confidence": _confidence(edge.evidence),
                            "source_kind": SOURCE_KIND,
                            "depth": hop,
                            "via_qualified_name": edge.target_definition.qualified_name,
                        }
                    )
            if include_tests:
                for edge, hop in incoming_results:
                    if not _is_test_file(edge.file) or edge.file in seen_tests:
                        continue
                    seen_tests.add(edge.file)
                    tests.append(
                        {
                            "file": edge.file,
                            "line": edge.line,
                            "evidence": edge.evidence,
                            "confidence": _confidence(edge.evidence),
                            "source_kind": SOURCE_KIND,
                            "depth": hop,
                            "via_qualified_name": edge.target_definition.qualified_name,
                        }
                    )
        if include_callees:
            for edge, hop in _traverse(selected, outgoing, "outgoing", max_depth, budget):
                if edge.target_definition.qualified_name in seen_callees:
                    continue
                seen_callees.add(edge.target_definition.qualified_name)
                row = _definition_row(edge.target_definition, edge.evidence)
                row["depth"] = hop
                row["via_qualified_name"] = edge.source_definition.qualified_name if edge.source_definition else None
                callees.append(row)
        traversal_truncated = traversal_truncated or budget.truncated
        traversal_reason = budget.reason or traversal_reason

    if max_depth >= 1:
        for file in index.files:
            if include_references:
                for reference in file.metadata.references:
                    if reference.context == "load" and reference.name.casefold() == symbol.rsplit(".", 1)[-1].casefold():
                        references.append(
                            {
                                "file": file.relative,
                                "line": reference.line,
                                "column": reference.column,
                                "name": reference.name,
                                "evidence": "name_only",
                                "confidence": _confidence("name_only"),
                                "source_kind": SOURCE_KIND,
                                "depth": 1,
                                "via_qualified_name": None,
                            }
                        )
            if include_imports and any(item.module == file.module for item in selected):
                imports.extend(
                    {
                        "file": file.relative,
                        "local": local,
                        "target": target,
                        "evidence": "import_bound",
                        "confidence": _confidence("import_bound"),
                        "source_kind": SOURCE_KIND,
                        "depth": 1,
                        "via_qualified_name": None,
                    }
                    for local, target in file.imports
                )

    source_remaining = source_max_chars
    if include_source:
        for row, item in zip(definition_rows, selected, strict=True):
            if source_remaining <= 0:
                row["source_truncated"] = True
                continue
            indexed = next(file for file in index.files if file.relative == item.file)
            if file_version(indexed.path) != indexed.version:
                raise ToolError("context_stale", "Python source changed after the context index snapshot.")
            lines = indexed.path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            excerpt = "".join(lines[item.line - 1 : item.end_line])
            row["source"] = excerpt[:source_remaining]
            row["source_truncated"] = len(excerpt) > source_remaining
            source_remaining -= len(row["source"])

    categories = ("callers", "callees", "references", "imports", "tests")
    groups = (callers, callees, references, imports, tests)
    tagged = [(category, row) for category, rows in zip(categories, groups, strict=True) for row in rows]
    page = tagged[offset : offset + max_relationships]
    paged: dict[str, list[dict[str, Any]]] = {category: [] for category in categories}
    for category, row in page:
        paged[category].append(row)
    callers, callees, references, imports, tests = (paged[category] for category in categories)
    relationships_truncated = offset + len(page) < len(tagged)
    incomplete_reasons: list[str] = []
    if index.scan_truncated:
        incomplete_reasons.append("scan_truncated")
    if relationships_truncated:
        incomplete_reasons.append("relationships_truncated")
    if index.parse_errors:
        incomplete_reasons.append("parse_errors")
    if any(row.get("source_truncated") for row in definition_rows):
        incomplete_reasons.append("source_truncated")
    if definitions_truncated:
        incomplete_reasons.append("definitions_truncated")
    if traversal_truncated:
        incomplete_reasons.append(traversal_reason or "traversal_truncated")
    if index.outside_root:
        incomplete_reasons.append("symlink_outside_root")
    if index.deadline_exceeded:
        incomplete_reasons.append("index_deadline")
    if index.freshness != "current":
        incomplete_reasons.append("context_stale")
    confidence_summary: dict[str, int] = {}
    for _category, row in page:
        confidence = str(row.get("confidence", "inferred"))
        confidence_summary[confidence] = confidence_summary.get(confidence, 0) + 1
    missing_inputs = list(index.inventory.missing_inputs) if index.inventory is not None else []
    indexed_files = {file.relative: file for file in index.files}
    root_digest = hashlib.sha256(canonical_path(index.root).encode("utf-8")).hexdigest()
    for row, definition in zip(definition_rows, selected, strict=True):
        indexed = indexed_files[definition.file]
        try:
            relative_uri = indexed.path.relative_to(index.inventory.root).as_posix() if index.inventory else definition.file
            row["symbol_key"] = asdict(SymbolKey(
                canonical_workspace=canonical_path(index.inventory.root if index.inventory else index.root), language_id="python",
                relative_uri=quote(relative_uri, safe="/"),
                qualified_name=definition.qualified_name,
                kind=definition.kind, start_line=definition.line, start_character=None,
                content_digest=indexed.version.content_sha256, coordinate_system="python_legacy",
            ))
            row["snapshot"] = asdict(SnapshotEvidence(
                source_sha=None,
                worktree_id=index.inventory.worktree_id if index.inventory else root_digest,
                document_digest=indexed.version.content_sha256, document_version=None,
                index_generation=index.snapshot_id or root_digest,
                provider_id=None, source_kind="ast",
            ))
        except ValidationError:
            row["symbol_key"] = row["snapshot"] = None
            missing_inputs.append("symbol_identity_unavailable")
            incomplete_reasons.append("symbol_identity_unavailable")
    complete = not incomplete_reasons
    requested_relations: dict[str, bool] = {"definition": True}
    if max_depth >= 1:
        requested_relations.update({
            "caller": include_callers, "callee": include_callees, "reference": include_references,
            "import": include_imports, "related_test": include_tests,
        })
    return {
        "ok": True,
        "root": str(index.root),
        "symbol": symbol,
        "ambiguous": len(matched) > 1,
        "definitions": definition_rows,
        "callers": callers,
        "callees": callees,
        "references": references,
        "imports": imports,
        "tests": tests,
        "files_scanned": len(index.files),
        "scan_truncated": index.scan_truncated,
        "relationships_truncated": relationships_truncated,
        "complete": complete,
        "incomplete_reasons": incomplete_reasons,
        "query_depth": max_depth,
        "traversal_truncated": traversal_truncated,
        "total_exact": not (definitions_truncated or traversal_truncated or index.deadline_exceeded),
        "snapshot_id": index.snapshot_id,
        "freshness": index.freshness,
        # Static AST evidence cannot prove runtime dispatch, so semantic completeness stays false until LSP evidence exists.
        "semantic_complete": False,
        "language_id": "python",
        "backend": "ast",
        "snapshot": definition_rows[0].get("snapshot") if len(definition_rows) == 1 else None,
        "completeness_by_relation": {kind: complete for kind, requested in requested_relations.items() if requested},
        "missing_inputs": sorted(set(missing_inputs)),
        "fallback_reason": None,
        "version": 1,
        "confidence_summary": confidence_summary,
        "parse_errors": list(index.parse_errors),
        "count": len(page),
        "offset": offset,
        "limit": max_relationships,
        "total": len(tagged),
    }
