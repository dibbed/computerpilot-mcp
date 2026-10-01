"""Immutable models for deterministic execution-route planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class RouteClass(str, Enum):
    NATIVE_FILESYSTEM = "native.filesystem"
    NATIVE_CODE = "native.code"
    NATIVE_GIT = "native.git"
    NATIVE_PROCESS = "native.process"
    NATIVE_SYSTEM = "native.system"
    NATIVE_EXCEL = "native.excel"
    NATIVE_DOCX = "native.docx"
    NATIVE_PDF = "native.pdf"
    SEMANTIC_BROWSER = "semantic.browser"
    SEMANTIC_WINDOWS_UIA = "semantic.windows_uia"
    VISUAL_DESKTOP = "visual.desktop"
    RAW_DESKTOP = "raw.desktop"


class RiskClass(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RecoverabilityClass(str, Enum):
    WEAK = "weak"
    MEDIUM = "medium"
    STRONG = "strong"


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    name: str
    destructive: bool = False
    semantic_ambiguous: bool = False
    semantic_stale: bool = False
    requires_macro_preservation: bool = False
    allow_raw_desktop: bool = False
    preferred_tool: str | None = None

    def __post_init__(self) -> None:
        normalized = self.name.strip().casefold()
        if not normalized or len(normalized) > 200:
            raise ValueError("execution intent name must be between 1 and 200 characters")
        object.__setattr__(self, "name", normalized)
        if self.preferred_tool is not None:
            preferred = self.preferred_tool.strip()
            if not preferred or len(preferred) > 200:
                raise ValueError("preferred_tool must be between 1 and 200 characters")
            object.__setattr__(self, "preferred_tool", preferred)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "destructive": self.destructive,
            "semantic_ambiguous": self.semantic_ambiguous,
            "semantic_stale": self.semantic_stale,
            "requires_macro_preservation": self.requires_macro_preservation,
            "allow_raw_desktop": self.allow_raw_desktop,
            "preferred_tool": self.preferred_tool,
        }


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    route: RouteClass
    representative_tool: str
    determinism: int
    risk: RiskClass
    recoverability: RecoverabilityClass
    capability_confidence: int
    cost_tier: int
    latency_tier: int
    fallback_level: int
    capability_checks: tuple[str, ...] = ()
    supported: bool = True
    reason: str = ""
    rejection_code: str | None = None
    preserves_macros: bool = False

    def __post_init__(self) -> None:
        if not self.representative_tool or len(self.representative_tool) > 200:
            raise ValueError("representative_tool must be between 1 and 200 characters")
        for name, value in (
            ("determinism", self.determinism),
            ("capability_confidence", self.capability_confidence),
            ("cost_tier", self.cost_tier),
            ("latency_tier", self.latency_tier),
        ):
            if not 1 <= value <= 5:
                raise ValueError(f"{name} must be between 1 and 5")
        if not 0 <= self.fallback_level <= 10:
            raise ValueError("fallback_level must be between 0 and 10")

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route.value,
            "representative_tool": self.representative_tool,
            "determinism": self.determinism,
            "risk_class": self.risk.value,
            "recoverability": self.recoverability.value,
            "capability_confidence": self.capability_confidence,
            "cost_tier": self.cost_tier,
            "latency_tier": self.latency_tier,
            "fallback_level": self.fallback_level,
            "capability_checks": list(self.capability_checks),
            "supported": self.supported,
            "reason": self.reason,
            "rejection_code": self.rejection_code,
            "preserves_macros": self.preserves_macros,
        }


@dataclass(frozen=True, slots=True)
class RouteDecision:
    intent: str
    selected_route: RouteClass | None
    representative_tool: str | None
    candidate_count: int
    selection_reason: str
    fallback_level: int | None
    capability_checks: tuple[str, ...]
    risk: RiskClass | None
    router_policy_version: str

    def __post_init__(self) -> None:
        if self.candidate_count < 0:
            raise ValueError("candidate_count must not be negative")
        if self.fallback_level is not None and not 0 <= self.fallback_level <= 10:
            raise ValueError("fallback_level must be between 0 and 10")
        if not self.router_policy_version or len(self.router_policy_version) > 100:
            raise ValueError("router_policy_version must be between 1 and 100 characters")

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "selected_route": self.selected_route.value if self.selected_route else None,
            "representative_tool": self.representative_tool,
            "candidate_count": self.candidate_count,
            "selection_reason": self.selection_reason,
            "fallback_level": self.fallback_level,
            "capability_checks": list(self.capability_checks),
            "risk_class": self.risk.value if self.risk else None,
            "router_policy_version": self.router_policy_version,
        }
