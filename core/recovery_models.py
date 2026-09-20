"""Recovery domain models for durable operation reconciliation."""

from __future__ import annotations

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
