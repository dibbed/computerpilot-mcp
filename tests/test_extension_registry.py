"""Tests for capability catalog registry, generation snapshots, and authoritative lookup."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

import pytest

from core.extensions.models import (
    LIMITS,
    ExtensionToolDescriptor,
)
from core.extensions.registry import (
    CatalogLimitExceededError,
    DuplicateToolIdentityError,
    ExtensionLease,
    ExtensionRegistry,
    ExtensionRegistryError,
    SchemaChangedRestartRequiredError,
    StaleGenerationError,
    TombstoneQuotaExceededError,
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
