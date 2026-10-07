"""Pure deterministic execution-route candidate generation and selection."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from core.errors import ToolError
from core.execution_models import ExecutionIntent, RecoverabilityClass, RiskClass, RouteCandidate, RouteClass, RouteDecision
from core.platform import PlatformCapabilities
from core.router_metrics import ROUTER_METRICS, RouterMetrics
from core.tool_catalog import IntentRouteRule, intent_route_rules

ROUTER_POLICY_VERSION = "deterministic-v1"
MAX_CANDIDATES = 25

_RECOVERABILITY_RANK = {
    RecoverabilityClass.WEAK: 1,
    RecoverabilityClass.MEDIUM: 2,
    RecoverabilityClass.STRONG: 3,
}
_RISK_RANK = {
    RiskClass.LOW: 1,
    RiskClass.MEDIUM: 2,
    RiskClass.HIGH: 3,
}


class ExecutionRouter:
    def __init__(
        self,
        *,
        registered_tools: set[str] | frozenset[str],
        capabilities: PlatformCapabilities,
        policy_version: str = ROUTER_POLICY_VERSION,
        enabled: bool = True,
        metrics: RouterMetrics | None = None,
    ) -> None:
        self.registered_tools = frozenset(registered_tools)
        self.capabilities = capabilities
        self.policy_version = policy_version.strip().casefold()
        self.enabled = enabled
        self.metrics = metrics or ROUTER_METRICS

    def _validate_policy(self) -> None:
        if not self.enabled:
            raise ToolError(
                "execution_router_disabled",
                "The adaptive execution router is disabled by configuration.",
            )
        if self.policy_version != ROUTER_POLICY_VERSION:
            raise ToolError(
                "unsupported_router_policy",
                f"Unsupported execution router policy {self.policy_version!r}.",
                hint=f"Use {ROUTER_POLICY_VERSION!r}.",
            )

    @staticmethod
    def _hard_rejection(intent: ExecutionIntent, candidate: RouteCandidate) -> tuple[str, str] | None:
        route = candidate.route
        if route is RouteClass.VISUAL_DESKTOP:
            return "visual_route_unavailable", "visual desktop routing is reserved for v0.8.0"
        if intent.semantic_stale and route in {
            RouteClass.SEMANTIC_BROWSER,
            RouteClass.SEMANTIC_WINDOWS_UIA,
            RouteClass.RAW_DESKTOP,
        }:
            return "stale_semantic_reference", "stale semantic references cannot fall back to desktop coordinates"
        if intent.destructive and intent.semantic_ambiguous and route in {
            RouteClass.SEMANTIC_BROWSER,
            RouteClass.SEMANTIC_WINDOWS_UIA,
        }:
            return "destructive_semantic_ambiguous", "ambiguous semantic destructive actions are not auto-selected"
        if route is RouteClass.RAW_DESKTOP:
            if intent.destructive:
                return "destructive_raw_desktop_forbidden", "destructive actions cannot auto-select raw desktop input"
            if not intent.allow_raw_desktop:
                return "raw_desktop_not_allowed", "raw desktop routing requires explicit permission"
        if intent.requires_macro_preservation and not candidate.preserves_macros:
            return "macro_preservation_unsupported", "route cannot prove macro preservation"
        return None

    def _candidate(self, rule: IntentRouteRule, intent: ExecutionIntent) -> RouteCandidate:
        checks: list[str] = []
        supported = True
        rejection_code: str | None = None
        reason = "registered route satisfies current capability snapshot"

        if rule.representative_tool not in self.registered_tools:
            supported = False
            rejection_code = "tool_unavailable"
            reason = f"required tool {rule.representative_tool!r} is not registered"
            checks.append(f"tool:{rule.representative_tool}=unavailable")
        else:
            checks.append(f"tool:{rule.representative_tool}=available")

        for capability_name in rule.required_capabilities:
            available = bool(getattr(self.capabilities, capability_name, False))
            checks.append(f"capability:{capability_name}={'available' if available else 'unavailable'}")
            if not available and supported:
                supported = False
                rejection_code = "capability_unavailable"
                reason = f"required capability {capability_name!r} is unavailable"

        candidate = RouteCandidate(
            route=rule.route,
            representative_tool=rule.representative_tool,
            determinism=rule.determinism,
            risk=rule.risk,
            recoverability=rule.recoverability,
            capability_confidence=rule.capability_confidence,
            cost_tier=rule.cost_tier,
            latency_tier=rule.latency_tier,
            fallback_level=rule.fallback_level,
            capability_checks=tuple(checks),
            supported=supported,
            reason=reason,
            rejection_code=rejection_code,
            preserves_macros=rule.preserves_macros,
        )
        if candidate.supported:
            hard_rejection = self._hard_rejection(intent, candidate)
            if hard_rejection is not None:
                rejection_code, reason = hard_rejection
                candidate = replace(
                    candidate,
                    supported=False,
                    rejection_code=rejection_code,
                    reason=reason,
                )
        return candidate

    @staticmethod
    def _sort_key(candidate: RouteCandidate) -> tuple[int, int, int, int, int, int, str, str]:
        return (
            -candidate.determinism,
            -_RECOVERABILITY_RANK[candidate.recoverability],
            _RISK_RANK[candidate.risk],
            -candidate.capability_confidence,
            candidate.cost_tier,
            candidate.latency_tier,
            candidate.route.value,
            candidate.representative_tool,
        )

    def _all_candidates(self, intent: ExecutionIntent) -> list[RouteCandidate]:
        items = [self._candidate(rule, intent) for rule in intent_route_rules(intent.name)]
        valid = sorted((item for item in items if item.supported), key=self._sort_key)
        rejected = sorted(
            (item for item in items if not item.supported),
            key=lambda item: (item.fallback_level, item.route.value, item.representative_tool),
        )
        return [*valid, *rejected]

    def candidates(
        self,
        intent: ExecutionIntent,
        *,
        max_candidates: int = 8,
    ) -> dict[str, Any]:
        self._validate_policy()
        bounded_limit = min(max(int(max_candidates), 1), MAX_CANDIDATES)
        all_candidates = self._all_candidates(intent)
        returned = all_candidates[:bounded_limit]
        valid_count = sum(item.supported for item in all_candidates)
        return {
            "ok": True,
            "intent": intent.name,
            "router_policy_version": self.policy_version,
            "items": [item.as_dict() for item in returned],
            "count": len(returned),
            "total_candidates": len(all_candidates),
            "valid_candidate_count": valid_count,
            "rejected_candidate_count": len(all_candidates) - valid_count,
            "truncated": len(returned) < len(all_candidates),
        }

    def recommend(
        self,
        intent: ExecutionIntent,
        *,
        explain: bool = True,
        max_candidates: int = 8,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        candidate_result = self.candidates(intent, max_candidates=max_candidates)
        all_candidates = self._all_candidates(intent)
        valid = [candidate for candidate in all_candidates if candidate.supported]
        selected = valid[0] if valid else None

        decision = RouteDecision(
            intent=intent.name,
            selected_route=selected.route if selected else None,
            representative_tool=selected.representative_tool if selected else None,
            candidate_count=len(valid),
            selection_reason=(
                "highest deterministic valid route under deterministic-v1"
                if selected is not None
                else "no valid route under current tool and capability snapshot"
            ),
            fallback_level=selected.fallback_level if selected else None,
            capability_checks=selected.capability_checks if selected else (),
            risk=selected.risk if selected else None,
            router_policy_version=self.policy_version,
        )
        result = {"ok": selected is not None, **decision.as_dict()}
        if explain:
            result["candidates"] = candidate_result["items"]
            result["rejected_candidate_count"] = candidate_result["rejected_candidate_count"]
            result["truncated"] = candidate_result["truncated"]

        self.metrics.record(
            intent=intent.name,
            selected_route=selected.route.value if selected else None,
            fallback_level=selected.fallback_level if selected else None,
            policy_version=self.policy_version,
            candidate_count=len(valid),
            rejected_candidate_count=candidate_result["rejected_candidate_count"],
            elapsed_ms=(time.perf_counter() - started) * 1_000.0,
        )
        return result
