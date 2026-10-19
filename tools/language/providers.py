"""Local, pinned language-server provider configuration.

This module is strictly offline: it never discovers executables, never spawns
processes, and never reads credentials. Provider commands come only from a
local operator configuration file whose binaries are pinned by SHA-256 before
any dispatch.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from core.config import SETTINGS, Settings
from core.errors import ToolError
from core.resource_locks import canonical_path

ProviderId = Literal["pyright", "typescript", "phpactor"]

PROVIDER_LANGUAGES: dict[str, tuple[str, ...]] = {
    "pyright": ("python",),
    "typescript": ("javascript", "javascriptreact", "typescript", "typescriptreact"),
    "phpactor": ("php",),
}
_PROVIDER_FIELDS = frozenset(
    {
        "provider_id",
        "languages",
        "executable",
        "executable_sha256",
        "entrypoint",
        "entrypoint_sha256",
        "declared_version",
        "trusted_roots",
    }
)
MAX_CONFIG_BYTES = 64 * 1024
MAX_JSON_DEPTH = 8
MAX_PROVIDERS = 3
MAX_TRUSTED_ROOTS = 32
MAX_WORKTREE_MARKER_BYTES = 4_096
MAX_DECLARED_VERSION_CHARS = 64
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_LAUNCHER_SUFFIXES = (".cmd", ".bat", ".ps1")


@dataclass(frozen=True, slots=True)
class LspProvider:
    provider_id: ProviderId
    languages: tuple[str, ...]
    executable: Path
    executable_sha256: str
    entrypoint: Path | None
    entrypoint_sha256: str | None
    declared_version: str
    trusted_roots: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class SessionKey:
    workspace_root: str
    worktree_marker_digest: str
    language_id: str
    provider_id: str
    config_digest: str
    runtime_generation: str


def _invalid() -> ToolError:
    """A fixed error that never echoes configuration content."""

    return ToolError("config_invalid", "The LSP provider configuration is invalid.")


def _check_json_depth(text: str, limit: int) -> None:
    depth = 0
    quoted = False
    escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > limit:
                raise ValueError("config depth limit")
        elif char in "]}":
            depth -= 1
            if depth < 0:
                raise ValueError("invalid JSON nesting")
    if depth or quoted:
        raise ValueError("incomplete JSON")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError("nonfinite JSON number")


def _parse_json(text: str) -> object:
    _check_json_depth(text, MAX_JSON_DEPTH)
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def _absolute_local(value: str) -> bool:
    local = value[4:] if value.startswith("\\\\?\\") else value
    if any(ord(char) < 32 for char in value) or "://" in value or local.startswith(("//", "\\\\")):
        return False
    return Path(value).is_absolute()


def _launcher_path(value: str) -> bool:
    return value.casefold().endswith(_LAUNCHER_SUFFIXES)


def _load_provider(entry: object) -> LspProvider:
    if not isinstance(entry, dict) or frozenset(entry) != _PROVIDER_FIELDS:
        raise _invalid()
    provider_id = entry["provider_id"]
    if not isinstance(provider_id, str) or provider_id not in PROVIDER_LANGUAGES:
        raise _invalid()
    allowed = PROVIDER_LANGUAGES[provider_id]
    languages = entry["languages"]
    if not isinstance(languages, list) or not 1 <= len(languages) <= len(allowed):
        raise _invalid()
    if any(not isinstance(item, str) or item not in allowed for item in languages):
        raise _invalid()
    if len(set(languages)) != len(languages):
        raise _invalid()
    executable = entry["executable"]
    if not isinstance(executable, str) or not _absolute_local(executable) or _launcher_path(executable):
        raise _invalid()
    executable_sha256 = entry["executable_sha256"]
    if not isinstance(executable_sha256, str) or _DIGEST_RE.fullmatch(executable_sha256) is None:
        raise _invalid()
    entrypoint = entry["entrypoint"]
    entrypoint_sha256 = entry["entrypoint_sha256"]
    if entrypoint is None:
        if entrypoint_sha256 is not None:
            raise _invalid()
        entrypoint_path: Path | None = None
    else:
        if not isinstance(entrypoint, str) or not _absolute_local(entrypoint) or _launcher_path(entrypoint):
            raise _invalid()
        if not isinstance(entrypoint_sha256, str) or _DIGEST_RE.fullmatch(entrypoint_sha256) is None:
            raise _invalid()
        entrypoint_path = Path(entrypoint)
    if provider_id in {"typescript", "phpactor"} and entrypoint_path is None:
        raise _invalid()
    declared_version = entry["declared_version"]
    if (
        not isinstance(declared_version, str)
        or not 1 <= len(declared_version) <= MAX_DECLARED_VERSION_CHARS
        or any(ord(char) < 32 for char in declared_version)
    ):
        raise _invalid()
    roots = entry["trusted_roots"]
    if not isinstance(roots, list) or not 1 <= len(roots) <= MAX_TRUSTED_ROOTS:
        raise _invalid()
    trusted: list[Path] = []
    for root in roots:
        if not isinstance(root, str) or not _absolute_local(root):
            raise _invalid()
        trusted.append(Path(root))
    return LspProvider(
        provider_id=cast(ProviderId, provider_id),
        languages=tuple(languages),
        executable=Path(executable),
        executable_sha256=executable_sha256,
        entrypoint=entrypoint_path,
        entrypoint_sha256=entrypoint_sha256,
        declared_version=declared_version,
        trusted_roots=tuple(trusted),
    )


def load_lsp_providers(path: Path) -> tuple[LspProvider, ...]:
    """Load a local provider configuration; never spawns, discovers, or reads credentials."""

    try:
        with Path(path).open("rb") as handle:
            data = handle.read(MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        raise ToolError(
            "lsp_provider_config_missing", "The LSP provider configuration file was not found."
        ) from None
    except OSError:
        raise _invalid() from None
    if len(data) > MAX_CONFIG_BYTES:
        raise _invalid()
    try:
        parsed = _parse_json(data.decode("utf-8", errors="strict"))
    except (ValueError, UnicodeError):
        raise _invalid() from None
    if not isinstance(parsed, dict) or frozenset(parsed) != {"schema_version", "providers"}:
        raise _invalid()
    version = parsed["schema_version"]
    if type(version) is not int:
        raise _invalid()
    if version != 1:
        raise ToolError("config_version_unsupported", "Unsupported LSP provider configuration version.")
    entries = parsed["providers"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_PROVIDERS:
        raise _invalid()
    providers: list[LspProvider] = []
    seen: set[str] = set()
    for entry in entries:
        provider = _load_provider(entry)
        if provider.provider_id in seen:
            raise _invalid()
        seen.add(provider.provider_id)
        providers.append(provider)
    return tuple(providers)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_pinned_file(path: Path, expected: str) -> None:
    try:
        if not path.is_file():
            raise OSError("not a regular file")
        digest = _sha256_file(path)
    except OSError:
        raise ToolError(
            "lsp_provider_changed",
            "The pinned LSP provider file is unavailable; refusing to launch.",
        ) from None
    if digest != expected:
        raise ToolError(
            "lsp_provider_changed",
            "The pinned LSP provider file changed; refusing to launch.",
        )


def _providers_from_settings(settings: Settings) -> tuple[LspProvider, ...]:
    value = settings.providers_config
    if not isinstance(value, str) or not value.strip():
        raise ToolError("lsp_provider_unavailable", "No LSP provider configuration is configured.")
    return load_lsp_providers(Path(value))


def _under_trusted(canonical_root: str, trusted_roots: Sequence[Path]) -> bool:
    for trusted in trusted_roots:
        trusted_canonical = canonical_path(trusted)
        prefix = trusted_canonical if trusted_canonical.endswith(os.sep) else trusted_canonical + os.sep
        if canonical_root == trusted_canonical or canonical_root.startswith(prefix):
            return True
    return False


def validate_provider(
    root: str | Path,
    language: str,
    *,
    providers: Sequence[LspProvider] | None = None,
    settings: Settings = SETTINGS,
) -> LspProvider:
    """Select exactly one eligible provider and re-verify its pinned files before any launch."""

    candidates = tuple(providers) if providers is not None else _providers_from_settings(settings)
    canonical_root = canonical_path(root)
    eligible = [
        provider
        for provider in candidates
        if language in provider.languages and _under_trusted(canonical_root, provider.trusted_roots)
    ]
    if not eligible:
        raise ToolError("lsp_provider_unavailable", "No LSP provider is configured for this language and root.")
    if len(eligible) > 1:
        raise ToolError("lsp_provider_ambiguous", "More than one LSP provider matches this language and root.")
    provider = eligible[0]
    _verify_pinned_file(provider.executable, provider.executable_sha256)
    if provider.entrypoint is not None and provider.entrypoint_sha256 is not None:
        _verify_pinned_file(provider.entrypoint, provider.entrypoint_sha256)
    return provider


def command_for(provider: LspProvider) -> tuple[str, ...]:
    """Build the exact argv tuple for a validated provider; shell launchers stay forbidden."""

    executable = str(provider.executable)
    if not Path(executable).is_absolute() or _launcher_path(executable):
        raise ToolError("lsp_provider_command_denied", "The LSP provider command is not permitted.")
    if provider.entrypoint is not None:
        entrypoint = str(provider.entrypoint)
        if not Path(entrypoint).is_absolute() or _launcher_path(entrypoint):
            raise ToolError("lsp_provider_command_denied", "The LSP provider command is not permitted.")
        if provider.provider_id == "phpactor":
            return (executable, entrypoint, "language-server")
        return (executable, entrypoint, "--stdio")
    if provider.provider_id != "pyright":
        raise ToolError("lsp_provider_command_denied", "The LSP provider command is not permitted.")
    return (executable, "--stdio")


def _worktree_marker_digest(root: Path) -> str:
    marker = root / ".git"
    if marker.is_file():
        try:
            with marker.open("rb") as handle:
                content = handle.read(MAX_WORKTREE_MARKER_BYTES + 1)
        except OSError:
            raise ToolError("lsp_worktree_marker_invalid", "The worktree marker could not be read.") from None
        if len(content) > MAX_WORKTREE_MARKER_BYTES:
            raise ToolError("lsp_worktree_marker_invalid", "The worktree marker exceeds the size limit.")
        return "file:" + hashlib.sha256(content).hexdigest()
    if marker.is_dir():
        try:
            info = marker.stat()
        except OSError:
            raise ToolError("lsp_worktree_marker_invalid", "The worktree marker could not be read.") from None
        identity = f"{info.st_dev}:{info.st_ino}" if info.st_ino else f"{info.st_mtime_ns}:{info.st_size}"
        return f"dir:{canonical_path(marker)}:{identity}"
    return "no-git"


def _config_digest(provider: LspProvider) -> str:
    record = {
        "provider_id": provider.provider_id,
        "languages": list(provider.languages),
        "executable": str(provider.executable),
        "executable_sha256": provider.executable_sha256,
        "entrypoint": str(provider.entrypoint) if provider.entrypoint is not None else None,
        "entrypoint_sha256": provider.entrypoint_sha256,
        "declared_version": provider.declared_version,
        "trusted_roots": [str(item) for item in provider.trusted_roots],
    }
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_session_key(root: str | Path, language: str, provider: LspProvider, generation: str) -> SessionKey:
    """Reproducible per-worktree session identity; HEAD is intentionally not part of the key."""

    canonical_root = canonical_path(root)
    return SessionKey(
        workspace_root=canonical_root,
        worktree_marker_digest=_worktree_marker_digest(Path(canonical_root)),
        language_id=language,
        provider_id=provider.provider_id,
        config_digest=_config_digest(provider),
        runtime_generation=generation,
    )
