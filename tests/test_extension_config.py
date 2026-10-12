from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from core.config import SETTINGS
from core.extensions.config import load_extension_config, resolve_extension_config_path
from core.registry import create_server


@pytest.fixture
def definition(tmp_path: Path) -> dict[str, Any]:
    return {
        "extension_id": "fixture", "display_name": "Local fixture", "provider": "fixture",
        "command": sys.executable, "working_directory": str(tmp_path), "platforms": ["windows"],
        "tools": {"read_value": {"remote_name": "read_value", "domain": "testing"}},
    }


def test_disabled_does_not_even_open_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("config opened while disabled")

    monkeypatch.setattr(Path, "open", forbidden)
    settings = replace(SETTINGS, extensions_enabled=False, extensions_config=str(tmp_path / "must-not-open.json"))
    result = load_extension_config(settings)
    assert result.error_code is None and result.config.extensions == ()


def test_missing_and_disabled_placeholders_are_zero_or_valid(tmp_path: Path, definition: dict[str, Any]) -> None:
    path = tmp_path / "extensions.json"
    settings = replace(SETTINGS, extensions_enabled=True, extensions_config=str(path))
    assert load_extension_config(settings).config.extensions == ()
    definition["command"] = "C:/nonexistent/python.exe"
    definition["working_directory"] = "C:/nonexistent"
    definition["environment"] = {"references": {"TOKEN": {"kind": "env", "name": "ABSENT_REFERENCE"}}}
    path.write_text(json.dumps({"schema_version": 1, "extensions": [definition]}), encoding="utf-8")
    result = load_extension_config(settings)
    assert result.error_code is None and len(result.config.extensions) == 1
    assert result.revision and len(result.revision) == 64


@pytest.mark.parametrize("data,code", [
    (b'{"schema_version":2,"extensions":[]}', "config_version_unsupported"),
    (b'{"schema_version":true,"extensions":[]}', "config_invalid"),
    (b'{"schema_version":1,"schema_version":1,"extensions":[]}', "config_invalid"),
    (b'{"schema_version":1,"extensions":[],"secret":"CANARY"}', "config_invalid"),
    (b'\xff', "config_invalid"),
    (b'[' * 33 + b']' * 33, "config_invalid"),
    (b' ' * (256 * 1024 + 1), "config_invalid"),
], ids=["future", "bool", "duplicates", "unknown", "utf8", "depth", "bytes"])
def test_invalid_configuration_is_safe_atomic_and_preserved(tmp_path: Path, data: bytes, code: str) -> None:
    path = tmp_path / "extensions.json"
    path.write_bytes(data)
    result = load_extension_config(replace(SETTINGS, extensions_enabled=True, extensions_config=str(path)))
    assert result.error_code == code and result.config.extensions == ()
    assert path.read_bytes() == data
    assert "CANARY" not in repr(result)
    assert len(asyncio.run(create_server().list_tools())) >= 32


def test_absolute_local_paths_and_preparse_string_escapes(tmp_path: Path, definition: dict[str, Any]) -> None:
    for value in ("relative.json", "https://example.invalid/config.json", "//server/share/config.json"):
        assert load_extension_config(replace(SETTINGS, extensions_enabled=True, extensions_config=value)).error_code == "config_invalid"
    path = tmp_path / "extensions.json"
    definition["display_name"] = 'braces { [ and escaped " quote } ]'
    path.write_text(json.dumps({"schema_version": 1, "extensions": [definition]}), encoding="utf-8")
    settings = replace(SETTINGS, extensions_enabled=True, extensions_config=str(path))
    assert load_extension_config(settings).error_code is None
    definition["command"] = "relative-python"
    path.write_text(json.dumps({"schema_version": 1, "extensions": [definition]}), encoding="utf-8")
    assert load_extension_config(settings).error_code == "config_invalid"


def test_config_home_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert resolve_extension_config_path(replace(SETTINGS, extensions_config=None)) == tmp_path / "computerpilot-mcp" / "extensions.json"


def test_minimal_private_environment_and_missing_reference(definition: dict[str, Any]) -> None:
    from core.errors import ToolError
    from core.extensions.config import build_extension_environment
    from core.extensions.models import ExtensionDefinition

    definition["environment"] = {
        "values": {"PYTHONUNBUFFERED": "1"},
        "references": {"FIXTURE_TOKEN": {"kind": "env", "name": "FIXTURE_TOKEN"}},
    }
    model = ExtensionDefinition.model_validate_json(json.dumps(definition))
    canary = "PRIVATE-CANARY-7c692"
    result = build_extension_environment(model, {"FIXTURE_TOKEN": canary, "UNRELATED_SECRET": "unrelated", "PYTHONPATH": "inject"})
    assert result == {"PYTHONUNBUFFERED": "1", "FIXTURE_TOKEN": canary}
    assert canary not in repr(model)
    with pytest.raises(ToolError) as error:
        build_extension_environment(model, {})
    assert error.value.code == "auth_missing" and "FIXTURE_TOKEN" not in str(error.value)


@pytest.mark.parametrize("environment,arguments", [
    ({"inherit": ["PATH"]}, []),
    ({"inherit": ["PYTHONPATH"]}, []),
    ({"values": {"API_TOKEN": "value"}}, []),
    ({"references": {"LD_PRELOAD": {"kind": "env", "name": "TOKEN"}}}, []),
    ({"references": {"TOKEN": {"kind": "env", "name": "*"}}}, []),
    ({}, ["--api-key=value"]),
    ({}, ["${TOKEN}"]),
    ({}, ["$(cmd)"]),
], ids=["path", "pythonpath", "secret-value", "preload", "wildcard", "secret-argv", "interpolation", "substitution"])
def test_disabled_unsafe_config_is_still_denied(
    tmp_path: Path, definition: dict[str, Any], environment: dict[str, Any], arguments: list[str],
) -> None:
    definition["environment"], definition["arguments"] = environment, arguments
    path = tmp_path / "extensions.json"
    path.write_text(json.dumps({"schema_version": 1, "extensions": [definition]}), encoding="utf-8")
    result = load_extension_config(replace(SETTINGS, extensions_enabled=True, extensions_config=str(path)))
    assert result.error_code == "config_invalid" and result.config.extensions == ()


def test_resolved_literal_never_reaches_public_failure(
    tmp_path: Path, definition: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    canary = "PRIVATE-CANARY-7c692"
    monkeypatch.setenv("FIXTURE_TOKEN", canary)
    definition["environment"] = {"references": {"TOKEN": {"kind": "env", "name": "FIXTURE_TOKEN"}}}
    definition["arguments"] = ["innocent=" + canary]
    path = tmp_path / "extensions.json"
    path.write_text(json.dumps({"schema_version": 1, "extensions": [definition]}), encoding="utf-8")
    result = load_extension_config(replace(SETTINGS, extensions_enabled=True, extensions_config=str(path)))
    assert result.error_code == "config_invalid" and canary not in repr(result)
    captured = capsys.readouterr()
    assert canary not in captured.out + captured.err


def test_environment_value_and_aggregate_limits(definition: dict[str, Any]) -> None:
    from core.errors import ToolError
    from core.extensions.config import build_extension_environment
    from core.extensions.models import ExtensionDefinition

    definition["environment"] = {"references": {"TOKEN": {"name": "TOKEN"}}}
    model = ExtensionDefinition.model_validate_json(json.dumps(definition))
    with pytest.raises(ToolError, match="environment is invalid"):
        build_extension_environment(model, {"TOKEN": "x" * (8192 + 1)})
    definition["environment"] = {"references": {f"TOKEN_{i}": {"name": f"TOKEN_{i}"} for i in range(5)}}
    model = ExtensionDefinition.model_validate_json(json.dumps(definition))
    with pytest.raises(ToolError, match="environment is invalid"):
        build_extension_environment(model, {f"TOKEN_{i}": "x" * 8192 for i in range(5)})


@pytest.mark.parametrize("key", [
    "LD_AUDIT", "NODE_OPTIONS", "NODE_PATH", "PYTHONUSERBASE", "JAVA_TOOL_OPTIONS",
    "GCONV_PATH", "DOTNET_STARTUP_HOOKS", "RUBYOPT", "PERL5OPT", "BASH_ENV",
])
def test_launcher_injection_refs_denied_before_any_launch(
    key: str, tmp_path: Path, definition: dict[str, Any],
) -> None:
    from core.errors import ToolError
    from core.extensions.config import build_extension_environment
    from core.extensions.models import ExtensionDefinition

    definition["environment"] = {"references": {key: {"name": "REVIEW_REFERENCE"}}}
    model = ExtensionDefinition.model_validate_json(json.dumps(definition))
    with pytest.raises(ToolError) as error:
        build_extension_environment(model, {"REVIEW_REFERENCE": "/review-placeholder"})
    assert error.value.code == "config_invalid"
    path = tmp_path / "extensions.json"
    path.write_text(json.dumps({"schema_version": 1, "extensions": [definition]}), encoding="utf-8")
    result = load_extension_config(replace(SETTINGS, extensions_enabled=True, extensions_config=str(path)))
    assert result.error_code == "config_invalid" and result.config.extensions == ()
