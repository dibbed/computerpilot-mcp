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
    target = tmp_path / "must-not-open.json"
    original_open = Path.open

    def forbidden(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == target or "must-not-open.json" in str(self):
            raise AssertionError("config opened while disabled")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbidden)
    settings = replace(SETTINGS, extensions_enabled=False, extensions_config=str(target))
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


def test_validate_candidate_valid_and_revision(definition: dict[str, Any]) -> None:
    from core.extensions.config import validate_extension_config_candidate
    from core.extensions.models import ExtensionConfig

    candidate_dict = {"schema_version": 1, "extensions": [definition]}
    config, revision = validate_extension_config_candidate(candidate_dict)
    assert isinstance(config, ExtensionConfig)
    assert len(config.extensions) == 1
    assert len(revision) == 64

    # Also test bytes, string, and ExtensionConfig candidate inputs
    raw_bytes = json.dumps(candidate_dict).encode("utf-8")
    config_b, rev_b = validate_extension_config_candidate(raw_bytes)
    assert len(config_b.extensions) == 1 and len(rev_b) == 64

    config_s, rev_s = validate_extension_config_candidate(raw_bytes.decode("utf-8"))
    assert len(config_s.extensions) == 1 and len(rev_s) == 64

    config_m, rev_m = validate_extension_config_candidate(config)
    assert len(config_m.extensions) == 1 and len(rev_m) == 64


@pytest.mark.parametrize("bad_candidate,code", [
    ({"schema_version": 2, "extensions": []}, "config_version_unsupported"),
    ({"schema_version": 0, "extensions": []}, "config_version_unsupported"),
    ({"schema_version": True, "extensions": []}, "config_invalid"),
    (b'{"schema_version": 1, "schema_version": 1, "extensions": []}', "config_invalid"),
    (b'{"schema_version": 1, "val": NaN, "extensions": []}', "config_invalid"),
    (b'[' * 35 + b']' * 35, "config_invalid"),
    (b'\xff\xfe\x00\x00', "config_invalid"),
    (12345, "config_invalid"),
], ids=["future-version", "zero-version", "bool-version", "duplicate-key", "nan-constant", "depth-limit", "bad-utf8", "bad-type"])
def test_validate_candidate_rejects_invalid_inputs(bad_candidate: Any, code: str) -> None:
    from core.errors import ToolError
    from core.extensions.config import validate_extension_config_candidate

    with pytest.raises(ToolError) as exc_info:
        validate_extension_config_candidate(bad_candidate)
    assert exc_info.value.code == code


def test_validate_candidate_semantic_and_secret_filters(definition: dict[str, Any]) -> None:
    from core.errors import ToolError
    from core.extensions.config import validate_extension_config_candidate

    # Non-absolute command
    bad_def = dict(definition, command="python")
    with pytest.raises(ToolError) as exc_info:
        validate_extension_config_candidate({"schema_version": 1, "extensions": [bad_def]})
    assert exc_info.value.code == "config_invalid"

    # Secret argument
    bad_def = dict(definition, arguments=["--token=supersecret"])
    with pytest.raises(ToolError) as exc_info:
        validate_extension_config_candidate({"schema_version": 1, "extensions": [bad_def]})
    assert exc_info.value.code == "config_invalid"

    # Command interpolation
    bad_def = dict(definition, arguments=["$(whoami)"])
    with pytest.raises(ToolError) as exc_info:
        validate_extension_config_candidate({"schema_version": 1, "extensions": [bad_def]})
    assert exc_info.value.code == "config_invalid"


def test_publish_extension_config_atomic_success(tmp_path: Path, definition: dict[str, Any]) -> None:
    from core.extensions.config import publish_extension_config

    target = tmp_path / "published_extensions.json"
    candidate = {"schema_version": 1, "extensions": [definition]}

    config, revision, published_path = publish_extension_config(candidate, target_path=target)
    assert published_path == target
    assert target.exists()
    assert config.schema_version == 1
    assert len(revision) == 64

    # Verify load_extension_config sees published file identically
    settings = replace(SETTINGS, extensions_enabled=True, extensions_config=str(target))
    loaded = load_extension_config(settings)
    assert loaded.error_code is None
    assert loaded.revision == revision
    assert len(loaded.config.extensions) == 1

    # Verify no temp files left behind
    assert not list(tmp_path.glob(".*.tmp.*"))


def test_publish_extension_config_refuses_to_overwrite_future_schema(
    tmp_path: Path, definition: dict[str, Any],
) -> None:
    from core.errors import ToolError
    from core.extensions.config import publish_extension_config

    target = tmp_path / "extensions.json"
    future_data = b'{"schema_version": 2, "extensions": [{"extension_id": "future"}]}'
    target.write_bytes(future_data)

    candidate = {"schema_version": 1, "extensions": [definition]}
    with pytest.raises(ToolError) as exc_info:
        publish_extension_config(candidate, target_path=target)
    assert exc_info.value.code == "config_version_unsupported"

    # Future file is never overwritten and its exact bytes remain preserved
    assert target.read_bytes() == future_data


def test_publish_extension_config_cas_revision(tmp_path: Path, definition: dict[str, Any]) -> None:
    from core.errors import ToolError
    from core.extensions.config import publish_extension_config

    target = tmp_path / "extensions.json"
    candidate1 = {"schema_version": 1, "extensions": [definition]}
    _, rev1, _ = publish_extension_config(candidate1, target_path=target)

    # CAS mismatch: expected revision does not match current file
    definition2 = dict(definition, display_name="Updated name")
    candidate2 = {"schema_version": 1, "extensions": [definition2]}
    with pytest.raises(ToolError) as exc_info:
        publish_extension_config(candidate2, target_path=target, expected_revision="0" * 64)
    assert exc_info.value.code == "config_conflict"
    # Content on disk still matches rev1
    assert target.read_bytes() == json.dumps(candidate1, indent=2).encode("utf-8")

    # CAS match: update succeeds
    _, rev2, _ = publish_extension_config(candidate2, target_path=target, expected_revision=rev1)
    assert rev2 != rev1

    # CAS on nonexistent file with non-empty expected revision fails
    nonexistent = tmp_path / "nonexistent.json"
    with pytest.raises(ToolError) as exc_info2:
        publish_extension_config(candidate1, target_path=nonexistent, expected_revision=rev1)
    assert exc_info2.value.code == "config_conflict"


def test_publish_extension_config_cleans_up_temp_on_failure(
    tmp_path: Path, definition: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    from core.extensions.config import publish_extension_config

    target = tmp_path / "extensions.json"
    candidate = {"schema_version": 1, "extensions": [definition]}

    def broken_replace(src: Any, dst: Any) -> None:
        raise OSError("Simulated atomic replace failure")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError, match="Simulated atomic replace failure"):
        publish_extension_config(candidate, target_path=target)

    # Verify no temporary files remain in parent dir
    assert not list(tmp_path.glob(".*.tmp.*"))
    assert not target.exists()


def test_restart_only_application_contract(tmp_path: Path, definition: dict[str, Any]) -> None:
    from core.extensions.config import publish_extension_config
    from core.extensions.manager import ExtensionLifecycleManager

    target = tmp_path / "extensions.json"
    def_a = dict(definition, extension_id="ext_alpha")
    candidate_a = {"schema_version": 1, "extensions": [def_a]}
    config_a, _, _ = publish_extension_config(candidate_a, target_path=target)

    # Manager 1 starts with frozen config_a
    manager1 = ExtensionLifecycleManager(config=config_a)
    assert manager1.get_owner("ext_alpha") is not None
    assert manager1.get_owner("ext_beta") is None

    # Disk config is updated to ext_beta
    def_b = dict(definition, extension_id="ext_beta")
    candidate_b = {"schema_version": 1, "extensions": [def_b]}
    _config_b, _, _ = publish_extension_config(candidate_b, target_path=target)

    # Running manager 1 is completely unaffected by disk changes
    assert manager1.get_owner("ext_alpha") is not None
    assert manager1.get_owner("ext_beta") is None

    # Only upon restart (new manager created with reloaded config) do changes apply
    settings = replace(SETTINGS, extensions_enabled=True, extensions_config=str(target))
    reloaded = load_extension_config(settings)
    manager2 = ExtensionLifecycleManager(config=reloaded.config)
    assert manager2.get_owner("ext_alpha") is None
    assert manager2.get_owner("ext_beta") is not None


def test_remove_extension_preserves_unresolved_history(tmp_path: Path, definition: dict[str, Any]) -> None:
    from core.recovery import OperationRecoveryJournal
    from core.recovery_models import ExternalOperationMetadata

    journal_path = tmp_path / "operations.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(journal_path, "runtime-alpha")

    meta = ExternalOperationMetadata(
        extension_id="doomed_ext",
        provider="doomed_provider",
        public_name="external.doomed_ext.mutate",
        remote_name="mutate",
        catalog_generation=1,
        schema_fingerprint="f" * 64,
        mutation_class="remote_mutation",
        dispatch_stage="prepared",
    )
    handle = journal.begin("external.doomed_ext.mutate", "test_op", external_metadata=meta)
    assert handle is not None
    journal.checkpoint_external_dispatch(handle)
    journal.mark_uncertain(handle, "timeout_lost")

    assert journal.unresolved_external_count() == 1
    record = journal.inspect(handle.operation_id)
    assert record["state"] == "uncertain"
    assert record["external"]["extension_id"] == "doomed_ext"

    # Simulate restart without doomed_ext
    reloaded_journal = OperationRecoveryJournal()
    reloaded_journal.configure(journal_path, "runtime-beta")
    assert reloaded_journal.unresolved_external_count() == 1
    reloaded_record = reloaded_journal.inspect(handle.operation_id)
    assert reloaded_record["external"]["extension_id"] == "doomed_ext"
    assert reloaded_record["state"] == "uncertain"


def test_rename_extension_preserves_unresolved_history_without_translation(
    tmp_path: Path, definition: dict[str, Any],
) -> None:
    from core.recovery import OperationRecoveryJournal
    from core.recovery_models import ExternalOperationMetadata

    journal_path = tmp_path / "operations.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(journal_path, "runtime-rename-test")

    meta = ExternalOperationMetadata(
        extension_id="original_name",
        provider="provider",
        public_name="external.original_name.action",
        remote_name="action",
        catalog_generation=1,
        schema_fingerprint="c" * 64,
        mutation_class="remote_mutation",
        dispatch_stage="prepared",
    )
    handle = journal.begin("external.original_name.action", "op_data", external_metadata=meta)
    assert handle is not None
    journal.checkpoint_external_dispatch(handle)
    journal.mark_uncertain(handle, "network_drop")

    # Rename extension from original_name to renamed_name
    reloaded_journal = OperationRecoveryJournal()
    reloaded_journal.configure(journal_path, "runtime-after-rename")

    # Historical record is never translated or overwritten to renamed_name
    record = reloaded_journal.inspect(handle.operation_id)
    assert record["external"]["extension_id"] == "original_name"
    assert record["external"]["public_name"] == "external.original_name.action"


def test_compatibility_data_preservation_and_disabled_baseline(tmp_path: Path) -> None:
    nonexistent_config = tmp_path / "no_ext" / "extensions.json"
    settings = replace(SETTINGS, extensions_enabled=False, extensions_config=str(nonexistent_config))

    result = load_extension_config(settings)
    assert result.error_code is None
    assert result.config.extensions == ()
    # Disabled baseline never creates directories or files
    assert not (tmp_path / "no_ext").exists()

    # Native server tool registry baseline remains intact
    tools = asyncio.run(create_server().list_tools())
    assert len(tools) >= 32
