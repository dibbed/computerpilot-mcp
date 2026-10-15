"""Recovery domain models for durable operation reconciliation."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OperationState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_RESULT = "waiting_result"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    RECONCILING = "reconciling"
    ACKNOWLEDGED = "acknowledged"
    UNRESOLVABLE = "unresolvable"


@dataclass(slots=True)
class Postcondition:
    kind: str
    expected: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Evidence:
    source: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OperationRecordV2:
    operation_id: str
    operation_type: str
    state: OperationState
    target: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    postcondition: Postcondition | None = None
    evidence: list[Evidence] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class NativeUIATarget:
    """Numeric UIA identity only; never persist control text, titles or values."""

    pid: int | None = None
    process_created: float | None = None
    window_handle: int | None = None
    element_handle: int | None = None
    element_runtime_id: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        for value in (self.pid, self.window_handle, self.element_handle):
            if value is not None and (type(value) is not int or not 0 < value < 2**64):
                raise ValueError("UIA identity requires bounded positive integers.")
        created = self.process_created
        if created is not None and (
            type(created) not in {int, float} or not 0 < created < 10**12 or not math.isfinite(created)
        ):
            raise ValueError("UIA process identity requires a finite creation time.")
        if not isinstance(self.element_runtime_id, tuple) or len(self.element_runtime_id) > 32 or any(
            type(value) is not int or not -(2**63) <= value < 2**63 for value in self.element_runtime_id
        ):
            raise ValueError("UIA runtime identity requires at most 32 bounded integers.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "process_created": self.process_created,
            "window_handle": self.window_handle,
            "element_handle": self.element_handle,
            "element_runtime_id": list(self.element_runtime_id),
            "identity_status": "complete" if (
                self.pid and self.process_created and self.window_handle and self.element_runtime_id
            ) else "partial",
        }

    def label(self) -> str:
        runtime = ",".join(str(value) for value in self.element_runtime_id)
        return f"windows_uia:pid={self.pid};window={self.window_handle};element={self.element_handle};runtime={runtime}"


@dataclass(frozen=True, slots=True)
class ExternalRequestCorrelation:
    key_id: str
    digest: str
    algorithm: str = "hmac-sha256-runtime-v1"
    scope: str = "runtime_only"

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "key_id": self.key_id,
            "digest": self.digest,
            "scope": self.scope,
        }


@dataclass(frozen=True, slots=True)
class ExternalOperationMetadata:
    extension_id: str
    provider: str
    public_name: str
    remote_name: str
    catalog_generation: int
    schema_fingerprint: str
    mutation_class: str
    policy_revision: str | None = None
    dispatch_stage: str = "prepared"
    external_outcome: str | None = None
    reason_code: str | None = None
    receipt_id: str | None = None
    correlation: ExternalRequestCorrelation | None = None
    external_metadata_version: int = 1

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "external_metadata_version": self.external_metadata_version,
            "extension_id": self.extension_id,
            "provider": self.provider,
            "public_name": self.public_name,
            "remote_name": self.remote_name,
            "catalog_generation": self.catalog_generation,
            "schema_fingerprint": self.schema_fingerprint,
            "mutation_class": self.mutation_class,
            "dispatch_stage": self.dispatch_stage,
        }
        if self.policy_revision is not None:
            payload["policy_revision"] = self.policy_revision
        if self.external_outcome is not None:
            payload["external_outcome"] = self.external_outcome
        if self.reason_code is not None:
            payload["reason_code"] = self.reason_code
        if self.receipt_id is not None:
            payload["receipt_id"] = self.receipt_id
        if self.correlation is not None:
            payload["correlation"] = self.correlation.as_dict()
        return payload
