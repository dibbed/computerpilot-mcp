"""Typed wire envelopes that preserve the existing native response shapes."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictBool, StrictInt, field_validator, model_validator

from core.errors import ToolError
from core.execution_models import RecoverabilityClass, RiskClass, RouteClass
from core.workflow_models import StepState, WorkflowState


def _compact_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Compact generated output schemas without touching payloads or literal data."""

    for key in ("title", "description", "default", "examples"):
        schema.pop(key, None)
    for key in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
        for child in schema.get(key, {}).values():
            if isinstance(child, dict):
                _compact_output_schema(child)
    for key in ("items", "contains", "propertyNames", "additionalProperties", "unevaluatedProperties",
                "unevaluatedItems", "not", "if", "then", "else"):
        child = schema.get(key)
        if isinstance(child, dict):
            _compact_output_schema(child)
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        for child in schema.get(key, []):
            if isinstance(child, dict):
                _compact_output_schema(child)
    values = [schema["const"]] if "const" in schema else schema.get("enum")
    if schema.get("type") == "string" and isinstance(values, list) and all(isinstance(value, str) for value in values):
        schema.pop("type")
    if set(schema) == {"anyOf"} and len(schema["anyOf"]) == 2:
        first, second = schema["anyOf"]
        value = second if first == {"type": "null"} else first if second == {"type": "null"} else None
        type_only_keys = {
            "type", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
            "minLength", "maxLength", "pattern", "minItems", "maxItems", "uniqueItems", "items",
            "prefixItems", "contains", "minContains", "maxContains", "minProperties", "maxProperties",
            "properties", "patternProperties", "additionalProperties", "propertyNames", "required",
            "dependentRequired", "dependentSchemas", "unevaluatedProperties", "unevaluatedItems",
        }
        if (isinstance(value, dict) and value.get("type") in ("boolean", "integer", "number", "string", "array", "object")
                and set(value) <= type_only_keys):
            schema.clear()
            schema.update(value)
            schema["type"] = [value["type"], "null"]
    return schema


class WireModel(BaseModel):
    """Common success/failure envelope; unknown legacy fields remain visible."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    ok: StrictBool
    operation: str | None = None
    error: str | None = None
    message: str | None = None
    hint: str | None = None

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)
        branches: list[tuple[bool, frozenset[str], frozenset[str]]] = [
            (False, frozenset({"operation", "error", "message"}), frozenset()),
        ]
        required: frozenset[str] = getattr(cls, "success_required_fields", frozenset())
        if required:
            branches.append((True, required, getattr(cls, "nullable_success_fields", frozenset())))
        schema.setdefault("allOf", []).extend(
            {
                "if": {"properties": {"ok": {"const": success}}, "required": ["ok"]},
                "then": {"required": sorted(fields), "patternProperties": {
                    "^(?:" + "|".join(re.escape(name) for name in sorted(fields - nullable)) + r")(?![\s\S])":
                        {"not": {"type": "null"}},
                } if fields - nullable else {}},
            }
            for success, fields, nullable in branches
        )
        return _compact_output_schema(schema)

    @model_validator(mode="after")
    def _require_failure_identity(self) -> WireModel:
        if self.ok:
            return self
        missing = [
            name
            for name in ("operation", "error", "message")
            if name not in self.model_fields_set or getattr(self, name) is None
        ]
        if missing:
            raise ValueError("Failure output is missing its legacy envelope fields.")
        return self

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("exclude_unset", True)
        return super().model_dump(*args, **kwargs)


class NativeFailure(BaseModel):
    """The unchanged legacy native failure envelope."""

    model_config = ConfigDict(extra="allow")

    ok: Literal[False]
    operation: str
    error: str
    message: str
    hint: str | None = None


_Wire = TypeVar("_Wire", bound=BaseModel)
_Root = TypeVar("_Root")


class WireRootModel(RootModel[_Root]):
    """Model a legacy union without introducing a result wrapper or null fields."""

    model_config = ConfigDict(json_schema_extra={"type": "object"})

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return _compact_output_schema(super().model_json_schema(*args, **kwargs))

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("exclude_unset", True)
        return super().model_dump(*args, **kwargs)


class SuccessWireModel(WireModel):
    """Envelope whose success branch must carry every declared legacy field."""

    success_required_fields: ClassVar[frozenset[str]] = frozenset()
    nullable_success_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _require_success_fields(self) -> SuccessWireModel:
        if not self.ok:
            return self
        missing = sorted(
            name for name in self.success_required_fields
            if name not in self.model_fields_set or (getattr(self, name) is None and name not in self.nullable_success_fields)
        )
        if missing:
            raise ValueError("Success output is missing declared fields: " + ", ".join(missing))
        return self


def validate_tool_output(model: type[_Wire], value: Any, *, operation: str) -> dict[str, Any]:
    """Validate at the registry boundary and return the unchanged wire dictionary."""

    try:
        json.dumps(value, allow_nan=False)
        validated = model.model_validate(value)
        return validated.model_dump(mode="json", by_alias=True, exclude_unset=True)
    except (ValueError, TypeError):
        raise ToolError(
            "output_contract_violation",
            f"{operation} output does not satisfy its declared contract.",
        ) from None


JobStatus = Literal["queued", "running", "orphaned", "succeeded", "failed", "cancelled", "timed_out", "interrupted"]


class JobStatusResult(SuccessWireModel):
    nullable_success_fields = frozenset({"queue_deadline", "pid", "exit_code", "error"})
    success_required_fields = frozenset({
        "job_id", "status", "created", "updated", "version", "queue_deadline", "pid", "exit_code",
        "cancel_requested", "error",
    })

    job_id: str | None = None
    status: JobStatus | None = None
    created: Annotated[float, Field(allow_inf_nan=False)] | None = None
    updated: Annotated[float, Field(allow_inf_nan=False)] | None = None
    version: Annotated[StrictInt, Field(ge=1)] | None = None
    queue_deadline: Annotated[float, Field(allow_inf_nan=False)] | None = None
    pid: Annotated[StrictInt, Field(gt=0)] | None = None
    exit_code: StrictInt | None = None
    cancel_requested: Literal[0, 1] | None = None

class WorkflowStepSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    step_index: Annotated[StrictInt, Field(ge=0)]
    state: StepState
    attempts: Annotated[StrictInt, Field(ge=0)]
    started_at: str | None
    finished_at: str | None
    evidence: Any
    error: str | None


class WorkflowSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    workflow_id: str
    state: WorkflowState
    current_step: StrictInt
    version: Annotated[StrictInt, Field(ge=1)]
    definition: dict[str, Any]
    inputs: dict[str, Any]
    execution_compatible: StrictBool
    execution_definition_hash: str | None
    execution_mode: Literal["sequential", "dag"]
    graph_contract_version: Literal[0, 1]
    topological_order: list[int]
    max_parallel_steps: Annotated[StrictInt, Field(ge=1)]
    effective_parallel_steps: Annotated[StrictInt, Field(ge=1)]
    total_timeout_sec: float | None
    active_admission_count: Annotated[StrictInt, Field(ge=0)]
    ready_step_indices: list[int]
    active_step_indices: list[int]
    blocked_step_indices: list[int]
    completed_step_count: Annotated[StrictInt, Field(ge=0)]
    cancel_requested_at: str | None
    cancel_reason: str | None
    created_at: str
    updated_at: str
    last_error: str | None
    steps: list[WorkflowStepSnapshot]


class WorkflowStatusResult(WireRootModel[WorkflowSnapshot | NativeFailure]):
    pass


class FullHealth(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: Literal[True]
    health_status: Literal["healthy", "degraded", "unhealthy", "stale"]
    snapshot_schema_version: Literal[1]
    runtime_generation: str
    collected_at: str
    collected_monotonic: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    server: str
    version: str
    tool_profile: str
    tool_count: Annotated[StrictInt, Field(ge=0)]
    unique_tool_names: StrictBool
    snapshot_truncated: StrictBool = False

    @field_validator("collected_at")
    @classmethod
    def _validate_collection_time(cls, value: str) -> str:
        if datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError("Health collection time requires a timezone.")
        return value


class BoundedHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: Literal[False]
    health_status: Literal["unavailable"]
    snapshot_truncated: Literal[True]
    snapshot_reason: Literal["payload_limit"]


class UnavailableHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: Literal[True]
    health_status: Literal["unavailable"]
    snapshot_reason: Literal["runtime_not_started"]
    server: str
    version: str
    tool_profile: str
    tool_count: Annotated[StrictInt, Field(ge=0)]
    unique_tool_names: StrictBool


class HealthResult(WireRootModel[FullHealth | BoundedHealth | UnavailableHealth | NativeFailure]):
    pass


class RouteCandidateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: RouteClass
    representative_tool: str
    determinism: Annotated[StrictInt, Field(ge=1, le=5)]
    risk_class: RiskClass
    recoverability: RecoverabilityClass
    capability_confidence: Annotated[StrictInt, Field(ge=1, le=5)]
    cost_tier: Annotated[StrictInt, Field(ge=1, le=5)]
    latency_tier: Annotated[StrictInt, Field(ge=1, le=5)]
    fallback_level: Annotated[StrictInt, Field(ge=0, le=10)]
    capability_checks: list[str]
    supported: StrictBool
    reason: str
    rejection_code: str | None
    preserves_macros: StrictBool
    source_type: Literal["external_mcp"] | None = None
    extension_id: str | None = None
    provider: str | None = None
    catalog_generation: Annotated[StrictInt, Field(ge=0)] | None = None
    classification_source: Literal["operator_policy"] | None = None


class RouteRecommendationSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: StrictBool
    intent: str
    selected_route: RouteClass | None
    representative_tool: str | None
    candidate_count: Annotated[StrictInt, Field(ge=0)]
    selection_reason: str
    fallback_level: Annotated[StrictInt, Field(ge=0, le=10)] | None
    capability_checks: list[str]
    risk_class: RiskClass | None
    router_policy_version: str
    candidates: list[RouteCandidateSnapshot] | None = None
    rejected_candidate_count: Annotated[StrictInt, Field(ge=0)] | None = None
    truncated: StrictBool | None = None
    source_type: Literal["external_mcp"] | None = None
    extension_id: str | None = None
    provider: str | None = None
    catalog_generation: Annotated[StrictInt, Field(ge=1)] | None = None
    classification_source: Literal["operator_policy"] | None = None

    @model_validator(mode="after")
    def _validate_route_selection(self) -> RouteRecommendationSnapshot:
        if self.ok != (self.selected_route is not None):
            raise ValueError("Recommendation outcome must match the selected route.")
        if self.ok and (self.representative_tool is None or self.candidate_count == 0):
            raise ValueError("A selected route requires a tool and an eligible candidate.")
        return self


class RouteRecommendation(WireRootModel[RouteRecommendationSnapshot | NativeFailure]):
    pass
