"""Strict operator definitions and immutable accepted capability data."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from core.execution_models import RecoverabilityClass, RiskClass
from core.platform import PlatformName
from core.tool_profiles import ALL_DOMAINS, PROFILE_DOMAINS


@dataclass(frozen=True, slots=True)
class ExtensionLimits:
    """Provisional Core ceilings, measured/frozen by roadmap M14 rather than providers."""

    configured: int = 16
    enabled: int = 8
    parallel_startup: int = 2
    tools_per_extension: int = 128
    total_tools: int = 512
    config_bytes: int = 256 * 1024
    environment_bytes: int = 32 * 1024
    environment_value_bytes: int = 8 * 1024
    catalog_bytes: int = 2 * 1024 * 1024
    schema_bytes: int = 32 * 1024
    description_chars: int = 180
    json_depth: int = 32
    schema_nodes: int = 2048
    composition_width: int = 16
    frame_bytes: int = 2 * 1024 * 1024
    argument_bytes: int = 128 * 1024
    inline_bytes: int = 128 * 1024
    artifact_bytes: int = 2 * 1024 * 1024
    calls_per_extension: int = 2
    global_calls: int = 8
    notification_pending: int = 16
    recent_errors: int = 8
    stderr_bytes: int = 4 * 1024
    health_bytes: int = 32 * 1024
    startup_sec: float = 15
    handshake_sec: float = 5
    discovery_sec: float = 5
    call_sec: float = 30
    shutdown_sec: float = 10
    restart_attempts: int = 3
    restart_window_sec: int = 600
    unresolved_operations: int = 128
    terminal_history: int = 1000
    terminal_days: int = 30


LIMITS = ExtensionLimits()
BoundedArgument = Annotated[str, Field(max_length=2048)]
ReferenceName = Annotated[str, Field(min_length=1, max_length=128)]
IntentName = Annotated[str, Field(min_length=1, max_length=200)]


def public_tool_name(extension_id: str, remote_name: str) -> str:
    """Validate injective identity without case folding, truncation or aliases."""
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", extension_id, flags=re.ASCII):
        raise ValueError("invalid extension identity")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", remote_name, flags=re.ASCII):
        raise ValueError("invalid remote tool identity")
    name = f"external.{extension_id}.{remote_name}"
    if len(name) > 128:
        raise ValueError("composed tool identity exceeds limit")
    return name


def schema_fingerprint(input_schema: Mapping[str, Any], output_schema: Mapping[str, Any] | None) -> str:
    """Hash already accepted schemas; this helper does not authorize or validate schemas."""
    data = json.dumps([dict(input_schema), None if output_schema is None else dict(output_schema)],
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def public_tool_names(extension_id: str, remote_names: Iterable[str], native_names: Collection[str] = ()) -> tuple[str, ...]:
    """Index a bounded discovery sequence, including duplicates across flattened pages."""
    names: set[str] = set()
    for remote_name in remote_names:
        if len(names) >= LIMITS.tools_per_extension:
            raise ValueError("tool count limit")
        name = public_tool_name(extension_id, remote_name)
        if name in names or name in native_names:
            raise ValueError("tool identity collision")
        names.add(name)
    return tuple(sorted(names))


class TrustClass(str, Enum):
    UNTRUSTED = "untrusted"
    RESTRICTED = "restricted"
    TRUSTED = "trusted"


class MutationClass(str, Enum):
    READ_ONLY = "read_only"
    LOCAL_MUTATION = "local_mutation"
    REMOTE_MUTATION = "remote_mutation"
    DESTRUCTIVE_LOCAL_MUTATION = "destructive_local_mutation"
    DESTRUCTIVE_REMOTE_MUTATION = "destructive_remote_mutation"
    UNKNOWN = "unknown"


class ExtensionState(str, Enum):
    CONFIGURED = "configured"
    DISABLED = "disabled"
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"
    FAILED = "failed"
    STOPPING = "stopping"


class StrictDefinition(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", validate_default=True)


class EnvironmentReference(StrictDefinition):
    kind: Literal["env"] = "env"
    name: ReferenceName


class ExtensionEnvironment(StrictDefinition):
    inherit: tuple[ReferenceName, ...] = Field(default=(), max_length=32)
    values: Mapping[ReferenceName, BoundedArgument] = Field(default_factory=dict, max_length=32)
    references: Mapping[ReferenceName, EnvironmentReference] = Field(default_factory=dict, max_length=32)

    @field_validator("values", "references")
    @classmethod
    def freeze_maps(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return MappingProxyType(dict(value))

    @field_serializer("values", "references")
    def serialize_maps(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return dict(value)


class ExtensionTimeouts(StrictDefinition):
    startup_sec: float = Field(default=LIMITS.startup_sec, gt=0, le=LIMITS.startup_sec)
    handshake_sec: float = Field(default=LIMITS.handshake_sec, gt=0, le=LIMITS.handshake_sec)
    discovery_sec: float = Field(default=LIMITS.discovery_sec, gt=0, le=LIMITS.discovery_sec)
    call_sec: float = Field(default=LIMITS.call_sec, gt=0, le=LIMITS.call_sec)
    shutdown_sec: float = Field(default=LIMITS.shutdown_sec, gt=0, le=LIMITS.shutdown_sec)


class ToolPolicy(StrictDefinition):
    remote_name: str = Field(min_length=1, max_length=128)
    domain: str = Field(min_length=1, max_length=64)
    mutation_class: MutationClass = MutationClass.UNKNOWN
    risk_class: RiskClass = RiskClass.HIGH
    profiles: tuple[str, ...] = Field(default=(), max_length=len(PROFILE_DOMAINS))
    intents: tuple[IntentName, ...] = Field(default=(), max_length=25)
    allow_call: bool = False
    determinism: int = Field(default=1, ge=1, le=4)
    recoverability: RecoverabilityClass = RecoverabilityClass.WEAK
    capability_confidence: int = Field(default=1, ge=1, le=5)
    cost_tier: int = Field(default=5, ge=1, le=5)
    latency_tier: int = Field(default=5, ge=1, le=5)
    required_env_refs: tuple[ReferenceName, ...] = Field(default=(), max_length=32)

    @field_validator("domain")
    @classmethod
    def known_domain(cls, value: str) -> str:
        if value not in ALL_DOMAINS:
            raise ValueError("unknown domain")
        return value

    @field_validator("profiles")
    @classmethod
    def known_profiles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(name not in PROFILE_DOMAINS for name in value):
            raise ValueError("unknown or duplicate profile")
        return value


class ExtensionDefinition(StrictDefinition):
    extension_id: str = Field(min_length=1, max_length=32)
    display_name: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=64)
    transport: Literal["stdio"] = "stdio"
    command: str = Field(min_length=1, max_length=32767)
    arguments: tuple[BoundedArgument, ...] = Field(default=(), max_length=64)
    working_directory: str = Field(min_length=1, max_length=32767)
    enabled: bool = False
    launch_approved: bool = False
    trust_class: TrustClass = TrustClass.UNTRUSTED
    platforms: tuple[PlatformName, ...] = Field(min_length=1, max_length=3)
    environment: ExtensionEnvironment = Field(default_factory=ExtensionEnvironment)
    timeouts: ExtensionTimeouts = Field(default_factory=ExtensionTimeouts)
    tools: Mapping[str, ToolPolicy] = Field(default_factory=dict, max_length=LIMITS.tools_per_extension)

    @model_validator(mode="after")
    def validate_identity(self) -> ExtensionDefinition:
        public_tool_name(self.extension_id, "identity_check")
        for name, policy in self.tools.items():
            if name != policy.remote_name:
                raise ValueError("tool policy key must match exact remote name")
            public_tool_name(self.extension_id, name)
        return self

    @field_validator("tools")
    @classmethod
    def freeze_tools(cls, value: Mapping[str, ToolPolicy]) -> Mapping[str, ToolPolicy]:
        return MappingProxyType(dict(value))

    @field_serializer("tools")
    def serialize_tools(self, value: Mapping[str, ToolPolicy]) -> dict[str, ToolPolicy]:
        return dict(value)


class ExtensionConfig(StrictDefinition):
    schema_version: Literal[1]
    extensions: tuple[ExtensionDefinition, ...] = Field(default=(), max_length=LIMITS.configured)

    @model_validator(mode="after")
    def validate_identities(self) -> ExtensionConfig:
        ids = [definition.extension_id for definition in self.extensions]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate extension identity")
        if sum(definition.enabled for definition in self.extensions) > LIMITS.enabled:
            raise ValueError("enabled extension limit")
        return self

    def configured_tool_policies(self, native_names: Collection[str] = ()) -> Mapping[str, ToolPolicy]:
        """An immutable configuration index, never a claim of live tool availability."""
        result: dict[str, ToolPolicy] = {}
        for definition in self.extensions:
            for policy in definition.tools.values():
                name = public_tool_name(definition.extension_id, policy.remote_name)
                if name in result or name in native_names:
                    raise ValueError("tool identity collision")
                result[name] = policy
        return MappingProxyType(dict(sorted(result.items())))

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("schema version must be an integer")
        return value


@dataclass(frozen=True, slots=True)
class ExtensionIdentity:
    extension_id: str
    provider: str
    source_type: Literal["external_mcp"] = "external_mcp"


@dataclass(frozen=True, slots=True)
class ProviderToolHints:
    """Non-authoritative provider diagnostic hints retained without permission grant."""

    read_only: bool | None = None
    idempotent: bool | None = None
    destructive: bool | None = None
    raw_hints: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class ExtensionCapabilityMetadata:
    """Immutable authoritative capability metadata combining operator policy and diagnostic hints."""

    public_name: str
    extension_id: str
    domain: str
    mutation_class: MutationClass
    risk_class: RiskClass
    recoverability: RecoverabilityClass
    trust_class: TrustClass
    allow_call: bool
    determinism: int
    capability_confidence: int
    cost_tier: int
    latency_tier: int
    profiles: tuple[str, ...]
    intents: tuple[str, ...]
    required_env_refs: tuple[str, ...]
    source_type: Literal["external_mcp"] = "external_mcp"
    provider_hints: ProviderToolHints = ProviderToolHints()
    classification_source: str = "operator_policy"


@dataclass(frozen=True, slots=True)
class ExtensionToolDescriptor:
    public_name: str
    remote_name: str
    identity: ExtensionIdentity
    description: str
    input_schema_json: bytes
    output_schema_json: bytes | None
    policy: ToolPolicy
    catalog_generation: int
    schema_fingerprint: str
    provider_hints: ProviderToolHints = ProviderToolHints()

    @property
    def input_schema(self) -> dict[str, Any]:
        # Accepted canonical bytes are immutable; callers receive independent schema copies.
        return cast(dict[str, Any], json.loads(self.input_schema_json))

    @property
    def output_schema(self) -> dict[str, Any] | None:
        return None if self.output_schema_json is None else cast(dict[str, Any], json.loads(self.output_schema_json))

    @property
    def byte_size(self) -> int:
        output_len = 0 if self.output_schema_json is None else len(self.output_schema_json)
        return (
            len(self.input_schema_json)
            + output_len
            + len(self.description.encode("utf-8"))
            + len(self.public_name.encode("utf-8"))
        )

    @property
    def extension_id(self) -> str:
        return self.identity.extension_id

    def to_capability_metadata(self, trust_class: TrustClass | str) -> ExtensionCapabilityMetadata:
        if str(trust_class) == "built_in":
            raise ValueError("built_in claim denied for external capabilities")
        resolved_trust = trust_class if isinstance(trust_class, TrustClass) else TrustClass(trust_class)
        return ExtensionCapabilityMetadata(
            public_name=self.public_name,
            extension_id=self.identity.extension_id,
            domain=self.policy.domain,
            mutation_class=self.policy.mutation_class,
            risk_class=self.policy.risk_class,
            recoverability=self.policy.recoverability,
            trust_class=resolved_trust,
            allow_call=self.policy.allow_call,
            determinism=self.policy.determinism,
            capability_confidence=self.policy.capability_confidence,
            cost_tier=self.policy.cost_tier,
            latency_tier=self.policy.latency_tier,
            profiles=self.policy.profiles,
            intents=self.policy.intents,
            required_env_refs=self.policy.required_env_refs,
            source_type=self.identity.source_type,
            provider_hints=self.provider_hints,
            classification_source="operator_policy",
        )

    @classmethod
    def create(
        cls,
        *,
        extension_id: str,
        provider: str,
        remote_name: str,
        description: str,
        input_schema: Mapping[str, Any],
        output_schema: Mapping[str, Any] | None = None,
        policy: ToolPolicy | None = None,
        catalog_generation: int = 1,
        provider_hints: ProviderToolHints | None = None,
    ) -> ExtensionToolDescriptor:
        pub_name = public_tool_name(extension_id, remote_name)
        in_bytes = json.dumps(
            dict(input_schema),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        out_bytes = (
            None
            if output_schema is None
            else json.dumps(
                dict(output_schema),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        )
        fingerprint = schema_fingerprint(input_schema, output_schema)
        pol = policy or ToolPolicy(remote_name=remote_name, domain="testing")
        hints = provider_hints or ProviderToolHints()
        return cls(
            public_name=pub_name,
            remote_name=remote_name,
            identity=ExtensionIdentity(extension_id=extension_id, provider=provider),
            description=description,
            input_schema_json=in_bytes,
            output_schema_json=out_bytes,
            policy=pol,
            catalog_generation=catalog_generation,
            schema_fingerprint=fingerprint,
            provider_hints=hints,
        )


@dataclass(frozen=True, slots=True)
class ExtensionCatalogSnapshot:
    manager_generation: int
    extension_generations: Mapping[str, int]
    tools: Mapping[str, ExtensionToolDescriptor]
    total_tools: int
    total_bytes: int
    observed_at: str
    withheld_tools: Mapping[str, str] = MappingProxyType({})

    @classmethod
    def empty(cls) -> ExtensionCatalogSnapshot:
        return cls(
            manager_generation=0,
            extension_generations=MappingProxyType({}),
            tools=MappingProxyType({}),
            total_tools=0,
            total_bytes=0,
            observed_at="",
            withheld_tools=MappingProxyType({}),
        )

    def get_tool(self, public_name: str) -> ExtensionToolDescriptor | None:
        return self.tools.get(public_name)

    def has_tool(self, public_name: str) -> bool:
        return public_name in self.tools

    def get_withheld_reason(self, public_name: str) -> str | None:
        return self.withheld_tools.get(public_name)

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(self.tools.keys())

    def extension_tools(self, extension_id: str) -> tuple[ExtensionToolDescriptor, ...]:
        return tuple(desc for desc in self.tools.values() if desc.identity.extension_id == extension_id)


@dataclass(frozen=True, slots=True)
class ExtensionHealth:
    extension_id: str
    provider: str
    enabled: bool
    state: ExtensionState
    routable: bool
    discovered_count: int
    approved_count: int
    observed_at: str
    generation: int
    last_success_at: str | None = None
    last_error_code: str | None = None
    last_error_at: str | None = None
    restart_count: int = 0
