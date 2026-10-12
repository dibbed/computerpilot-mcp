"""Tests for capability catalog registry, generation snapshots, and authoritative lookup."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

import pytest

from core.extensions.models import (
    LIMITS,
    ExtensionToolDescriptor,
    MutationClass,
    ProviderToolHints,
    TrustClass,
)
from core.extensions.registry import (
    ArgumentValidationResult,
    CatalogLimitExceededError,
    DuplicateToolIdentityError,
    ExtensionLease,
    ExtensionRegistry,
    ExtensionRegistryError,
    SchemaChangedRestartRequiredError,
    SchemaValidationResult,
    StaleGenerationError,
    TombstoneQuotaExceededError,
    evaluate_capability_trust_mutation,
    evaluate_tool_trust_mutation,
    validate_schema_subset,
    validate_tool_arguments,
)


def _make_tool(
    extension_id: str,
    remote_name: str,
    generation: int = 1,
    schema: Mapping[str, Any] | None = None,
    output_schema: Mapping[str, Any] | None = None,
    description: str = "A test tool",
) -> ExtensionToolDescriptor:
    input_s = schema if schema is not None else {"type": "object", "properties": {"param": {"type": "string"}}}
    return ExtensionToolDescriptor.create(
        extension_id=extension_id,
        provider=f"provider-{extension_id}",
        remote_name=remote_name,
        description=description,
        input_schema=input_s,
        output_schema=output_schema,
        catalog_generation=generation,
    )


def test_empty_registry_and_snapshot_defaults() -> None:
    registry = ExtensionRegistry()
    snapshot = registry.active_snapshot

    assert snapshot.manager_generation == 0
    assert snapshot.total_tools == 0
    assert snapshot.total_bytes == 0
    assert snapshot.tool_names == ()
    assert snapshot.get_tool("external.fake.tool") is None
    assert registry.has_tool("external.fake.tool") is False
    assert registry.manager_generation == 0
    assert registry.extension_generation("fake") == 0


def test_publish_single_extension_and_authoritative_lookup() -> None:
    registry = ExtensionRegistry()
    t1 = _make_tool("ext_a", "read_data", generation=1)
    t2 = _make_tool("ext_a", "list_data", generation=1)

    snapshot = registry.publish_extension_catalog("ext_a", 1, [t1, t2])

    assert snapshot.manager_generation == 1
    assert snapshot.total_tools == 2
    assert snapshot.total_bytes > 0
    assert snapshot.extension_generations["ext_a"] == 1

    # Deterministic alphabetical ordering
    assert snapshot.tool_names == ("external.ext_a.list_data", "external.ext_a.read_data")

    # Authoritative exact lookup
    lookup1 = registry.get_tool("external.ext_a.read_data")
    assert lookup1 is not None
    assert lookup1.remote_name == "read_data"
    assert lookup1.catalog_generation == 1

    # Non-exact lookup fails
    assert registry.get_tool("read_data") is None
    assert registry.get_tool("external.ext_a.unknown") is None
    assert registry.get_tool("external.ext_b.read_data") is None

    # Extension tools helper
    ext_tools = snapshot.extension_tools("ext_a")
    assert len(ext_tools) == 2
    assert snapshot.extension_tools("ext_b") == ()

    # Snapshot immutability
    with pytest.raises(TypeError):
        snapshot.tools["external.ext_a.hack"] = t1  # type: ignore[index]


def test_monotonic_generation_and_stale_rejection() -> None:
    registry = ExtensionRegistry()
    t1 = _make_tool("ext_a", "tool1", generation=1)
    registry.publish_extension_catalog("ext_a", 1, [t1])

    # Re-publishing same generation 1 fails
    with pytest.raises(StaleGenerationError, match="<= max published generation"):
        registry.publish_extension_catalog("ext_a", 1, [t1])

    # Publishing lower generation 0 fails
    with pytest.raises(StaleGenerationError):
        registry.publish_extension_catalog("ext_a", 0, [t1])

    # Active snapshot remains untouched
    assert registry.manager_generation == 1
    assert registry.active_snapshot.extension_generations["ext_a"] == 1


def test_failed_replacement_leaves_old_generation_intact_no_partial_publication() -> None:
    registry = ExtensionRegistry()
    t1 = _make_tool("ext_a", "valid1", generation=1)
    t2 = _make_tool("ext_a", "valid2", generation=1)
    registry.publish_extension_catalog("ext_a", 1, [t1, t2])

    # Candidate generation 2 with 1 valid tool and 1 broken tool (oversized description)
    t_good = _make_tool("ext_a", "valid1", generation=2)
    t_bad = _make_tool("ext_a", "broken", generation=2, description="x" * 200)

    with pytest.raises(CatalogLimitExceededError):
        registry.publish_extension_catalog("ext_a", 2, [t_good, t_bad])

    # Active snapshot MUST NOT be corrupted: generation 1 intact, no phantom tool
    snapshot = registry.active_snapshot
    assert snapshot.manager_generation == 1
    assert snapshot.extension_generations["ext_a"] == 1
    assert snapshot.total_tools == 2
    assert snapshot.get_tool("external.ext_a.valid1") is not None
    assert snapshot.get_tool("external.ext_a.valid1").catalog_generation == 1  # type: ignore[union-attr]
    assert snapshot.get_tool("external.ext_a.valid2") is not None
    assert snapshot.get_tool("external.ext_a.broken") is None


def test_multi_extension_and_duplicate_collision_rejection() -> None:
    registry = ExtensionRegistry(native_names=["run_pytest", "read_file"])

    t_a1 = _make_tool("ext_a", "tool_one", generation=1)
    registry.publish_extension_catalog("ext_a", 1, [t_a1])

    t_b1 = _make_tool("ext_b", "tool_two", generation=1)
    snapshot = registry.publish_extension_catalog("ext_b", 1, [t_b1])

    assert snapshot.manager_generation == 2
    assert snapshot.total_tools == 2
    assert snapshot.tool_names == ("external.ext_a.tool_one", "external.ext_b.tool_two")

    # Duplicate within candidate batch
    t_dup1 = _make_tool("ext_c", "dup_tool", generation=1)
    t_dup2 = _make_tool("ext_c", "dup_tool", generation=1)
    with pytest.raises(DuplicateToolIdentityError, match="duplicate tool name in candidate batch"):
        registry.publish_extension_catalog("ext_c", 1, [t_dup1, t_dup2])

    # Collision with native tool
    t_native = _make_tool("ext_c", "native_tool", generation=1)
    with pytest.raises(DuplicateToolIdentityError, match="collides with native tool"):
        registry.publish_extension_catalog("ext_c", 1, [t_native], native_names=["external.ext_c.native_tool"])

    # Collision with another active extension
    # If ext_c tries to create external.ext_a.tool_one directly:
    # Notice public_tool_name enforces external.<extension_id>.<remote_name>, so ext_c can't make external.ext_a
    # unless remote_name trick or descriptor manipulation:
    fake_desc = ExtensionToolDescriptor.create(
        extension_id="ext_a",
        provider="provider-fake",
        remote_name="tool_one",
        description="hacked",
        input_schema={"type": "object"},
        catalog_generation=1,
    )
    with pytest.raises(ExtensionRegistryError, match="does not match"):
        registry.publish_extension_catalog("ext_c", 1, [fake_desc])


def test_retire_extension_catalog() -> None:
    registry = ExtensionRegistry()
    t_a = _make_tool("ext_a", "tool_a", generation=1)
    t_b = _make_tool("ext_b", "tool_b", generation=1)
    registry.publish_extension_catalog("ext_a", 1, [t_a])
    registry.publish_extension_catalog("ext_b", 1, [t_b])

    assert registry.active_snapshot.total_tools == 2

    # Retire ext_a
    snapshot2 = registry.retire_extension_catalog("ext_a")
    assert snapshot2.manager_generation == 3
    assert snapshot2.total_tools == 1
    assert "ext_a" not in snapshot2.extension_generations
    assert registry.get_tool("external.ext_a.tool_a") is None
    assert registry.get_tool("external.ext_b.tool_b") is not None

    # Retiring again is a no-op that does not increment manager generation
    snapshot3 = registry.retire_extension_catalog("ext_a")
    assert snapshot3.manager_generation == 3

    # Retiring ext_b leaves catalog empty
    snapshot4 = registry.retire_extension_catalog("ext_b")
    assert snapshot4.manager_generation == 4
    assert snapshot4.total_tools == 0


def test_reconnect_same_schema_allowed_different_schema_rejected() -> None:
    registry = ExtensionRegistry()
    schema_v1 = {"type": "object", "properties": {"v": {"type": "string"}}}
    schema_v2 = {"type": "object", "properties": {"v": {"type": "integer"}}}

    t1 = _make_tool("ext_a", "query", generation=1, schema=schema_v1)
    registry.publish_extension_catalog("ext_a", 1, [t1])

    # Retire ext_a
    registry.retire_extension_catalog("ext_a")
    assert registry.get_tool("external.ext_a.query") is None

    # Reconnect with changed schema -> REJECTED with SchemaChangedRestartRequiredError
    t_changed = _make_tool("ext_a", "query", generation=2, schema=schema_v2)
    with pytest.raises(SchemaChangedRestartRequiredError, match=r"schema changed.*restart required"):
        registry.publish_extension_catalog("ext_a", 2, [t_changed])

    # Reconnect with SAME schema -> ALLOWED
    t_same = _make_tool("ext_a", "query", generation=2, schema=schema_v1)
    snapshot = registry.publish_extension_catalog("ext_a", 2, [t_same])
    assert snapshot.get_tool("external.ext_a.query") is not None
    assert snapshot.get_tool("external.ext_a.query").catalog_generation == 2  # type: ignore[union-attr]


def test_tombstone_quota_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = ExtensionRegistry()
    # Temporarily set total_tools to 3 to verify tombstone quota
    from core.extensions import registry as reg_mod

    monkeypatch.setattr(reg_mod, "LIMITS", dataclasses.replace(reg_mod.LIMITS, total_tools=3))

    t1 = _make_tool("ext_a", "tool1", generation=1)
    t2 = _make_tool("ext_a", "tool2", generation=1)
    t3 = _make_tool("ext_a", "tool3", generation=1)
    registry.publish_extension_catalog("ext_a", 1, [t1, t2, t3])

    # Retire ext_a
    registry.retire_extension_catalog("ext_a")

    # Now attempt to register a new identity "tool4" (would make 4 tombstones > 3)
    t4 = _make_tool("ext_b", "tool4", generation=1)
    with pytest.raises(TombstoneQuotaExceededError, match="tombstone quota"):
        registry.publish_extension_catalog("ext_b", 1, [t4])


def test_catalog_limits_enforced() -> None:
    registry = ExtensionRegistry()

    # Tools per extension limit
    many_tools = [_make_tool("ext_a", f"tool_{i}", generation=1) for i in range(LIMITS.tools_per_extension + 1)]
    with pytest.raises(CatalogLimitExceededError, match="per-extension limit"):
        registry.publish_extension_catalog("ext_a", 1, many_tools)

    # Schema size limit
    big_schema = {"type": "object", "properties": {"data": {"type": "string", "default": "x" * (LIMITS.schema_bytes + 10)}}}
    big_tool = _make_tool("ext_a", "big_schema", generation=1, schema=big_schema)
    with pytest.raises(CatalogLimitExceededError, match="schema bytes"):
        registry.publish_extension_catalog("ext_a", 1, [big_tool])

    # Description length limit
    desc_tool = _make_tool("ext_a", "long_desc", generation=1, description="x" * (LIMITS.description_chars + 1))
    with pytest.raises(CatalogLimitExceededError, match="description length"):
        registry.publish_extension_catalog("ext_a", 1, [desc_tool])


def test_lease_creation_and_validation() -> None:
    registry = ExtensionRegistry()
    t1 = _make_tool("ext_a", "call_api", generation=1)
    registry.publish_extension_catalog("ext_a", 1, [t1])

    # Lease creation
    lease = registry.create_lease("external.ext_a.call_api")
    assert lease is not None
    assert isinstance(lease, ExtensionLease)
    assert lease.public_name == "external.ext_a.call_api"
    assert lease.manager_generation == 1
    assert lease.catalog_generation == 1
    assert lease.schema_fingerprint == t1.schema_fingerprint

    # Valid lease
    assert registry.is_lease_valid(lease) is True

    # Lease for non-existent tool
    assert registry.create_lease("external.nonexistent.tool") is None

    # Invalidate lease via generation upgrade
    t1_v2 = _make_tool("ext_a", "call_api", generation=2)
    registry.publish_extension_catalog("ext_a", 2, [t1_v2])
    assert registry.is_lease_valid(lease) is False  # catalog_generation is now 2

    # Invalidate lease via retirement
    new_lease = registry.create_lease("external.ext_a.call_api")
    assert new_lease is not None
    assert registry.is_lease_valid(new_lease) is True
    registry.retire_extension_catalog("ext_a")
    assert registry.is_lease_valid(new_lease) is False


def test_schema_subset_allowed_and_rejected_keywords() -> None:
    # Valid schema with typical allowed keywords
    valid_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "title": "Search",
        "description": "Search items",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 100},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "status": {"type": "string", "enum": ["active", "archived"]},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    assert validate_schema_subset(valid_schema) == SchemaValidationResult(valid=True)

    # Boolean schema is valid
    assert validate_schema_subset(True) == SchemaValidationResult(valid=True)

    # Unrecognized $schema dialect
    bad_dialect = {"$schema": "https://json-schema.org/draft/2099/schema", "type": "object"}
    res_dialect = validate_schema_subset(bad_dialect)
    assert res_dialect.valid is False
    assert res_dialect.error_code == "unsupported_schema_dialect"

    # Unknown keyword rejected
    bad_keyword = {"type": "object", "customKeyword": 123}
    res_kw = validate_schema_subset(bad_keyword)
    assert res_kw.valid is False
    assert res_kw.error_code == "unsupported_keyword:customKeyword"

    # ReDoS regex keywords rejected
    bad_pattern = {"type": "object", "properties": {"code": {"type": "string", "pattern": "^[a-z]+$"}}}
    res_pattern = validate_schema_subset(bad_pattern)
    assert res_pattern.valid is False
    assert res_pattern.error_code == "unsupported_regex_keyword:pattern"

    bad_prop_pattern = {"type": "object", "patternProperties": {"^s_": {"type": "string"}}}
    res_prop_pattern = validate_schema_subset(bad_prop_pattern)
    assert res_prop_pattern.valid is False
    assert res_prop_pattern.error_code == "unsupported_regex_keyword:patternProperties"

    # Unsupported schema keywords ($id, $dynamicRef, etc.) rejected
    bad_id = {"$id": "https://example.com/schema.json", "type": "object"}
    res_id = validate_schema_subset(bad_id)
    assert res_id.valid is False
    assert res_id.error_code == "unsupported_schema_keyword:$id"


def test_schema_subset_remote_ref_rejected() -> None:
    # HTTP remote ref rejected
    remote_http = {"type": "object", "properties": {"user": {"$ref": "https://example.com/user.json"}}}
    res_http = validate_schema_subset(remote_http)
    assert res_http.valid is False
    assert res_http.error_code == "unsupported_remote_ref"

    # File remote ref rejected
    remote_file = {"type": "object", "properties": {"user": {"$ref": "file:///etc/passwd"}}}
    res_file = validate_schema_subset(remote_file)
    assert res_file.valid is False
    assert res_file.error_code == "unsupported_remote_ref"

    # Non-standard anchor ref rejected
    anchor_ref = {"type": "object", "properties": {"user": {"$ref": "#myAnchor"}}}
    res_anchor = validate_schema_subset(anchor_ref)
    assert res_anchor.valid is False
    assert res_anchor.error_code == "unsupported_remote_ref"


def test_schema_subset_local_defs_and_cyclic_detection() -> None:
    # Valid acyclic local ref
    valid_local = {
        "type": "object",
        "$defs": {
            "Address": {"type": "object", "properties": {"city": {"type": "string"}}},
        },
        "properties": {
            "billing": {"$ref": "#/$defs/Address"},
        },
    }
    assert validate_schema_subset(valid_local) == SchemaValidationResult(valid=True)

    # Unresolvable local ref
    unresolvable = {
        "type": "object",
        "$defs": {
            "Existing": {"type": "string"},
        },
        "properties": {
            "billing": {"$ref": "#/$defs/Missing"},
        },
    }
    res_missing = validate_schema_subset(unresolvable)
    assert res_missing.valid is False
    assert res_missing.error_code == "unresolvable_local_ref:#/$defs/Missing"

    # Cyclic local ref (A -> B -> A)
    cyclic = {
        "type": "object",
        "$defs": {
            "NodeA": {
                "type": "object",
                "properties": {"next": {"$ref": "#/$defs/NodeB"}},
            },
            "NodeB": {
                "type": "object",
                "properties": {"back": {"$ref": "#/$defs/NodeA"}},
            },
        },
        "properties": {
            "head": {"$ref": "#/$defs/NodeA"},
        },
    }
    res_cyclic = validate_schema_subset(cyclic)
    assert res_cyclic.valid is False
    assert res_cyclic.error_code == "cyclic_reference_detected"


def test_schema_subset_depth_and_composition_limits() -> None:
    # Composition width > 16 rejected
    wide_all_of = {
        "type": "object",
        "allOf": [{"type": "object"} for _ in range(LIMITS.composition_width + 1)],
    }
    res_wide = validate_schema_subset(wide_all_of)
    assert res_wide.valid is False
    assert res_wide.error_code == "schema_composition_width_exceeded"

    # Depth > 32 rejected
    deep_schema: dict[str, Any] = {"type": "string"}
    for _ in range(LIMITS.json_depth + 2):
        deep_schema = {"type": "object", "properties": {"nested": deep_schema}}

    res_deep = validate_schema_subset(deep_schema)
    assert res_deep.valid is False
    assert res_deep.error_code == "schema_depth_exceeded"


def test_validate_tool_arguments_preserves_file_name_and_types() -> None:
    schema = {
        "type": "object",
        "properties": {
            "file.name": {"type": "string", "minLength": 1},
            "retry_count": {"type": "integer", "minimum": 0, "maximum": 5},
            "enabled": {"type": "boolean"},
            "mode": {"type": "string", "enum": ["read", "write"]},
        },
        "required": ["file.name", "retry_count"],
        "additionalProperties": False,
    }

    # Valid arguments matching exact 'file.name' property
    good_args = {"file.name": "report.pdf", "retry_count": 3, "enabled": True, "mode": "read"}
    assert validate_tool_arguments(schema, good_args) == ArgumentValidationResult(valid=True)

    # Missing required argument
    missing_args = {"retry_count": 3}
    res_missing = validate_tool_arguments(schema, missing_args)
    assert res_missing.valid is False
    assert res_missing.error_code == "missing_required_argument:file.name"

    # Type mismatch
    type_err = {"file.name": 12345, "retry_count": 3}
    res_type = validate_tool_arguments(schema, type_err)
    assert res_type.valid is False
    assert res_type.error_code == "type_mismatch:file.name"

    # Enum violation
    enum_err = {"file.name": "test.txt", "retry_count": 1, "mode": "destroy"}
    res_enum = validate_tool_arguments(schema, enum_err)
    assert res_enum.valid is False
    assert res_enum.error_code == "enum_violation:mode"

    # Additional property rejected when additionalProperties is False
    unexpected = {"file.name": "test.txt", "retry_count": 1, "unexpected_key": "val"}
    res_unexpected = validate_tool_arguments(schema, unexpected)
    assert res_unexpected.valid is False
    assert res_unexpected.error_code == "unexpected_argument:unexpected_key"


def test_validate_tool_arguments_strictly_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Network I/O called during schema or argument validation!")

    monkeypatch.setattr(urllib.request, "urlopen", boom)

    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
    res = validate_tool_arguments(schema, {"name": "testing"})
    assert res.valid is True


def test_candidate_publishing_withheld_invalid_schema_tools() -> None:
    registry = ExtensionRegistry()

    # Tool 1 has a valid safe schema
    t1_valid = _make_tool(
        "ext_a",
        "valid_tool",
        generation=1,
        schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )

    # Tool 2 has an invalid schema with a forbidden regex pattern
    t2_invalid = _make_tool(
        "ext_a",
        "invalid_tool",
        generation=1,
        schema={"type": "object", "properties": {"q": {"type": "string", "pattern": "^[a-z]+$"}}},
    )

    # Publishing succeeds for valid tools; invalid tool is WITHHELD with explicit code
    snapshot = registry.publish_extension_catalog("ext_a", 1, [t1_valid, t2_invalid])

    # Valid tool is active and callable
    assert snapshot.total_tools == 1
    assert snapshot.has_tool("external.ext_a.valid_tool") is True
    assert registry.get_tool("external.ext_a.valid_tool") is not None

    # Invalid tool is NOT callable in snapshot.tools
    assert snapshot.has_tool("external.ext_a.invalid_tool") is False
    assert registry.get_tool("external.ext_a.invalid_tool") is None

    # Invalid tool is tracked in withheld_tools with explicit reason
    assert "external.ext_a.invalid_tool" in snapshot.withheld_tools
    assert snapshot.withheld_tools["external.ext_a.invalid_tool"] == "unsupported_regex_keyword:pattern"
    assert registry.get_withheld_reason("external.ext_a.invalid_tool") == "unsupported_regex_keyword:pattern"

    # Retiring ext_a cleans up both active and withheld tools
    snapshot2 = registry.retire_extension_catalog("ext_a")
    assert snapshot2.total_tools == 0
    assert len(snapshot2.withheld_tools) == 0


def test_evaluate_tool_trust_mutation_matrix() -> None:
    # 1. BUILT_IN claim always denied
    for mut in MutationClass:
        for allow in (True, False):
            for closed in (True, False):
                ok, err = evaluate_tool_trust_mutation(
                    "built_in", mut, allow, is_mutation_gate_closed=closed
                )
                assert ok is False
                assert err == "built_in_claim_denied"

    # 2. UNTRUSTED always forbidden
    for mut in MutationClass:
        for allow in (True, False):
            for closed in (True, False):
                ok, err = evaluate_tool_trust_mutation(
                    TrustClass.UNTRUSTED, mut, allow, is_mutation_gate_closed=closed
                )
                assert ok is False
                assert err == "untrusted_extension_call_forbidden"

    # 3. allow_call=False always disallowed under RESTRICTED and TRUSTED
    for trust in (TrustClass.RESTRICTED, TrustClass.TRUSTED):
        for mut in MutationClass:
            for closed in (True, False):
                ok, err = evaluate_tool_trust_mutation(trust, mut, False, is_mutation_gate_closed=closed)
                assert ok is False
                assert err == "operator_policy_disallowed"

    # 4. UNKNOWN mutation class always forbidden when allow_call=True
    for trust in (TrustClass.RESTRICTED, TrustClass.TRUSTED):
        for closed in (True, False):
            ok, err = evaluate_tool_trust_mutation(
                trust, MutationClass.UNKNOWN, True, is_mutation_gate_closed=closed
            )
            assert ok is False
            assert err == "unknown_mutation_class_forbidden"

    # 5. RESTRICTED trust class only permits READ_ONLY
    # READ_ONLY is allowed
    ok, err = evaluate_tool_trust_mutation(TrustClass.RESTRICTED, MutationClass.READ_ONLY, True, is_mutation_gate_closed=True)
    assert ok is True and err is None
    ok, err = evaluate_tool_trust_mutation(TrustClass.RESTRICTED, MutationClass.READ_ONLY, True, is_mutation_gate_closed=False)
    assert ok is True and err is None

    # All non-read_only mutations are forbidden under RESTRICTED
    for mut in (
        MutationClass.LOCAL_MUTATION,
        MutationClass.REMOTE_MUTATION,
        MutationClass.DESTRUCTIVE_LOCAL_MUTATION,
        MutationClass.DESTRUCTIVE_REMOTE_MUTATION,
    ):
        for closed in (True, False):
            ok, err = evaluate_tool_trust_mutation(TrustClass.RESTRICTED, mut, True, is_mutation_gate_closed=closed)
            assert ok is False
            assert err == "restricted_trust_mutation_forbidden"

    # 6. TRUSTED trust class
    # READ_ONLY is allowed
    ok, err = evaluate_tool_trust_mutation(TrustClass.TRUSTED, MutationClass.READ_ONLY, True, is_mutation_gate_closed=True)
    assert ok is True and err is None

    # Mutations when mutation gate is closed (pre-M10 default)
    for mut in (
        MutationClass.LOCAL_MUTATION,
        MutationClass.REMOTE_MUTATION,
        MutationClass.DESTRUCTIVE_LOCAL_MUTATION,
        MutationClass.DESTRUCTIVE_REMOTE_MUTATION,
    ):
        ok, err = evaluate_tool_trust_mutation(TrustClass.TRUSTED, mut, True, is_mutation_gate_closed=True)
        assert ok is False
        assert err == "mutation_gate_closed_pre_m10"

        # When mutation gate is open (post-M10 simulation)
        ok, err = evaluate_tool_trust_mutation(TrustClass.TRUSTED, mut, True, is_mutation_gate_closed=False)
        assert ok is True and err is None


def test_provider_hints_never_grant_authority_threat_model_r07() -> None:
    # A provider supplies deceptive hints claiming to be read-only, idempotent, and non-destructive
    deceptive_hints = ProviderToolHints(
        read_only=True,
        idempotent=True,
        destructive=False,
        raw_hints={"readOnlyHint": True, "claim": "harmless"},
    )

    # 1. Deceptive hints cannot bypass allow_call=False
    ok, err = evaluate_tool_trust_mutation(
        TrustClass.RESTRICTED,
        MutationClass.READ_ONLY,
        False,
        provider_hints=deceptive_hints,
    )
    assert ok is False
    assert err == "operator_policy_disallowed"

    # 2. Deceptive hints cannot bypass RESTRICTED mutation prohibition
    ok, err = evaluate_tool_trust_mutation(
        TrustClass.RESTRICTED,
        MutationClass.LOCAL_MUTATION,
        True,
        provider_hints=deceptive_hints,
    )
    assert ok is False
    assert err == "restricted_trust_mutation_forbidden"

    # 3. Deceptive hints cannot bypass UNTRUSTED extension prohibition
    ok, err = evaluate_tool_trust_mutation(
        TrustClass.UNTRUSTED,
        MutationClass.READ_ONLY,
        True,
        provider_hints=deceptive_hints,
    )
    assert ok is False
    assert err == "untrusted_extension_call_forbidden"

    # 4. Deceptive hints cannot make UNKNOWN mutation class permitted
    ok, err = evaluate_tool_trust_mutation(
        TrustClass.TRUSTED,
        MutationClass.UNKNOWN,
        True,
        provider_hints=deceptive_hints,
    )
    assert ok is False
    assert err == "unknown_mutation_class_forbidden"

    # 5. evaluate_capability_trust_mutation with ExtensionCapabilityMetadata
    tool = _make_tool(
        "ext_a",
        "query",
        generation=1,
    )
    # Metadata inherits operator policy
    meta = tool.to_capability_metadata(TrustClass.RESTRICTED)
    assert meta.classification_source == "operator_policy"
    # By default in _make_tool, policy has allow_call=False and mutation_class=UNKNOWN
    ok, err = evaluate_capability_trust_mutation(meta)
    assert ok is False
    assert err == "operator_policy_disallowed"


