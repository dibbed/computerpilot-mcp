"""Bounded, version-aware metadata cache for Python project analysis."""

from __future__ import annotations

import ast
import sys
import threading
import tokenize
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any

from core.config import SETTINGS
from core.errors import ToolError
from core.resource_locks import canonical_path

CACHE_ENTRY_OVERHEAD_BYTES = 256


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


@dataclass(frozen=True, slots=True)
class PythonFileMetadata:
    path: str
    functions: tuple[FunctionMetadata, ...]
    classes: tuple[ClassMetadata, ...]
    imports: tuple[ImportMetadata, ...]
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
                )
            )
        else:
            imports.append(
                ImportMetadata(
                    line=node.lineno,
                    source=node.module,
                    names=tuple(alias.name for alias in node.names),
                    level=node.level,
                )
            )

    return PythonFileMetadata(
        path=str(path),
        functions=tuple(functions),
        classes=tuple(classes),
        imports=tuple(imports),
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
    """LRU cache keyed by canonical path and validated by mtime_ns + size.

    Cache bookkeeping is thread-safe. C1 intentionally does not single-flight
    concurrent cache misses; duplicate in-flight parses are addressed in C2.
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

    @staticmethod
    def _version(path: Path) -> FileVersion:
        stat = path.stat()
        return FileVersion(mtime_ns=stat.st_mtime_ns, size=stat.st_size)

    def get(self, path: Path) -> PythonFileMetadata:
        target = path.resolve(strict=False)
        version = self._version(target)
        key = canonical_path(target)
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None and cached.version == version:
                self._entries.move_to_end(key)
                return cached.metadata
            if cached is not None:
                self._entries.pop(key)
                self._bytes -= cached.estimated_bytes

        metadata: PythonFileMetadata | None = None
        for _attempt in range(3):
            metadata = self._loader(target)
            after = self._version(target)
            if after == version:
                break
            version = after
        else:
            raise ToolError(
                "project_file_changed_during_parse",
                f"Python source changed repeatedly while being indexed: {target}",
            )
        assert metadata is not None

        estimated = _deep_size(metadata) + _deep_size(version) + sys.getsizeof(key) + CACHE_ENTRY_OVERHEAD_BYTES
        if estimated > self.max_bytes:
            return metadata

        with self._lock:
            existing = self._entries.pop(key, None)
            if existing is not None:
                self._bytes -= existing.estimated_bytes
            self._entries[key] = _CacheEntry(version=version, metadata=metadata, estimated_bytes=estimated)
            self._bytes += estimated
            self._evict_locked()
        return metadata

    def _evict_locked(self) -> None:
        while self._entries and (len(self._entries) > self.max_files or self._bytes > self.max_bytes):
            _, entry = self._entries.popitem(last=False)
            self._bytes -= entry.estimated_bytes

    def clear(self) -> None:
        with self._lock:
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
