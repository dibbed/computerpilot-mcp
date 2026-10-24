"""Read-only bounded package inventory, with caller-selected workspace containment."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

try:
    import tomllib as _toml
except ImportError:  # Python 3.10 retains marker/hash evidence without a TOML parser.
    _toml = None

from core.config import resolve_path
from core.errors import ToolError
from core.resource_locks import canonical_path
from tools.project.intelligence_models import LanguageId
from tools.project.service import EXCLUDED_DIRS

_MAX_MANIFESTS = 256
_MAX_MANIFEST_BYTES = 256 * 1024
_MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024
_MAX_SOURCE_BYTES = 16 * 1024 * 1024
_MAX_ENTRIES = 10_000
_MAX_DEPTH = 32
_INVENTORY_SECONDS = 5.0
_MANIFESTS: dict[str, Literal["python", "node", "php"]] = {
    "pyproject.toml": "python", "package.json": "node", "composer.json": "php",
}
_FINGERPRINT_FILES = frozenset({"tsconfig.json", "jsconfig.json"})
_LANGUAGES: dict[str, LanguageId] = {
    ".py": "python", ".pyi": "python", ".ts": "typescript", ".tsx": "typescriptreact",
    ".js": "javascript", ".jsx": "javascriptreact", ".mjs": "javascript", ".cjs": "javascript", ".php": "php",
}


@dataclass(frozen=True, slots=True)
class ManifestInfo:
    path: Path
    kind: Literal["python", "node", "php"]
    sha256: str
    name: str | None = None
    workspace_patterns: tuple[str, ...] = ()
    version: Literal[1] = 1


@dataclass(frozen=True, slots=True)
class PackageInfo:
    root: Path
    manifests: tuple[ManifestInfo, ...]
    version: Literal[1] = 1


@dataclass(frozen=True, slots=True)
class ProjectInventory:
    root: Path
    packages: tuple[PackageInfo, ...]
    worktree_id: str
    language_ids: tuple[LanguageId, ...]
    complete: bool
    missing_inputs: tuple[str, ...]
    version: Literal[1] = 1
    source_fingerprint: str | None = None


def language_for_path(path: Path) -> LanguageId | Literal["unsupported"]:
    return _LANGUAGES.get(path.suffix.casefold(), "unsupported")


def _error(code: str, path: Path, root: Path) -> ToolError:
    relative = path.relative_to(root).as_posix() if path.is_relative_to(root) else "outside_workspace"
    return ToolError(code, f"Project inventory input is unavailable or invalid: {relative}")


def _read_bytes(path: Path, root: Path, maximum: int = _MAX_MANIFEST_BYTES) -> bytes:
    try:
        if not path.resolve(strict=False).is_relative_to(root):
            raise _error("project_scope_escape", path, root)
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode) or before.st_size > maximum
            or int(getattr(before, "st_file_attributes", 0)) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise _error("invalid_project_manifest", path, root)
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            data = stream.read(maximum + 1)
            finished = os.fstat(stream.fileno())
        after = path.lstat()
        identities = {(item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns) for item in (before, opened, finished, after)}
        if len(identities) != 1 or len(data) != before.st_size or len(data) > maximum:
            raise _error("invalid_project_manifest", path, root)
        if not path.resolve(strict=False).is_relative_to(root):
            raise _error("project_scope_escape", path, root)
        return data
    except OSError:
        raise _error("invalid_project_manifest", path, root) from None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate manifest key")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError("Non-finite manifest number")


def _workspace_patterns(value: object) -> tuple[str, ...]:
    if isinstance(value, dict):
        value = value.get("packages", [])
    if not isinstance(value, list) or len(value) > _MAX_MANIFESTS:
        raise ValueError("Invalid workspace patterns")
    patterns: list[str] = []
    for pattern in value:
        if not isinstance(pattern, str) or not 1 <= len(pattern) <= 32_767:
            raise ValueError("Invalid workspace pattern")
        parts = pattern.split("/")
        if (
            PurePosixPath(pattern).is_absolute() or "\\" in pattern or ":" in pattern or "\x00" in pattern
            or any(token in pattern for token in ("?", "[", "]", "{", "}", "!"))
            or any(part in {"", ".", ".."} or ("*" in part and part != "*") for part in parts)
            or parts.count("*") > 1
        ):
            raise ValueError("Workspace pattern leaves the supported bounded subset")
        patterns.append(pattern)
    return tuple(patterns)


def _manifest(path: Path, root: Path) -> tuple[ManifestInfo, str | None]:
    data = _read_bytes(path, root)
    kind = _MANIFESTS[path.name]
    digest = hashlib.sha256(data).hexdigest()
    if kind == "python" and _toml is None:
        return ManifestInfo(path, kind, digest), "toml_parser_unavailable"
    try:
        text = data.decode("utf-8", errors="strict")
        parsed = _toml.loads(text) if kind == "python" and _toml is not None else json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant,
        )
        if not isinstance(parsed, dict):
            raise ValueError("Manifest must be an object")
        metadata = parsed.get("project", {}) if kind == "python" else parsed
        if not isinstance(metadata, dict):
            raise ValueError("Invalid project metadata")
        name = metadata.get("name")
        if name is not None and (not isinstance(name, str) or not 1 <= len(name) <= 500):
            raise ValueError("Invalid package name")
        patterns = _workspace_patterns(parsed.get("workspaces", [])) if kind == "node" else ()
    except (ValueError, TypeError, RecursionError):
        raise _error("invalid_project_manifest", path, root) from None
    return ManifestInfo(path, kind, digest, name, patterns), None


def _worktree_identity(root: Path) -> str:
    marker = root / ".git"
    if marker.is_file():
        identity = "file:" + hashlib.sha256(_read_bytes(marker, root, 4096)).hexdigest()
    elif marker.is_dir():
        info = marker.lstat()
        if stat.S_ISLNK(info.st_mode) or int(getattr(info, "st_file_attributes", 0)) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise _error("invalid_project_manifest", marker, root)
        identity = f"dir:{info.st_dev}:{info.st_ino}:{info.st_mtime_ns if not info.st_ino else 0}"
    else:
        identity = "no-git"
    return hashlib.sha256((canonical_path(root) + "\0" + identity).encode("utf-8")).hexdigest()


def _directory_identity(path: Path, root: Path, expected: tuple[int, int, int] | None = None) -> tuple[int, int, int]:
    info = path.lstat()
    resolved = path.resolve(strict=False)
    if (
        not resolved.is_relative_to(root) or resolved != path or not stat.S_ISDIR(info.st_mode)
        or int(getattr(info, "st_file_attributes", 0)) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise _error("project_scope_escape", path, root)
    identity = (info.st_dev, info.st_ino, info.st_ctime_ns if not info.st_ino else 0)
    if expected is not None and identity != expected:
        raise _error("invalid_project_manifest", path, root)
    return identity


def detect_project_types(root: Path, *, max_manifests: int = 256, fingerprint_sources: bool = False) -> ProjectInventory:
    if type(max_manifests) is not int or not 1 <= max_manifests <= _MAX_MANIFESTS:
        raise ToolError("inventory_limit", "Manifest admission must be between 1 and 256.")
    if type(fingerprint_sources) is not bool:
        raise ToolError("inventory_limit", "Source fingerprint selection must be a boolean.")
    root = resolve_path(root)
    if not root.is_dir():
        raise ToolError("project_scope_escape", "The selected project root must be an existing directory.")
    deadline = time.monotonic() + _INVENTORY_SECONDS
    try:
        root_identity = _directory_identity(root, root)
    except (OSError, RuntimeError):
        raise ToolError("project_scope_escape", "The selected project root is no longer available.") from None
    pending = deque([(root, 0, root_identity)])
    packages: dict[Path, list[ManifestInfo]] = {}
    languages: set[LanguageId] = set()
    missing: list[str] = []
    visited = 0
    manifests = 0
    fingerprint_available = fingerprint_sources
    source_bytes = 0
    fingerprint_entries: list[tuple[str, str, str]] = []
    scanned_directories: list[tuple[Path, tuple[int, int, int], int, int]] = []

    def incomplete(code: str, path: Path) -> None:
        if len(missing) < 1_000:
            relative = path.relative_to(root).as_posix() if path.is_relative_to(root) else "outside_workspace"
            missing.append(f"{code}:{relative}")

    try:
        worktree_id = _worktree_identity(root)
    except (OSError, ToolError):
        incomplete("invalid_project_manifest", root / ".git")
        worktree_id = hashlib.sha256(canonical_path(root).encode("utf-8")).hexdigest()
    exhausted = False
    while pending and not exhausted:
        current, depth, identity = pending.popleft()
        if time.monotonic() >= deadline:
            incomplete("inventory_limit", current)
            break
        entries: list[Path] = []
        try:
            _directory_identity(current, root, identity)
            with os.scandir(current) as scanner:
                # Revalidate after open before consuming names from a raced directory alias.
                _directory_identity(current, root, identity)
                if fingerprint_available:
                    scanned = current.stat()
                    scanned_directories.append((current, identity, scanned.st_mtime_ns, scanned.st_ctime_ns))
                for entry in scanner:
                    _directory_identity(current, root, identity)
                    visited += 1
                    if visited > _MAX_ENTRIES or time.monotonic() >= deadline:
                        incomplete("inventory_limit", current)
                        exhausted = True
                        break
                    entries.append(Path(entry.path))
                _directory_identity(current, root, identity)
        except ToolError as error:
            incomplete(error.code, current)
            continue
        except (OSError, RuntimeError):
            incomplete("invalid_project_manifest", current)
            continue
        for path in sorted(entries, key=lambda item: item.name.casefold()):
            if time.monotonic() >= deadline:
                incomplete("inventory_limit", current)
                exhausted = True
                break
            try:
                _directory_identity(current, root, identity)
            except ToolError as error:
                incomplete(error.code, current)
                break
            except (OSError, RuntimeError):
                incomplete("invalid_project_manifest", current)
                break
            try:
                info = path.lstat()
                is_directory = path.is_dir()
                if is_directory and (path.name in EXCLUDED_DIRS or path.name.startswith(".pytest-tmp-")):
                    continue
                if not path.resolve(strict=True).is_relative_to(root):
                    incomplete("project_scope_escape", current)
                    continue
                linked = stat.S_ISLNK(info.st_mode) or bool(
                    int(getattr(info, "st_file_attributes", 0)) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                )
                if linked:
                    incomplete("invalid_project_manifest", current)
                    continue
                if is_directory:
                    if depth >= _MAX_DEPTH:
                        incomplete("inventory_limit", current)
                    else:
                        pending.append((path, depth + 1, _directory_identity(path, root)))
                    continue
                language = language_for_path(path)
                if language != "unsupported":
                    languages.add(language)
                    if fingerprint_available:
                        try:
                            data = _read_bytes(path, root, min(_MAX_SOURCE_FILE_BYTES, _MAX_SOURCE_BYTES - source_bytes))
                            data.decode("utf-8", errors="strict")
                            source_bytes += len(data)
                            fingerprint_entries.append(("source", path.relative_to(root).as_posix(), hashlib.sha256(data).hexdigest()))
                            if time.monotonic() >= deadline:
                                fingerprint_available = False
                                incomplete("inventory_limit", current)
                        except (OSError, RuntimeError, ToolError, UnicodeError):
                            # Optional cache evidence must not hide otherwise usable package metadata.
                            fingerprint_available = False
                if path.name in _FINGERPRINT_FILES:
                    if fingerprint_available:
                        try:
                            data = _read_bytes(path, root, _MAX_MANIFEST_BYTES)
                        except (OSError, RuntimeError, ToolError):
                            fingerprint_available = False
                        else:
                            fingerprint_entries.append(
                                ("config", path.relative_to(root).as_posix(), hashlib.sha256(data).hexdigest())
                            )
                            if time.monotonic() >= deadline:
                                fingerprint_available = False
                                incomplete("inventory_limit", current)
                    continue
                if path.name not in _MANIFESTS:
                    continue
                manifests += 1
                if manifests > max_manifests:
                    incomplete("inventory_limit", path)
                    exhausted = True
                    break
                manifest, reason = _manifest(path, root)
                packages.setdefault(path.parent, []).append(manifest)
                if fingerprint_available:
                    fingerprint_entries.append(("manifest", path.relative_to(root).as_posix(), manifest.sha256))
                if manifest.kind == "python":
                    languages.add("python")
                elif manifest.kind == "php":
                    languages.add("php")
                if reason:
                    incomplete(reason, path)
            except (OSError, RuntimeError):
                # A stale directory iterator may carry names never verified inside this root.
                incomplete("invalid_project_manifest", current)
            except ToolError as error:
                incomplete(error.code, path)
    source_fingerprint = None
    if fingerprint_available and not missing:
        try:
            _directory_identity(root, root, root_identity)
            verified_source_bytes = 0
            # Re-read known inputs, never re-enumerate; each of two source passes has a 16 MiB budget.
            for kind, relative, expected_digest in fingerprint_entries:
                if time.monotonic() >= deadline:
                    fingerprint_available = False
                    break
                maximum = (
                    min(_MAX_SOURCE_FILE_BYTES, _MAX_SOURCE_BYTES - verified_source_bytes)
                    if kind == "source" else _MAX_MANIFEST_BYTES
                )
                data = _read_bytes(root / relative, root, maximum)
                if kind == "source":
                    verified_source_bytes += len(data)
                if hashlib.sha256(data).hexdigest() != expected_digest or time.monotonic() >= deadline:
                    fingerprint_available = False
                    break
            for directory, identity, modified, changed in scanned_directories:
                if not fingerprint_available:
                    break
                _directory_identity(directory, root, identity)
                current_info = directory.stat()
                if (current_info.st_mtime_ns, current_info.st_ctime_ns) != (modified, changed) or time.monotonic() >= deadline:
                    fingerprint_available = False
                    break
            if fingerprint_available and _worktree_identity(root) == worktree_id and time.monotonic() < deadline:
                digest = hashlib.sha256()
                # Length-prefixed fields keep root, identity, paths and hashes unambiguous.
                for fields in [("root", canonical_path(root), worktree_id), *sorted(fingerprint_entries)]:
                    for field in fields:
                        encoded = field.encode("utf-8", errors="strict")
                        digest.update(len(encoded).to_bytes(8, "big"))
                        digest.update(encoded)
                if time.monotonic() < deadline:
                    source_fingerprint = digest.hexdigest()
        except (OSError, RuntimeError, ToolError, UnicodeError):
            pass
    return ProjectInventory(
        root=root,
        packages=tuple(PackageInfo(path, tuple(items)) for path, items in sorted(packages.items())),
        worktree_id=worktree_id,
        language_ids=tuple(sorted(languages)),
        complete=not missing,
        missing_inputs=tuple(missing),
        source_fingerprint=source_fingerprint,
    )


def resolve_package_for_file(inventory: ProjectInventory, path: Path) -> PackageInfo | None:
    root = resolve_path(inventory.root)
    if root != inventory.root or type(inventory.version) is not int or inventory.version != 1:
        raise ToolError("project_scope_escape", "The inventory no longer identifies the selected workspace.")
    path = resolve_path(path, base=root)
    if not path.is_relative_to(root):
        raise ToolError("project_scope_escape", "The requested package path leaves the selected workspace.")
    candidates = [package for package in inventory.packages if path.is_relative_to(package.root)]
    if not candidates:
        return None
    package = max(candidates, key=lambda item: len(item.root.parts))
    if not package.root.is_relative_to(root):
        raise ToolError("project_scope_escape", "The requested package leaves the selected workspace.")
    for manifest in package.manifests:
        if hashlib.sha256(_read_bytes(manifest.path, root)).hexdigest() != manifest.sha256:
            raise _error("invalid_project_manifest", manifest.path, root)
    return package
