"""Bounded, evidence-qualified Python code context lookup."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from core.resource_locks import canonical_path
from core.singleflight import SingleFlight
from tools.project import service
from tools.project.index import CallMetadata, PythonFileMetadata

Evidence = Literal["exact_qualified", "import_bound", "same_module", "name_only"]
EVIDENCE_RANK: dict[Evidence, int] = {
    "exact_qualified": 0,
    "import_bound": 1,
    "same_module": 2,
    "name_only": 3,
}


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


@dataclass(frozen=True, slots=True)
class ContextIndex:
    root: Path
    files: tuple[IndexedFile, ...]
    definitions: tuple[Definition, ...]
    scan_truncated: bool
    parse_errors: tuple[dict[str, str], ...]


@dataclass(slots=True)
class _ContextEntry:
    signature: tuple[tuple[str, int, int], ...]
    index: ContextIndex


def _file_signature(files: tuple[Path, ...]) -> tuple[tuple[str, int, int], ...]:
    rows: list[tuple[str, int, int]] = []
    for path in files:
        stat = path.stat()
        rows.append((canonical_path(path), stat.st_mtime_ns, stat.st_size))
    return tuple(rows)


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


def _build_context_index(root: Path, files: tuple[Path, ...], scan_truncated: bool) -> ContextIndex:
    indexed_files: list[IndexedFile] = []
    definitions: list[Definition] = []
    parse_errors: list[dict[str, str]] = []
    for path in files:
        metadata = service.python_metadata(path)
        relative = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
        module = service.module_name(root, path) if root.is_dir() else path.stem
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
    return ContextIndex(
        root=root,
        files=tuple(sorted(indexed_files, key=lambda item: item.relative.casefold())),
        definitions=tuple(sorted(definitions, key=lambda item: (item.qualified_name.casefold(), item.file, item.line))),
        scan_truncated=scan_truncated,
        parse_errors=tuple(parse_errors),
    )


class ContextIndexCache:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[tuple[str, int], _ContextEntry] = {}
        self._flights: SingleFlight[tuple[str, int, tuple[tuple[str, int, int], ...]], ContextIndex] = SingleFlight()

    def get(self, root: Path, max_files: int) -> ContextIndex:
        resolved = root.resolve(strict=False)
        discovered, truncated = service.iter_python_files(resolved, max_files)
        files = tuple(sorted(discovered, key=lambda path: canonical_path(path)))
        signature = _file_signature(files)
        entry_key = (canonical_path(resolved), max_files)
        with self._lock:
            entry = self._entries.get(entry_key)
            if entry is not None and entry.signature == signature:
                return entry.index
        flight_key = (*entry_key, signature)

        def build() -> ContextIndex:
            with self._lock:
                current = self._entries.get(entry_key)
                if current is not None and current.signature == signature:
                    return current.index
            index = _build_context_index(resolved, files, truncated)
            with self._lock:
                self._entries[entry_key] = _ContextEntry(signature=signature, index=index)
            return index

        return self._flights.run(flight_key, build)

    def invalidate(self, path: str | Path | None = None) -> None:
        with self._lock:
            if path is None:
                self._entries.clear()
                return
            target = canonical_path(path)
            self._entries = {
                key: entry
                for key, entry in self._entries.items()
                if not (target == key[0] or target.startswith(key[0].rstrip("\\/") + "\\") or target.startswith(key[0].rstrip("\\/") + "/"))
            }

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


def lookup_code_context(
    root: Path,
    symbol: str,
    *,
    match: Literal["exact", "contains"] = "exact",
    file_filter: str | None = None,
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
    del max_depth  # Depth two is reserved for a later LSP-backed resolver; direct static edges are exact here.
    index = build_context_index(root, max_files)
    query = symbol.casefold()

    def matches(item: Definition) -> bool:
        if file_filter and file_filter.casefold() not in item.file.casefold():
            return False
        full = item.qualified_name.casefold()
        local = item.local_name.casefold()
        short = item.qualified_name.rsplit(".", 1)[-1].casefold()
        return query in full if match == "contains" else query in {full, local, short}

    selected = [item for item in index.definitions if matches(item)]
    definition_rows = [
        _definition_row(item, "exact_qualified" if item.qualified_name.casefold() == query else "name_only") for item in selected
    ]
    selected_names = {item.qualified_name for item in selected}
    callers: list[dict[str, Any]] = []
    callees: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    seen_callers: set[tuple[str, int, str]] = set()
    seen_callees: set[str] = set()
    seen_tests: set[str] = set()

    for file in index.files:
        for call in file.metadata.calls:
            target, evidence = _resolve_call(index, file, call)
            owner = _enclosing_definition(file, call.line)
            if target is not None and target.qualified_name in selected_names and include_callers:
                key = (file.relative, call.line, call.name)
                if key not in seen_callers:
                    seen_callers.add(key)
                    callers.append(
                        {
                            "file": file.relative,
                            "line": call.line,
                            "call": call.name,
                            "qualified_name": owner.qualified_name if owner else None,
                            "evidence": evidence,
                        }
                    )
                    if include_tests and _is_test_file(file.relative) and file.relative not in seen_tests:
                        seen_tests.add(file.relative)
                        tests.append({"file": file.relative, "line": call.line, "evidence": evidence})
            if owner is not None and owner.qualified_name in selected_names and target is not None and include_callees:
                if target.qualified_name not in seen_callees:
                    seen_callees.add(target.qualified_name)
                    callees.append(_definition_row(target, evidence))
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
                        }
                    )
        if include_imports and any(item.module == file.module for item in selected):
            imports.extend({"file": file.relative, "local": local, "target": target} for local, target in file.imports)

    source_remaining = source_max_chars
    if include_source:
        for row, item in zip(definition_rows, selected, strict=True):
            if source_remaining <= 0:
                row["source_truncated"] = True
                continue
            indexed = next(file for file in index.files if file.relative == item.file)
            lines = indexed.path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            excerpt = "".join(lines[item.line - 1 : item.end_line])
            row["source"] = excerpt[:source_remaining]
            row["source_truncated"] = len(excerpt) > source_remaining
            source_remaining -= len(row["source"])

    relationships = [*callers, *callees, *references, *imports, *tests]
    relationships_truncated = len(relationships) > offset + max_relationships
    window_end = offset + max_relationships
    # Keep categories stable while applying one global relationship budget.
    budget = max_relationships

    def take(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        nonlocal budget
        if budget <= 0:
            return []
        result = rows[:budget]
        budget -= len(result)
        return result

    callers = take(callers[offset:] if offset < len(callers) else [])
    consumed = min(offset, len(callers))
    remaining_offset = max(offset - consumed, 0)
    callees = take(callees[remaining_offset:])
    references = take(references)
    imports = take(imports)
    tests = take(tests)
    complete = not index.scan_truncated and not relationships_truncated
    return {
        "ok": True,
        "root": str(index.root),
        "symbol": symbol,
        "ambiguous": len(selected) > 1,
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
        "parse_errors": list(index.parse_errors),
        "count": min(len(relationships), window_end) - min(len(relationships), offset),
        "offset": offset,
        "limit": max_relationships,
        "total": len(relationships),
    }
