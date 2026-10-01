"""Bounded in-memory observability for execution routing."""

from __future__ import annotations

import threading
from collections import Counter, deque
from typing import Any


class RouterMetrics:
    def __init__(self, *, max_recent: int = 50) -> None:
        if not 1 <= max_recent <= 500:
            raise ValueError("max_recent must be between 1 and 500")
        self._lock = threading.Lock()
        self._max_recent = max_recent
        self._decision_count = 0
        self._no_route_count = 0
        self._fallback_count = 0
        self._route_distribution: Counter[str] = Counter()
        self._usage: Counter[str] = Counter()
        self._latency_total_ms = 0.0
        self._latency_max_ms = 0.0
        self._recent: deque[dict[str, Any]] = deque(maxlen=max_recent)

    @staticmethod
    def _family(route: str | None) -> str | None:
        if route is None:
            return None
        if route.startswith("native."):
            return "native"
        if route.startswith("semantic."):
            return "semantic"
        if route == "raw.desktop":
            return "raw"
        if route == "visual.desktop":
            return "visual"
        return "other"

    def record(
        self,
        *,
        intent: str,
        selected_route: str | None,
        fallback_level: int | None,
        policy_version: str,
        candidate_count: int,
        rejected_candidate_count: int,
        elapsed_ms: float,
    ) -> None:
        bounded_elapsed = max(float(elapsed_ms), 0.0)
        with self._lock:
            self._decision_count += 1
            if selected_route is None:
                self._no_route_count += 1
            else:
                self._route_distribution[selected_route] += 1
                family = self._family(selected_route)
                if family is not None:
                    self._usage[family] += 1
            if fallback_level is not None and fallback_level > 0:
                self._fallback_count += 1
            self._latency_total_ms += bounded_elapsed
            self._latency_max_ms = max(self._latency_max_ms, bounded_elapsed)
            self._recent.append(
                {
                    "intent": intent[:200],
                    "selected_route": selected_route,
                    "fallback_level": fallback_level,
                    "router_policy_version": policy_version[:100],
                    "candidate_count": max(int(candidate_count), 0),
                    "rejected_candidate_count": max(int(rejected_candidate_count), 0),
                }
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            count = self._decision_count
            return {
                "decision_count": count,
                "no_route_count": self._no_route_count,
                "fallback_count": self._fallback_count,
                "fallback_rate": (self._fallback_count / count) if count else 0.0,
                "route_distribution": dict(sorted(self._route_distribution.items())),
                "usage": {
                    "native": self._usage.get("native", 0),
                    "semantic": self._usage.get("semantic", 0),
                    "raw": self._usage.get("raw", 0),
                    "visual": self._usage.get("visual", 0),
                    "other": self._usage.get("other", 0),
                },
                "latency_ms": {
                    "average": (self._latency_total_ms / count) if count else 0.0,
                    "max": self._latency_max_ms,
                },
                "recent_decisions": list(self._recent),
                "recent_limit": self._max_recent,
            }


ROUTER_METRICS = RouterMetrics()
