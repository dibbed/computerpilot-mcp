"""Bounded, version-aware metadata cache for Python project analysis."""

from __future__ import annotations

import ast
import os
import sys
import threading
import tokenize
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, Literal

from core.config import SETTINGS
from core.errors import ToolError
from core.resource_locks import canonical_path
from core.singleflight import SingleFlight

CACHE_ENTRY_OVERHEAD_BYTES = 256
MAX_PARSE_VERSION_RETRIES = 3


@dataclass(frozen=True, slots=True)
class FileVersion:
    mtime_ns: int
    size: int


@dataclass(frozen=True, slots=True)
class FunctionMetadata:
    qualified_name: str
    line: int
    end_line: int | None
    is_async: bool
    signature: str | None


@dataclass(frozen=True, slots=True)
class ClassMetadata:
    qualified_name: str
    line: int
    end_line: int | None
    bases: tuple[str, ...]
    method_count: int


@dataclass(frozen=True, slots=True)
class ImportMetadata:
    line: int
    source: str | None
    names: tuple[str, ...]
    level: int
    bindings: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class NameReference:
    name: str
    line: int
    column: int
    context: Literal["load", "store"]


@dataclass(frozen=True, slots=True)
class CallMetadata:
    name: str
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class PythonFileMetadata:
    path: str
    functions: tuple[FunctionMetadata, ...]
    classes: tuple[ClassMetadata, ...]
    imports: tuple[ImportMetadata, ...]
    references: tuple[NameReference, ...]
    calls: tuple[CallMetadata, ...]
    parse_error: str | None = None


@dataclass(slots=True)
class _CacheEntry:
    version: FileVersion
    metadata: PythonFileMetadata
    estimated_bytes: int


def _walk_qualified(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    found: list[tuple[str, ast.AST]] = []

    def visit(body: list[ast.stmt], prefix: str = "") -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified = f"{prefix}.{node.name}" if prefix else node.name
                found.append((qualified, node))
                visit(node.body, qualified)

    visit(tree.body)
    return found


def _dotted_name(node: ast.AST, *, max_parts: int = 32) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute) and len(parts) < max_parts:
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def build_python_metadata(path: Path) -> PythonFileMetadata:
    """Parse one Python file into compact metadata instead of retaining its AST."""

    try:
        with tokenize.open(path) as handle:
            tree = ast.parse(handle.read(), filename=str(path))
    except (SyntaxError, UnicodeError, OSError) as exc:
        return PythonFileMetadata(
            path=str(path),
            functions=(),
            classes=(),
            imports=(),
            references=(),
            calls=(),
            parse_error=f"Cannot parse {path}: {exc}",
        )

    functions: list[FunctionMetadata] = []
    classes: list[ClassMetadata] = []
    for qualified, node in _walk_qualified(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            try:
                signature = f"({ast.unparse(node.args)})"
            except Exception:
                signature = None
            functions.append(
                FunctionMetadata(
                    qualified_name=qualified,
                    line=node.lineno,
                    end_line=node.end_lineno,
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                    signature=signature,
                )
            )
        elif isinstance(node, ast.ClassDef):
            classes.append(
                ClassMetadata(
                    qualified_name=qualified,
                    line=node.lineno,
                    end_line=node.end_lineno,
                    bases=tuple(ast.unparse(base) for base in node.bases[:10]),
                    method_count=sum(isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) for child in node.body),
                )
            )

    imports: list[ImportMetadata] = []
    import_nodes = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    for node in sorted(import_nodes, key=lambda item: item.lineno):
        if isinstance(node, ast.Import):
            imports.append(
                ImportMetadata(
                    line=node.lineno,
                    source=None,
                    names=tuple(alias.name for alias in node.names),
                    level=0,
                    bindings=tuple((alias.asname or alias.name.split(".")[0], alias.name) for alias in node.names),
                )
            )
        else:
            source = node.module or ""
            imports.append(
                ImportMetadata(
                    line=node.lineno,
                    source=node.module,
                    names=tuple(alias.name for alias in node.names),
                    level=node.level,
                    bindings=tuple(
                        (
                            alias.asname or alias.name,
                            ".".join(part for part in (source, alias.name) if part),
                        )
                        for alias in node.names
                        if alias.name != "*"
                    ),
                )
            )

    references: list[NameReference] = []
    calls: list[CallMetadata] = []
    seen_references: set[tuple[str, int, int, str]] = set()
    seen_calls: set[tuple[str, int, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            context: Literal["load", "store"] = "store" if isinstance(node.ctx, (ast.Store, ast.Del)) else "load"
            reference_key = (node.id, node.lineno, node.col_offset, context)
            if reference_key not in seen_references:
                seen_references.add(reference_key)
                references.append(NameReference(name=node.id, line=node.lineno, column=node.col_offset, context=context))
        elif isinstance(node, ast.Call):
            name = _dotted_name(node.func)
            if name is not None:
                call_key = (name, node.lineno, node.col_offset)
                if call_key not in seen_calls:
                    seen_calls.add(call_key)
                    calls.append(CallMetadata(name=name, line=node.lineno, column=node.col_offset))

    return PythonFileMetadata(
        path=str(path),
        functions=tuple(functions),
        classes=tuple(classes),
        imports=tuple(imports),
        references=tuple(sorted(references, key=lambda item: (item.line, item.column, item.name, item.context))),
        calls=tuple(sorted(calls, key=lambda item: (item.line, item.column, item.name))),
    )


def _deep_size(value: Any, seen: set[int] | None = None) -> int:
    """Estimate retained Python-object bytes for enforcing a conservative cache budget."""

    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    total = sys.getsizeof(value)
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            total += _deep_size(getattr(value, field.name), seen)
    elif isinstance(value, (tuple, list, set, frozenset)):
        total += sum(_deep_size(item, seen) for item in value)
    elif isinstance(value, dict):
        total += sum(_deep_size(key, seen) + _deep_size(item, seen) for key, item in value.items())
    return total


class PythonMetadataCache:
    """Bounded LRU metadata cache with per-file-version single-flight parsing.

    Cache hits are validated by canonical path, mtime_ns, and size. Concurrent
    callers requesting the same unchanged file version share one parse, while
    unrelated files stay concurrent. Explicit MCP mutations can invalidate one
    file or a whole directory subtree immediately; external edits still fall
    back to version validation on the next read.
    """

    def __init__(
        self,
        *,
        max_files: int | None = None,
        max_bytes: int | None = None,
        loader: Callable[[Path], PythonFileMetadata] = build_python_metadata,
    ) -> None:
        self.max_files = SETTINGS.ast_cache_max_files if max_files is None else max_files
        self.max_bytes = SETTINGS.ast_cache_max_bytes if max_bytes is None else max_bytes
        if self.max_files < 1 or self.max_bytes < 1:
            raise ValueError("Python metadata cache budgets must be positive.")
        self._loader = loader
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._bytes = 0
        self._path_generations: dict[str, int] = {}
        self._active_loads: dict[str, int] = {}
        self._flights: SingleFlight[str, PythonFileMetadata] = SingleFlight()

    @staticmethod
    def _version(path: Path) -> FileVersion:
        stat = path.stat()
        return FileVersion(mtime_ns=stat.st_mtime_ns, size=stat.st_size)

    def _cached_locked(self, key: str, version: FileVersion) -> PythonFileMetadata | None:
        cached = self._entries.get(key)
        if cached is None:
            return None
        if cached.version != version:
            self._entries.pop(key)
            self._bytes -= cached.estimated_bytes
            return None
        self._entries.move_to_end(key)
        return cached.metadata

    def get(self, path: Path) -> PythonFileMetadata:
        target = path.resolve(strict=False)
        key = canonical_path(target)
        version = self._version(target)
        with self._lock:
            cached = self._cached_locked(key, version)
            if cached is not None:
                return cached
        return self._flights.run(key, lambda: self._load_stable(target, key))

    def _load_stable(self, target: Path, key: str) -> PythonFileMetadata:
        for _attempt in range(MAX_PARSE_VERSION_RETRIES):
            version = self._version(target)
            with self._lock:
                cached = self._cached_locked(key, version)
                if cached is not None:
                    return cached
                generation = self._path_generations.get(key, 0)
                self._active_loads[key] = self._active_loads.get(key, 0) + 1

            try:
                metadata = self._loader(target)
                after = self._version(target)
                with self._lock:
                    invalidated = self._path_generations.get(key, 0) != generation
                if after != version or invalidated:
                    continue

                estimated = _deep_size(metadata) + _deep_size(version) + sys.getsizeof(key) + CACHE_ENTRY_OVERHEAD_BYTES
                with self._lock:
                    if self._path_generations.get(key, 0) != generation:
                        continue
                    if estimated <= self.max_bytes:
                        existing = self._entries.pop(key, None)
                        if existing is not None:
                            self._bytes -= existing.estimated_bytes
                        self._entries[key] = _CacheEntry(version=version, metadata=metadata, estimated_bytes=estimated)
                        self._bytes += estimated
                        self._evict_locked()
                return metadata
            finally:
                with self._lock:
                    remaining = self._active_loads.get(key, 1) - 1
                    if remaining > 0:
                        self._active_loads[key] = remaining
                    else:
                        self._active_loads.pop(key, None)
                        self._path_generations.pop(key, None)

        raise ToolError(
            "project_file_changed_during_parse",
            f"Python source changed repeatedly while being indexed: {target}",
        )

    def _evict_locked(self) -> None:
        while self._entries and (len(self._entries) > self.max_files or self._bytes > self.max_bytes):
            _, entry = self._entries.popitem(last=False)
            self._bytes -= entry.estimated_bytes

    def invalidate(self, path: str | Path, *, recursive: bool = False) -> int:
        key = canonical_path(path)
        prefix = key.rstrip("\\/") + os.sep
        removed = 0
        with self._lock:
            candidates = set(self._entries) | set(self._active_loads)
            if recursive:
                affected = [candidate for candidate in candidates if candidate == key or candidate.startswith(prefix)]
            else:
                affected = [key] if key in candidates else []
            for affected_key in affected:
                self._path_generations[affected_key] = self._path_generations.get(affected_key, 0) + 1
                entry = self._entries.pop(affected_key, None)
                if entry is not None:
                    self._bytes -= entry.estimated_bytes
                    removed += 1
                if affected_key not in self._active_loads:
                    self._path_generations.pop(affected_key, None)
        return removed

    def clear(self) -> None:
        with self._lock:
            for key in self._active_loads:
                self._path_generations[key] = self._path_generations.get(key, 0) + 1
            self._entries.clear()
            self._bytes = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "bytes": self._bytes,
                "max_files": self.max_files,
                "max_bytes": self.max_bytes,
            }


PYTHON_METADATA_CACHE = PythonMetadataCache()
