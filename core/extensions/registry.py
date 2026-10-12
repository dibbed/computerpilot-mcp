"""Authoritative capability catalog registry and generation snapshot service."""

from __future__ import annotations

import threading
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType

from core.extensions.models import (
    LIMITS,
    ExtensionCatalogSnapshot,
    ExtensionToolDescriptor,
    public_tool_name,
    schema_fingerprint,
)


class ExtensionRegistryError(Exception):
    """Base exception for capability registry errors."""


class CatalogLimitExceededError(ExtensionRegistryError):
    """Raised when catalog tool count, bytes, or schema size exceeds limits."""


class DuplicateToolIdentityError(ExtensionRegistryError):
    """Raised on tool identity collision within or across extensions, or with native tools."""


class StaleGenerationError(ExtensionRegistryError):
    """Raised when candidate catalog generation is not strictly monotonic."""


class SchemaChangedRestartRequiredError(ExtensionRegistryError):
    """Raised when a tool's schema changes under an existing public name during the same Core runtime."""


class TombstoneQuotaExceededError(ExtensionRegistryError):
    """Raised when identity tombstone quota is exhausted."""


@dataclass(frozen=True, slots=True)
class ExtensionLease:
    """Admitted tool contract lease pinned to manager generation, catalog generation and schema fingerprint."""

    public_name: str
    manager_generation: int
    catalog_generation: int
    schema_fingerprint: str
    policy_revision: int = 0


class ExtensionRegistry:
    """Thread-safe authoritative registry of immutable capability generations."""

    def __init__(self, native_names: Collection[str] = ()) -> None:
        self._lock = threading.Lock()
        self._active_snapshot = ExtensionCatalogSnapshot.empty()
        self._runtime_fingerprints: dict[str, str] = {}
        self._max_published_generations: dict[str, int] = {}
        self._native_names: frozenset[str] = frozenset(native_names)

    @property
    def active_snapshot(self) -> ExtensionCatalogSnapshot:
        return self._active_snapshot

    @property
    def manager_generation(self) -> int:
        return self._active_snapshot.manager_generation

    def get_tool(self, public_name: str) -> ExtensionToolDescriptor | None:
        return self._active_snapshot.get_tool(public_name)

    def has_tool(self, public_name: str) -> bool:
        return self._active_snapshot.has_tool(public_name)

    @property
    def registered_identities(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._runtime_fingerprints.keys())

    def extension_generation(self, extension_id: str) -> int:
        return self._active_snapshot.extension_generations.get(extension_id, 0)

    def create_lease(self, public_name: str, policy_revision: int = 0) -> ExtensionLease | None:
        descriptor = self.get_tool(public_name)
        if descriptor is None:
            return None
        return ExtensionLease(
            public_name=public_name,
            manager_generation=self.manager_generation,
            catalog_generation=descriptor.catalog_generation,
            schema_fingerprint=descriptor.schema_fingerprint,
            policy_revision=policy_revision,
        )

    def is_lease_valid(self, lease: ExtensionLease) -> bool:
        descriptor = self.get_tool(lease.public_name)
        if descriptor is None:
            return False
        return (
            descriptor.catalog_generation == lease.catalog_generation
            and descriptor.schema_fingerprint == lease.schema_fingerprint
        )

    def validate_candidate(
        self,
        extension_id: str,
        candidate_generation: int,
        descriptors: Sequence[ExtensionToolDescriptor],
        native_names: Collection[str] = (),
    ) -> tuple[ExtensionToolDescriptor, ...]:
        """Validate a candidate catalog batch before publication without mutating active state."""
        with self._lock:
            return self._validate_candidate_locked(
                extension_id=extension_id,
                candidate_generation=candidate_generation,
                descriptors=descriptors,
                native_names=native_names,
            )

    def _validate_candidate_locked(
        self,
        extension_id: str,
        candidate_generation: int,
        descriptors: Sequence[ExtensionToolDescriptor],
        native_names: Collection[str] = (),
    ) -> tuple[ExtensionToolDescriptor, ...]:
        current_gen = self._max_published_generations.get(extension_id, 0)
        if candidate_generation <= current_gen:
            raise StaleGenerationError(
                f"candidate generation {candidate_generation} <= max published generation {current_gen} for {extension_id}"
            )

        if len(descriptors) > LIMITS.tools_per_extension:
            raise CatalogLimitExceededError(
                f"tool count {len(descriptors)} exceeds per-extension limit {LIMITS.tools_per_extension}"
            )

        combined_native = self._native_names | frozenset(native_names)
        seen_batch_names: set[str] = set()

        for desc in descriptors:
            if desc.identity.extension_id != extension_id:
                raise ExtensionRegistryError(
                    f"descriptor extension_id '{desc.identity.extension_id}' does not match '{extension_id}'"
                )
            if desc.catalog_generation != candidate_generation:
                raise ExtensionRegistryError(
                    f"descriptor catalog_generation {desc.catalog_generation} does not match candidate {candidate_generation}"
                )

            expected_public_name = public_tool_name(extension_id, desc.remote_name)
            if desc.public_name != expected_public_name:
                raise ExtensionRegistryError(
                    f"descriptor public_name '{desc.public_name}' does not match expected '{expected_public_name}'"
                )

            expected_fingerprint = schema_fingerprint(desc.input_schema, desc.output_schema)
            if desc.schema_fingerprint != expected_fingerprint:
                raise ExtensionRegistryError(
                    f"descriptor schema fingerprint mismatch for '{desc.public_name}'"
                )

            if desc.public_name in seen_batch_names:
                raise DuplicateToolIdentityError(
                    f"duplicate tool name in candidate batch: {desc.public_name}"
                )
            seen_batch_names.add(desc.public_name)

            if desc.public_name in combined_native:
                raise DuplicateToolIdentityError(
                    f"tool name collides with native tool: {desc.public_name}"
                )

            # Check collision with other extensions in active snapshot
            existing_tool = self._active_snapshot.tools.get(desc.public_name)
            if existing_tool is not None and existing_tool.identity.extension_id != extension_id:
                raise DuplicateToolIdentityError(
                    f"tool name collides with active extension '{existing_tool.identity.extension_id}': {desc.public_name}"
                )

            # Check individual limits
            if len(desc.description) > LIMITS.description_chars:
                raise CatalogLimitExceededError(
                    f"description length {len(desc.description)} exceeds limit {LIMITS.description_chars}"
                )
            if len(desc.input_schema_json) > LIMITS.schema_bytes:
                raise CatalogLimitExceededError(
                    f"input schema bytes {len(desc.input_schema_json)} exceeds limit {LIMITS.schema_bytes}"
                )
            if desc.output_schema_json is not None and len(desc.output_schema_json) > LIMITS.schema_bytes:
                raise CatalogLimitExceededError(
                    f"output schema bytes {len(desc.output_schema_json)} exceeds limit {LIMITS.schema_bytes}"
                )

            # Check schema drift against runtime tombstone
            recorded_fp = self._runtime_fingerprints.get(desc.public_name)
            if recorded_fp is not None and recorded_fp != desc.schema_fingerprint:
                raise SchemaChangedRestartRequiredError(
                    f"schema changed for tool '{desc.public_name}'; Core restart required"
                )

        # Check tombstone quota
        new_names = [d.public_name for d in descriptors if d.public_name not in self._runtime_fingerprints]
        if len(self._runtime_fingerprints) + len(new_names) > LIMITS.total_tools:
            raise TombstoneQuotaExceededError(
                f"identity tombstone quota of {LIMITS.total_tools} exceeded"
            )

        # Projected totals check
        other_tools = [d for d in self._active_snapshot.tools.values() if d.identity.extension_id != extension_id]
        projected_tools_count = len(other_tools) + len(descriptors)
        if projected_tools_count > LIMITS.total_tools:
            raise CatalogLimitExceededError(
                f"total tools {projected_tools_count} exceeds global limit {LIMITS.total_tools}"
            )

        projected_bytes = sum(d.byte_size for d in other_tools) + sum(d.byte_size for d in descriptors)
        if projected_bytes > LIMITS.catalog_bytes:
            raise CatalogLimitExceededError(
                f"total catalog bytes {projected_bytes} exceeds global limit {LIMITS.catalog_bytes}"
            )

        return tuple(sorted(descriptors, key=lambda d: d.public_name))

    def publish_extension_catalog(
        self,
        extension_id: str,
        candidate_generation: int,
        descriptors: Sequence[ExtensionToolDescriptor],
        native_names: Collection[str] = (),
    ) -> ExtensionCatalogSnapshot:
        """Atomically validate and publish a candidate catalog generation for an extension."""
        with self._lock:
            validated = self._validate_candidate_locked(
                extension_id=extension_id,
                candidate_generation=candidate_generation,
                descriptors=descriptors,
                native_names=native_names,
            )

            # Record runtime fingerprints for newly introduced tool names
            for desc in validated:
                if desc.public_name not in self._runtime_fingerprints:
                    self._runtime_fingerprints[desc.public_name] = desc.schema_fingerprint

            # Update max published generation
            self._max_published_generations[extension_id] = candidate_generation

            # Build new tools map
            other_tools = {
                k: v for k, v in self._active_snapshot.tools.items()
                if v.identity.extension_id != extension_id
            }
            for desc in validated:
                other_tools[desc.public_name] = desc

            sorted_tools = MappingProxyType(dict(sorted(other_tools.items())))
            total_bytes = sum(d.byte_size for d in sorted_tools.values())

            # Build new extension generations map
            new_ext_gens = dict(self._active_snapshot.extension_generations)
            new_ext_gens[extension_id] = candidate_generation
            sorted_ext_gens = MappingProxyType(dict(sorted(new_ext_gens.items())))

            new_snapshot = ExtensionCatalogSnapshot(
                manager_generation=self._active_snapshot.manager_generation + 1,
                extension_generations=sorted_ext_gens,
                tools=sorted_tools,
                total_tools=len(sorted_tools),
                total_bytes=total_bytes,
                observed_at=datetime.now(timezone.utc).isoformat(),
            )
            self._active_snapshot = new_snapshot
            return new_snapshot

    def retire_extension_catalog(self, extension_id: str) -> ExtensionCatalogSnapshot:
        """Atomically retire all tools of an extension from the active snapshot."""
        with self._lock:
            remaining_tools = {
                k: v for k, v in self._active_snapshot.tools.items()
                if v.identity.extension_id != extension_id
            }
            if (
                len(remaining_tools) == len(self._active_snapshot.tools)
                and extension_id not in self._active_snapshot.extension_generations
            ):
                return self._active_snapshot

            sorted_tools = MappingProxyType(dict(sorted(remaining_tools.items())))
            total_bytes = sum(d.byte_size for d in sorted_tools.values())

            new_ext_gens = dict(self._active_snapshot.extension_generations)
            new_ext_gens.pop(extension_id, None)
            sorted_ext_gens = MappingProxyType(dict(sorted(new_ext_gens.items())))

            new_snapshot = ExtensionCatalogSnapshot(
                manager_generation=self._active_snapshot.manager_generation + 1,
                extension_generations=sorted_ext_gens,
                tools=sorted_tools,
                total_tools=len(sorted_tools),
                total_bytes=total_bytes,
                observed_at=datetime.now(timezone.utc).isoformat(),
            )
            self._active_snapshot = new_snapshot
            return new_snapshot

    def reset_for_new_runtime(self) -> None:
        """Reset runtime fingerprints and active snapshot for test isolation between simulated Core runtimes."""
        with self._lock:
            self._active_snapshot = ExtensionCatalogSnapshot.empty()
            self._runtime_fingerprints.clear()
            self._max_published_generations.clear()
