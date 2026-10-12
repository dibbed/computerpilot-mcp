"""Bounded offline configuration loading; no process or network activation."""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.config import SETTINGS, Settings
from core.errors import ToolError
from core.extensions.models import LIMITS, ExtensionConfig, ExtensionDefinition
from core.platform import config_home


@dataclass(frozen=True, slots=True)
class ConfigLoadResult:
    config: ExtensionConfig
    error_code: str | None = None
    revision: str | None = None


def _absolute_local_path(value: str, *, portable: bool = False) -> bool:
    local = value[4:] if value.startswith("\\\\?\\") else value
    if any(ord(char) < 32 for char in value) or "://" in value or local.startswith(("//", "\\\\")):
        return False
    if portable:
        return posixpath.isabs(local) or (bool(ntpath.splitdrive(local)[0]) and ntpath.isabs(local))
    return Path(value).is_absolute()


def resolve_extension_config_path(settings: Settings = SETTINGS) -> Path:
    value = settings.extensions_config
    path = config_home() / "computerpilot-mcp" / "extensions.json" if value is None else Path(value)
    if not _absolute_local_path(str(path)):
        raise ValueError("config path must be absolute and local")
    return path


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


_VALUE_KEYS = frozenset({"PYTHONUNBUFFERED", "PYTHONIOENCODING", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "NO_COLOR", "TERM"})
_OS_KEYS = frozenset({"SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR"})
_INHERIT_KEYS = _VALUE_KEYS | _OS_KEYS | {"HOME", "APPDATA", "USERPROFILE"}
_ARG_SECRET_KEYS = frozenset({
    "token", "api-key", "apikey", "access-key", "password", "secret", "authorization", "credential", "cookie", "session",
})


def _environment_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value, flags=re.ASCII))


def _unsafe_key(value: str) -> bool:
    key = value.upper()
    if key in _VALUE_KEYS:
        return False
    return key in {
        "PATH", "NODE_OPTIONS", "NODE_PATH", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS",
        "RUBYOPT", "RUBYLIB", "PERL5OPT", "PERL5LIB", "GCONV_PATH", "GLIBC_TUNABLES",
        "ENV", "BASH_ENV", "ZDOTDIR", "SHELLOPTS", "BASHOPTS", "PSMODULEPATH", "PSEXECUTIONPOLICYPREFERENCE",
    } or key.startswith(
        ("PYTHON", "LD_", "DYLD_", "BASH_FUNC_", "DOTNET_", "CORECLR_", "COR_"),
    )


def _validate_operator_definition(definition: ExtensionDefinition, parent: Mapping[str, str]) -> None:
    environment = definition.environment
    names = [*environment.inherit, *environment.values, *environment.references]
    aliases = [name.upper() if os.name == "nt" else name for name in names]
    if len(set(aliases)) != len(aliases) or any(not _environment_name(name) or _unsafe_key(name) for name in names):
        raise ValueError("unsafe or duplicate environment key")
    if any(name.upper() not in _INHERIT_KEYS for name in environment.inherit):
        raise ValueError("environment inheritance not allowlisted")
    if any(name.upper() not in _VALUE_KEYS for name in environment.values):
        raise ValueError("nonsecret environment values not allowlisted")
    for name, reference in environment.references.items():
        if name.upper() in _INHERIT_KEYS or not _environment_name(reference.name):
            raise ValueError("unsafe environment reference")
    literals = (definition.command, definition.working_directory, definition.provider, definition.display_name,
                *definition.arguments, *environment.values.values())
    for value in literals:
        if any(ord(char) < 32 for char in value) or "$(" in value or "${" in value or "`" in value or re.search(r"%[^%]+%", value):
            raise ValueError("operator field contains control or interpolation")
    for argument in definition.arguments:
        key = argument.casefold().replace("_", "-").lstrip("-").split("=", 1)[0].split(":", 1)[0]
        if key in _ARG_SECRET_KEYS:
            raise ValueError("credentials must use references, not arguments")
    for reference in environment.references.values():
        secret = parent.get(reference.name)
        if secret and any(secret in literal for literal in literals):
            raise ValueError("credential literal in operator fields")


def build_extension_environment(definition: ExtensionDefinition, parent: Mapping[str, str] | None = None) -> dict[str, str]:
    """Resolve a private minimal environment; this does not authorize or launch code."""
    values = os.environ if parent is None else parent
    try:
        _validate_operator_definition(definition, values)
        required = ("SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP") if os.name == "nt" else ("LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")
        result = {name: values[name] for name in required if name in values}
        result.update({name: values[name] for name in definition.environment.inherit if name in values})
        result.update(definition.environment.values)
        for name, reference in definition.environment.references.items():
            secret = values.get(reference.name)
            if not secret:
                raise ToolError("auth_missing", "A required extension credential is unavailable.")
            result[name] = secret
        for policy in definition.tools.values():
            if any(name not in definition.environment.references for name in policy.required_env_refs):
                raise ToolError("auth_missing", "A required extension credential reference is unavailable.")
        encoded = 0
        for name, value in result.items():
            if len(value) > LIMITS.environment_value_bytes:
                raise ValueError("environment value limit")
            size = len(value.encode("utf-8"))
            if "\0" in value or size > LIMITS.environment_value_bytes:
                raise ValueError("environment value limit")
            encoded += len(name.encode("utf-8")) + size + 2
        if encoded > LIMITS.environment_bytes:
            raise ValueError("environment byte limit")
        return result
    except (ValueError, UnicodeError):
        raise ToolError("config_invalid", "The extension environment is invalid.") from None


def load_extension_config(settings: Settings = SETTINGS) -> ConfigLoadResult:
    """Always validate a fresh complete snapshot; fail only extensions with safe codes."""
    empty = ExtensionConfig(schema_version=1)
    if not settings.extensions_enabled:
        return ConfigLoadResult(empty)
    try:
        path = resolve_extension_config_path(settings)
        try:
            with path.open("rb") as handle:
                data = handle.read(settings.extension_limits.config_bytes + 1)
        except FileNotFoundError:
            return ConfigLoadResult(empty)
        if len(data) > settings.extension_limits.config_bytes:
            raise ValueError("config byte limit")
        text = data.decode("utf-8", errors="strict")
        _check_json_depth(text, settings.extension_limits.json_depth)
        parsed = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        if not isinstance(parsed, dict):
            raise ValueError("config root must be an object")
        version = parsed.get("schema_version")
        if type(version) is int and version != 1:
            return ConfigLoadResult(empty, "config_version_unsupported")
        config = ExtensionConfig.model_validate_json(data)
        for definition in config.extensions:
            _validate_operator_definition(definition, os.environ)
            if not _absolute_local_path(definition.command, portable=True) or not _absolute_local_path(
                definition.working_directory, portable=True,
            ):
                raise ValueError("provider paths must be absolute and local")
        return ConfigLoadResult(config, revision=hashlib.sha256(data).hexdigest())
    except (OSError, ValueError, UnicodeError):
        return ConfigLoadResult(empty, "config_invalid")
