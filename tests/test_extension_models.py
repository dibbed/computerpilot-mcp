from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from core.extensions.models import ExtensionConfig, ExtensionDefinition, MutationClass, ToolPolicy, TrustClass


@pytest.fixture
def definition() -> dict[str, Any]:
    return {
        "extension_id": "fixture", "display_name": "Local fixture", "provider": "fixture",
        "transport": "stdio", "command": "C:/placeholder/python.exe", "arguments": [],
        "working_directory": "C:/placeholder", "platforms": ["windows"],
        "tools": {"read_value": {"remote_name": "read_value", "domain": "testing"}},
    }


def parse(value: dict[str, Any]) -> ExtensionDefinition:
    return ExtensionDefinition.model_validate_json(json.dumps(value))


def test_defaults_deny_and_nested_mappings_are_immutable(definition: dict[str, Any]) -> None:
    model = parse(definition)
    assert model.enabled is False and model.launch_approved is False
    assert model.trust_class is TrustClass.UNTRUSTED
    policy = model.tools["read_value"]
    assert policy.allow_call is False and policy.mutation_class is MutationClass.UNKNOWN
    assert policy.profiles == () and policy.intents == ()
    with pytest.raises(TypeError):
        model.tools["another"] = policy  # type: ignore[index]
    with pytest.raises(TypeError):
        model.environment.values["TOKEN"] = "value"  # type: ignore[index]
    with pytest.raises(ValidationError):
        model.enabled = True
    assert parse(model.model_dump(mode="json")) == model
    assert "session" not in model.model_dump() and "resolved_environment" not in model.model_dump()


@pytest.mark.parametrize("field,value", [
    ("enabled", "true"), ("launch_approved", 1), ("extension_id", 5),
    ("display_name", "x" * 129), ("provider", "x" * 65), ("extension_id", "x" * 33),
    ("command", "x" * 32768), ("working_directory", "x" * 32768),
    ("arguments", ["x"] * 65), ("arguments", ["x" * 2049]),
    ("transport", "http"), ("trust_class", "built_in"), ("session", {}),
], ids=["string-bool", "int-bool", "int-id", "long-display", "long-provider", "long-id",
        "long-command", "long-cwd", "many-args", "long-arg", "http", "built-in", "runtime-field"])
def test_strict_fields_and_bounds(definition: dict[str, Any], field: str, value: Any) -> None:
    definition[field] = value
    with pytest.raises(ValidationError):
        parse(definition)


@pytest.mark.parametrize("policy", [
    {"allow_call": "false"}, {"mutation_class": "probably_readonly"},
    {"determinism": True}, {"determinism": 5}, {"latency_tier": 6},
    {"profiles": ["admin"]}, {"domain": "admin"}, {"readOnlyHint": True},
    {"required_env_refs": ["x" * 129]},
], ids=["string-bool", "unknown-class", "bool-score", "native-score", "large-score", "profile", "domain", "hint", "long-ref"])
def test_reviewed_policy_does_not_accept_provider_hints_or_unbounded_values(policy: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ToolPolicy.model_validate_json(json.dumps({"remote_name": "read", "domain": "testing", **policy}))


@pytest.mark.parametrize("version", [True, "1", 2])
def test_schema_version_is_an_exact_integer(version: Any) -> None:
    with pytest.raises(ValidationError):
        ExtensionConfig.model_validate_json(json.dumps({"schema_version": version, "extensions": []}))


def test_nested_security_models_are_strict(definition: dict[str, Any]) -> None:
    definition["environment"] = {"references": {"TOKEN": {"kind": "file", "name": "token"}}}
    with pytest.raises(ValidationError):
        parse(definition)
    definition["environment"] = {"values": {"PYTHONUNBUFFERED": 1}}
    with pytest.raises(ValidationError):
        parse(definition)
    definition["environment"] = {}
    definition["timeouts"] = {"call_sec": 31}
    with pytest.raises(ValidationError):
        parse(definition)


def test_config_count_bound_and_descriptor_schema_copy(definition: dict[str, Any]) -> None:
    from core.extensions.models import ExtensionIdentity, ExtensionToolDescriptor

    with pytest.raises(ValidationError):
        ExtensionConfig.model_validate_json(json.dumps({"schema_version": 1, "extensions": [definition] * 17}))
    model = parse(definition)
    descriptor = ExtensionToolDescriptor(
        "external.fixture.read_value", "read_value", ExtensionIdentity("fixture", "fixture"), "Read",
        b'{"type":"object","properties":{"file.name":{"type":"string"}}}', None,
        model.tools["read_value"], 1, "accepted-fingerprint",
    )
    descriptor.input_schema["properties"]["file.name"]["type"] = "integer"
    assert descriptor.input_schema["properties"]["file.name"]["type"] == "string"


def test_exact_namespace_length_case_and_collisions(definition: dict[str, Any]) -> None:
    from core.extensions.models import public_tool_name

    assert public_tool_name("fixture", "Read.file") == "external.fixture.Read.file"
    prefix = "external.fixture."
    assert len(public_tool_name("fixture", "a" * (128 - len(prefix)))) == 128
    for ext, remote in (("Fixture", "read"), ("a.b", "read"), (" a", "read"), ("fixture", "نام"),
                        ("fixture", ""), ("fixture", "a\n"), ("fixture", "a" * (129 - len(prefix)))):
        with pytest.raises(ValueError):
            public_tool_name(ext, remote)
    duplicate = {"schema_version": 1, "extensions": [definition, definition]}
    with pytest.raises(ValidationError):
        ExtensionConfig.model_validate_json(json.dumps(duplicate))
    model = ExtensionConfig.model_validate_json(json.dumps({"schema_version": 1, "extensions": [definition]}))
    assert tuple(model.configured_tool_policies()) == ("external.fixture.read_value",)
    with pytest.raises(ValueError):
        model.configured_tool_policies({"external.fixture.read_value"})
    definition["tools"]["read_value"]["remote_name"] = "other"
    with pytest.raises(ValidationError):
        parse(definition)


def test_accepted_schema_fingerprint_canonicalization_and_output_contract() -> None:
    from core.extensions.models import schema_fingerprint

    schema = {"type": "object", "properties": {"file.name": {"type": "string"}}}
    reordered = {"properties": schema["properties"], "type": "object"}
    assert schema_fingerprint(schema, None) == schema_fingerprint(reordered, None)
    assert schema_fingerprint(schema, None) != schema_fingerprint(schema, {"type": "object"})


def test_discovery_name_index_rejects_cross_page_duplicates_and_preserves_logical_identity() -> None:
    from itertools import chain

    from core.extensions.models import public_tool_name, public_tool_names

    assert public_tool_names("fixture", ["read", "Write"]) == ("external.fixture.Write", "external.fixture.read")
    with pytest.raises(ValueError):
        public_tool_names("fixture", chain(["read"], ["read"]))
    with pytest.raises(ValueError):
        public_tool_names("fixture", ["read"], {"external.fixture.read"})
    with pytest.raises(ValueError):
        public_tool_names("fixture", (f"t{i}" for i in range(129)))
    assert public_tool_name("old", "Read") != public_tool_name("new", "Read")
    assert public_tool_names("old", ["Read"]) == ("external.old.Read",)


def test_provider_hints_and_capability_metadata_authority() -> None:
    from core.extensions.models import (
        ExtensionCapabilityMetadata,
        ExtensionToolDescriptor,
        ProviderToolHints,
    )

    # Provider tool hints defaults
    hints = ProviderToolHints(read_only=True, idempotent=True, destructive=False)
    assert hints.read_only is True
    assert hints.idempotent is True
    assert hints.destructive is False
    assert hints.raw_hints == {}

    pol = ToolPolicy(remote_name="op", domain="testing", allow_call=True, mutation_class=MutationClass.READ_ONLY)
    desc = ExtensionToolDescriptor.create(
        extension_id="ext",
        provider="prov",
        remote_name="op",
        description="test",
        input_schema={"type": "object"},
        policy=pol,
        provider_hints=hints,
    )

    # External capability claiming built_in trust must be rejected
    with pytest.raises(ValueError, match="built_in claim denied"):
        desc.to_capability_metadata("built_in")

    # Valid restricted/trusted metadata projection
    meta = desc.to_capability_metadata(TrustClass.RESTRICTED)
    assert isinstance(meta, ExtensionCapabilityMetadata)
    assert meta.public_name == "external.ext.op"
    assert meta.trust_class is TrustClass.RESTRICTED
    assert meta.allow_call is True
    assert meta.mutation_class is MutationClass.READ_ONLY
    assert meta.provider_hints.read_only is True
    assert meta.classification_source == "operator_policy"

