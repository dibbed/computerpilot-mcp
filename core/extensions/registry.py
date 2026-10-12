"""Authoritative capability catalog registry, schema normalization, and generation service."""

from __future__ import annotations

import threading
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, cast

from jsonschema.validators import validator_for
from referencing import Registry
from referencing.exceptions import NoSuchResource

from core.extensions.models import (
    LIMITS,
    ExtensionCapabilityMetadata,
    ExtensionCatalogSnapshot,
    ExtensionDefinition,
    ExtensionState,
    ExtensionToolDescriptor,
    MutationClass,
    ProviderToolHints,
    TrustClass,
    public_tool_name,
    schema_fingerprint,
)
from core.platform import normalize_system

ALLOWED_SCHEMA_KEYWORDS: frozenset[str] = frozenset({
    "$schema",
    "$defs",
    "definitions",
    "$ref",
    "title",
    "description",
    "default",
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "minProperties",
    "maxProperties",
    "enum",
    "const",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minItems",
    "maxItems",
    "uniqueItems",
    "allOf",
    "anyOf",
    "oneOf",
    "not",
})

RECOGNIZED_SCHEMA_DIALECTS: frozenset[str] = frozenset({
    "http://json-schema.org/draft-07/schema#",
    "https://json-schema.org/draft/2020-12/schema",
    "http://json-schema.org/draft-04/schema#",
})


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
class SchemaValidationResult:
    """Result of validating a JSON Schema against the safe subset."""

    valid: bool
    error_code: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ArgumentValidationResult:
    """Result of validating tool call arguments against accepted schema."""

    valid: bool
    error_code: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ToolEligibilityResult:
    """Result of pure deterministic tool eligibility evaluation across ordered gates."""

    eligible: bool
    rejection_code: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ExtensionLease:
    """Admitted tool contract lease pinned to manager generation, catalog generation and schema fingerprint."""

    public_name: str
    manager_generation: int
    catalog_generation: int
    schema_fingerprint: str
    policy_revision: int = 0


def _find_local_refs(sub: Any, found_set: set[str]) -> None:
    if isinstance(sub, Mapping):
        if "$ref" in sub and isinstance(sub["$ref"], str):
            r = sub["$ref"]
            if r.startswith(("#/$defs/", "#/definitions/")):
                found_set.add(r.split("/")[-1])
        for v in sub.values():
            _find_local_refs(v, found_set)
    elif isinstance(sub, (list, tuple)):
        for item in sub:
            _find_local_refs(item, found_set)


def validate_schema_subset(schema: Any) -> SchemaValidationResult:
    """Validate that a JSON Schema adheres to the strict safe subset without external network resolution."""
    if isinstance(schema, bool):
        return SchemaValidationResult(valid=True)
    if not isinstance(schema, Mapping):
        return SchemaValidationResult(
            valid=False,
            error_code="invalid_schema_type",
            message="Schema must be a JSON object or boolean",
        )

    # Check dialect if present
    if "$schema" in schema:
        dialect = schema["$schema"]
        if not isinstance(dialect, str) or dialect not in RECOGNIZED_SCHEMA_DIALECTS:
            return SchemaValidationResult(
                valid=False,
                error_code="unsupported_schema_dialect",
                message=f"Unsupported or unrecognized $schema dialect: '{dialect}'",
            )

    # Collect local definitions for ref resolution and cycle detection
    local_defs: dict[str, Any] = {}
    if "$defs" in schema and isinstance(schema["$defs"], Mapping):
        local_defs.update(schema["$defs"])
    if "definitions" in schema and isinstance(schema["definitions"], Mapping):
        local_defs.update(schema["definitions"])

    ref_targets: list[str] = []
    node_count = 0

    def walk(node: Any, depth: int) -> SchemaValidationResult | None:
        nonlocal node_count
        node_count += 1
        if node_count > LIMITS.schema_nodes:
            return SchemaValidationResult(
                valid=False,
                error_code="schema_node_count_exceeded",
                message=f"Schema node count exceeds ceiling of {LIMITS.schema_nodes}",
            )
        if depth > LIMITS.json_depth:
            return SchemaValidationResult(
                valid=False,
                error_code="schema_depth_exceeded",
                message=f"Schema nesting depth {depth} exceeds ceiling of {LIMITS.json_depth}",
            )

        if isinstance(node, bool):
            return None
        if not isinstance(node, Mapping):
            return None

        # Check all keywords in this object
        for key, val in node.items():
            if key not in ALLOWED_SCHEMA_KEYWORDS:
                if key in ("$id", "id", "$dynamicRef", "$dynamicAnchor", "$anchor"):
                    return SchemaValidationResult(
                        valid=False,
                        error_code=f"unsupported_schema_keyword:{key}",
                        message=f"Schema keyword '{key}' is forbidden",
                    )
                if key in ("pattern", "patternProperties"):
                    return SchemaValidationResult(
                        valid=False,
                        error_code=f"unsupported_regex_keyword:{key}",
                        message=f"Regex keyword '{key}' is forbidden to prevent ReDoS CPU exhaustion",
                    )
                return SchemaValidationResult(
                    valid=False,
                    error_code=f"unsupported_keyword:{key}",
                    message=f"Schema keyword '{key}' is not permitted in safe subset",
                )

            # Check composition width
            if key in ("allOf", "anyOf", "oneOf"):
                if isinstance(val, (list, tuple)):
                    if len(val) > LIMITS.composition_width:
                        return SchemaValidationResult(
                            valid=False,
                            error_code="schema_composition_width_exceeded",
                            message=f"Composition width {len(val)} exceeds ceiling of {LIMITS.composition_width}",
                        )
                    for item in val:
                        res = walk(item, depth + 1)
                        if res is not None:
                            return res

            elif key == "$ref":
                if not isinstance(val, str):
                    return SchemaValidationResult(
                        valid=False,
                        error_code="invalid_ref",
                        message="$ref must be a string",
                    )
                if val.startswith(("http://", "https://", "//", "urn:", "file:", "ftp:", "ws:", "wss:")):
                    return SchemaValidationResult(
                        valid=False,
                        error_code="unsupported_remote_ref",
                        message=f"Remote network $ref '{val}' is forbidden",
                    )
                if not (val.startswith("#/$defs/") or val.startswith("#/definitions/")):
                    return SchemaValidationResult(
                        valid=False,
                        error_code="unsupported_remote_ref",
                        message=f"Only local #/$defs/ or #/definitions/ references are permitted: '{val}'",
                    )
                ref_targets.append(val)

            elif key in ("properties", "$defs", "definitions"):
                if isinstance(val, Mapping):
                    for sub_val in val.values():
                        res = walk(sub_val, depth + 1)
                        if res is not None:
                            return res

            elif key in ("items", "additionalProperties", "not"):
                if isinstance(val, (Mapping, bool)):
                    res = walk(val, depth + 1)
                    if res is not None:
                        return res

        return None

    root_res = walk(schema, 0)
    if root_res is not None:
        return root_res

    # Validate local refs exist
    for ref_str in ref_targets:
        target_name = ref_str.split("/")[-1]
        if target_name not in local_defs:
            return SchemaValidationResult(
                valid=False,
                error_code=f"unresolvable_local_ref:{ref_str}",
                message=f"Local schema definition '{target_name}' referenced by '{ref_str}' does not exist",
            )

    # Detect cycles in local definitions graph
    def_refs: dict[str, set[str]] = {}
    for d_name, d_body in local_defs.items():
        found: set[str] = set()
        _find_local_refs(d_body, found)
        def_refs[d_name] = found

    visited: set[str] = set()
    rec_stack: set[str] = set()

    def has_cycle(node_name: str) -> bool:
        visited.add(node_name)
        rec_stack.add(node_name)
        for neighbor in def_refs.get(node_name, set()):
            if neighbor not in visited:
                if has_cycle(neighbor):
                    return True
            elif neighbor in rec_stack:
                return True
        rec_stack.remove(node_name)
        return False

    for d_name in local_defs:
        if d_name not in visited:
            if has_cycle(d_name):
                return SchemaValidationResult(
                    valid=False,
                    error_code="cyclic_reference_detected",
                    message=f"Cyclic $ref chain detected involving definition '{d_name}'",
                )

    try:
        validator_for(dict(schema)).check_schema(dict(schema))
    except Exception:
        return SchemaValidationResult(False, "invalid_schema", "The schema definition is invalid.")
    return SchemaValidationResult(valid=True)


def _no_schema_retrieval(uri: str) -> Any:
    raise NoSuchResource(uri)


def validate_tool_value(schema: Mapping[str, Any], value: Any) -> ArgumentValidationResult:
    """Apply every accepted schema rule through the installed no-network validator."""
    if not validate_schema_subset(schema).valid:
        return ArgumentValidationResult(False, "schema_unsupported", "The schema is unsupported.")
    try:
        validator_cls = validator_for(dict(schema))
        validator_cls.check_schema(dict(schema))
        # referencing generates its keyword-only constructor through attrs at runtime.
        registry = cast(Any, Registry)(retrieve=_no_schema_retrieval)
        validator = validator_cls(dict(schema), registry=registry)
        error = next(validator.iter_errors(value), None)
        if error is None:
            return ArgumentValidationResult(valid=True)
        name = ".".join(str(part) for part in error.path)
        rule = error.validator
        code = {"type": "type_mismatch", "enum": "enum_violation", "const": "const_violation"}.get(rule, "schema_violation")
        if rule == "required" and isinstance(error.instance, Mapping):
            name = next((key for key in error.validator_value if key not in error.instance), name)
            code = "missing_required_argument"
        elif rule == "additionalProperties" and isinstance(error.instance, Mapping):
            properties = error.schema.get("properties", {})
            name = next((key for key in error.instance if key not in properties), name)
            code = "unexpected_argument"
        return ArgumentValidationResult(False, f"{code}:{name}", "The value does not match the accepted schema.")
    except Exception:
        return ArgumentValidationResult(False, "schema_invalid", "The schema cannot be safely validated.")


def validate_tool_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> ArgumentValidationResult:
    if not isinstance(arguments, Mapping):
        return ArgumentValidationResult(False, "invalid_arguments_type", "Tool arguments must be a JSON object.")
    return validate_tool_value(schema, dict(arguments))


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

    def get_withheld_reason(self, public_name: str) -> str | None:
        return self._active_snapshot.get_withheld_reason(public_name)

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
    ) -> tuple[tuple[ExtensionToolDescriptor, ...], dict[str, str]]:
        """Validate candidate catalog batch, returning (accepted_descriptors, withheld_reasons)."""
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
    ) -> tuple[tuple[ExtensionToolDescriptor, ...], dict[str, str]]:
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

        accepted_list: list[ExtensionToolDescriptor] = []
        withheld_dict: dict[str, str] = {}

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

            # Validate schema safe subset
            in_res = validate_schema_subset(desc.input_schema)
            if not in_res.valid:
                withheld_dict[desc.public_name] = in_res.error_code or "unsupported_input_schema"
                continue

            if desc.output_schema is not None:
                out_res = validate_schema_subset(desc.output_schema)
                if not out_res.valid:
                    withheld_dict[desc.public_name] = out_res.error_code or "unsupported_output_schema"
                    continue

            # Check schema drift against runtime tombstone
            recorded_fp = self._runtime_fingerprints.get(desc.public_name)
            if recorded_fp is not None and recorded_fp != desc.schema_fingerprint:
                raise SchemaChangedRestartRequiredError(
                    f"schema changed for tool '{desc.public_name}'; Core restart required"
                )

            accepted_list.append(desc)

        # Check tombstone quota for newly introduced accepted names
        new_names = [d.public_name for d in accepted_list if d.public_name not in self._runtime_fingerprints]
        if len(self._runtime_fingerprints) + len(new_names) > LIMITS.total_tools:
            raise TombstoneQuotaExceededError(
                f"identity tombstone quota of {LIMITS.total_tools} exceeded"
            )

        # Projected totals check on accepted tools
        other_tools = [d for d in self._active_snapshot.tools.values() if d.identity.extension_id != extension_id]
        projected_tools_count = len(other_tools) + len(accepted_list)
        if projected_tools_count > LIMITS.total_tools:
            raise CatalogLimitExceededError(
                f"total tools {projected_tools_count} exceeds global limit {LIMITS.total_tools}"
            )

        projected_bytes = sum(d.byte_size for d in other_tools) + sum(d.byte_size for d in accepted_list)
        if projected_bytes > LIMITS.catalog_bytes:
            raise CatalogLimitExceededError(
                f"total catalog bytes {projected_bytes} exceeds global limit {LIMITS.catalog_bytes}"
            )

        sorted_accepted = tuple(sorted(accepted_list, key=lambda d: d.public_name))
        return sorted_accepted, withheld_dict

    def publish_extension_catalog(
        self,
        extension_id: str,
        candidate_generation: int,
        descriptors: Sequence[ExtensionToolDescriptor],
        native_names: Collection[str] = (),
    ) -> ExtensionCatalogSnapshot:
        """Atomically validate and publish a candidate catalog generation for an extension."""
        with self._lock:
            accepted, withheld = self._validate_candidate_locked(
                extension_id=extension_id,
                candidate_generation=candidate_generation,
                descriptors=descriptors,
                native_names=native_names,
            )

            # Record runtime fingerprints for newly introduced accepted names
            for desc in accepted:
                if desc.public_name not in self._runtime_fingerprints:
                    self._runtime_fingerprints[desc.public_name] = desc.schema_fingerprint

            # Update max published generation
            self._max_published_generations[extension_id] = candidate_generation

            # Build new tools map
            other_tools = {
                k: v for k, v in self._active_snapshot.tools.items()
                if v.identity.extension_id != extension_id
            }
            for desc in accepted:
                other_tools[desc.public_name] = desc

            sorted_tools = MappingProxyType(dict(sorted(other_tools.items())))
            total_bytes = sum(d.byte_size for d in sorted_tools.values())

            # Build new withheld tools map
            other_withheld = {
                k: v for k, v in self._active_snapshot.withheld_tools.items()
                if not k.startswith(f"external.{extension_id}.")
            }
            other_withheld.update(withheld)
            sorted_withheld = MappingProxyType(dict(sorted(other_withheld.items())))

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
                withheld_tools=sorted_withheld,
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
            remaining_withheld = {
                k: v for k, v in self._active_snapshot.withheld_tools.items()
                if not k.startswith(f"external.{extension_id}.")
            }

            has_tools = len(remaining_tools) != len(self._active_snapshot.tools)
            has_withheld = len(remaining_withheld) != len(self._active_snapshot.withheld_tools)
            has_gen = extension_id in self._active_snapshot.extension_generations

            if not has_tools and not has_withheld and not has_gen:
                return self._active_snapshot

            sorted_tools = MappingProxyType(dict(sorted(remaining_tools.items())))
            sorted_withheld = MappingProxyType(dict(sorted(remaining_withheld.items())))
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
                withheld_tools=sorted_withheld,
            )
            self._active_snapshot = new_snapshot
            return new_snapshot

    def reset_for_new_runtime(self) -> None:
        """Reset runtime fingerprints and active snapshot for test isolation between simulated Core runtimes."""
        with self._lock:
            self._active_snapshot = ExtensionCatalogSnapshot.empty()
            self._runtime_fingerprints.clear()
            self._max_published_generations.clear()

    def evaluate_eligibility(
        self,
        tool: ExtensionToolDescriptor | str,
        *,
        definition: ExtensionDefinition | None = None,
        runtime_state: ExtensionState | str = ExtensionState.HEALTHY,
        active_profile: str | None = None,
        platform: str | None = None,
        resolved_env_refs: Collection[str] | None = None,
        is_mutation_gate_closed: bool = True,
        is_recovery_available: bool = False,
        trust_class: TrustClass | str | None = None,
    ) -> ToolEligibilityResult:
        """Evaluate tool eligibility against active snapshot and caller context."""
        with self._lock:
            snap = self._active_snapshot
        return evaluate_tool_eligibility(
            tool,
            snapshot=snap,
            definition=definition,
            runtime_state=runtime_state,
            active_profile=active_profile,
            platform=platform,
            resolved_env_refs=resolved_env_refs,
            is_mutation_gate_closed=is_mutation_gate_closed,
            is_recovery_available=is_recovery_available,
            trust_class=trust_class,
        )

    def eligible_tools(
        self,
        *,
        definitions: Mapping[str, ExtensionDefinition] | None = None,
        runtime_states: Mapping[str, ExtensionState | str] | None = None,
        active_profile: str | None = None,
        platform: str | None = None,
        resolved_env_refs: Collection[str] | None = None,
        is_mutation_gate_closed: bool = True,
        is_recovery_available: bool = False,
        default_trust_class: TrustClass | str | None = None,
    ) -> tuple[ExtensionToolDescriptor, ...]:
        """Return tuple of all currently eligible tools in active snapshot."""
        with self._lock:
            snap = self._active_snapshot
        return filter_eligible_tools(
            snap,
            definitions=definitions,
            runtime_states=runtime_states,
            active_profile=active_profile,
            platform=platform,
            resolved_env_refs=resolved_env_refs,
            is_mutation_gate_closed=is_mutation_gate_closed,
            is_recovery_available=is_recovery_available,
            default_trust_class=default_trust_class,
        )


def evaluate_tool_trust_mutation(
    trust_class: TrustClass | str,
    mutation_class: MutationClass | str,
    allow_call: bool,
    *,
    is_mutation_gate_closed: bool = True,
    provider_hints: ProviderToolHints | None = None,
) -> tuple[bool, str | None]:
    """Authoritatively evaluate tool call eligibility against Trust x Mutation x AllowCall matrix.

    Provider hints are non-authoritative diagnostic annotations and cannot grant permissions,
    override operator policy, or elevate trust/mutation status.
    """
    trust_val = trust_class.value if isinstance(trust_class, TrustClass) else str(trust_class)
    mut_val = mutation_class.value if isinstance(mutation_class, MutationClass) else str(mutation_class)

    if trust_val == "built_in":
        return False, "built_in_claim_denied"

    if trust_val == TrustClass.UNTRUSTED.value:
        return False, "untrusted_extension_call_forbidden"

    if not allow_call:
        return False, "operator_policy_disallowed"

    if mut_val == MutationClass.UNKNOWN.value:
        return False, "unknown_mutation_class_forbidden"

    if trust_val == TrustClass.RESTRICTED.value:
        if mut_val != MutationClass.READ_ONLY.value:
            return False, "restricted_trust_mutation_forbidden"

    if mut_val != MutationClass.READ_ONLY.value:
        if is_mutation_gate_closed:
            return False, "mutation_gate_closed_pre_m10"

    return True, None


def evaluate_capability_trust_mutation(
    metadata: ExtensionCapabilityMetadata,
    *,
    is_mutation_gate_closed: bool = True,
) -> tuple[bool, str | None]:
    """Authoritatively evaluate capability metadata against Trust x Mutation x AllowCall matrix."""
    return evaluate_tool_trust_mutation(
        metadata.trust_class,
        metadata.mutation_class,
        metadata.allow_call,
        is_mutation_gate_closed=is_mutation_gate_closed,
        provider_hints=metadata.provider_hints,
    )


def evaluate_tool_eligibility(
    tool: ExtensionToolDescriptor | str,
    *,
    snapshot: ExtensionCatalogSnapshot | None = None,
    definition: ExtensionDefinition | None = None,
    runtime_state: ExtensionState | str = ExtensionState.HEALTHY,
    active_profile: str | None = None,
    platform: str | None = None,
    resolved_env_refs: Collection[str] | None = None,
    is_mutation_gate_closed: bool = True,
    is_recovery_available: bool = False,
    trust_class: TrustClass | str | None = None,
) -> ToolEligibilityResult:
    """Authoritatively and deterministically evaluate tool eligibility across ordered gates.

    Order of gates:
    1. Snapshot / Tool presence & schema validation
    2. Configuration & Launch approval
    3. Runtime state / Live health
    4. Platform match
    5. Active profile match
    6. Authentication / Required environment references
    7. Tool policy & Trust
    8. Mutation & Recovery (fail closed pre-M10)

    No protocol I/O is performed.
    """
    # Gate 1: Snapshot / Tool presence & schema
    desc: ExtensionToolDescriptor
    if isinstance(tool, str):
        if snapshot is None:
            return ToolEligibilityResult(False, "missing_snapshot", "Cannot evaluate tool name without active catalog snapshot")
        withheld_reason = snapshot.get_withheld_reason(tool)
        if withheld_reason is not None:
            return ToolEligibilityResult(False, "schema_withheld", f"Tool schema was withheld: {withheld_reason}")
        resolved_desc = snapshot.get_tool(tool)
        if resolved_desc is None:
            return ToolEligibilityResult(False, "tool_not_found", f"Tool '{tool}' is not in active catalog snapshot")
        desc = resolved_desc
    else:
        desc = tool
        if snapshot is not None:
            if desc.public_name in snapshot.withheld_tools:
                reason_msg = f"Tool schema was withheld: {snapshot.withheld_tools[desc.public_name]}"
                return ToolEligibilityResult(False, "schema_withheld", reason_msg)
            ext_gen = snapshot.extension_generations.get(desc.identity.extension_id)
            if ext_gen is None or desc.catalog_generation != ext_gen:
                return ToolEligibilityResult(False, "stale_generation", "Tool catalog generation does not match active snapshot generation")
            if not snapshot.has_tool(desc.public_name):
                return ToolEligibilityResult(False, "tool_not_found", f"Tool '{desc.public_name}' is not in active catalog snapshot")

    # Gate 2: Configuration & Launch approval (if definition provided)
    if definition is not None:
        if not definition.launch_approved:
            msg = f"Extension '{definition.extension_id}' has not been approved for launch"
            return ToolEligibilityResult(False, "launch_not_approved", msg)
        if not definition.enabled:
            return ToolEligibilityResult(False, "extension_disabled", f"Extension '{definition.extension_id}' is disabled in configuration")

    # Gate 3: Runtime State / Live Health
    state_val = runtime_state.value if isinstance(runtime_state, ExtensionState) else str(runtime_state)
    if state_val != ExtensionState.HEALTHY.value:
        return ToolEligibilityResult(False, "runtime_not_healthy", f"Extension runtime state is '{state_val}', not healthy")

    # Gate 4: Platform match
    if platform is not None and definition is not None:
        norm_plat = normalize_system(platform)
        if norm_plat not in definition.platforms:
            msg = f"Platform '{norm_plat}' is not supported by extension platforms {definition.platforms}"
            return ToolEligibilityResult(False, "platform_mismatch", msg)

    # Gate 5: Active profile match
    if active_profile is not None and active_profile != "all":
        if active_profile not in desc.policy.profiles:
            msg = f"Tool '{desc.public_name}' is not permitted under active profile '{active_profile}'"
            return ToolEligibilityResult(False, "profile_mismatch", msg)

    # Gate 6: Authentication / Required Environment References
    if desc.policy.required_env_refs:
        available_refs = frozenset(resolved_env_refs) if resolved_env_refs is not None else frozenset()
        missing = [r for r in desc.policy.required_env_refs if r not in available_refs]
        if missing:
            msg = f"Required environment references missing: {', '.join(sorted(missing))}"
            return ToolEligibilityResult(False, "missing_auth_refs", msg)

    # Gate 7: Tool policy & Trust
    eff_trust = definition.trust_class if definition is not None else (trust_class or TrustClass.UNTRUSTED)
    ok, err = evaluate_tool_trust_mutation(
        eff_trust,
        desc.policy.mutation_class,
        desc.policy.allow_call,
        is_mutation_gate_closed=is_mutation_gate_closed,
        provider_hints=desc.provider_hints,
    )
    if not ok:
        return ToolEligibilityResult(False, err, f"Trust/mutation policy rejection: {err}")

    # Gate 8: Mutation & Recovery (if mutation gate is open in post-M10 simulation)
    mut_val = desc.policy.mutation_class.value if isinstance(desc.policy.mutation_class, MutationClass) else str(desc.policy.mutation_class)
    if mut_val != MutationClass.READ_ONLY.value:
        if not is_mutation_gate_closed and not is_recovery_available:
            return ToolEligibilityResult(False, "recovery_unavailable", "Recovery mechanism is not available for mutating tool")

    return ToolEligibilityResult(True, None, None)


def filter_eligible_tools(
    snapshot: ExtensionCatalogSnapshot,
    *,
    definitions: Mapping[str, ExtensionDefinition] | None = None,
    runtime_states: Mapping[str, ExtensionState | str] | None = None,
    active_profile: str | None = None,
    platform: str | None = None,
    resolved_env_refs: Collection[str] | None = None,
    is_mutation_gate_closed: bool = True,
    is_recovery_available: bool = False,
    default_trust_class: TrustClass | str | None = None,
) -> tuple[ExtensionToolDescriptor, ...]:
    """Filter snapshot tools to only those eligible under given context, preserving deterministic ordering."""
    defs = definitions or {}
    states = runtime_states or {}
    eligible: list[ExtensionToolDescriptor] = []
    for desc in snapshot.tools.values():
        ext_id = desc.identity.extension_id
        defn = defs.get(ext_id)
        r_state = states.get(ext_id, ExtensionState.HEALTHY)
        res = evaluate_tool_eligibility(
            desc,
            snapshot=snapshot,
            definition=defn,
            runtime_state=r_state,
            active_profile=active_profile,
            platform=platform,
            resolved_env_refs=resolved_env_refs,
            is_mutation_gate_closed=is_mutation_gate_closed,
            is_recovery_available=is_recovery_available,
            trust_class=default_trust_class,
        )
        if res.eligible:
            eligible.append(desc)
    return tuple(eligible)


