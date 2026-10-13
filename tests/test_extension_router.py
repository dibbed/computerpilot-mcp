from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from types import MappingProxyType
from typing import Any

import pytest
from test_extension_bridge import bridge

from core.execution_models import ExternalRouteMetadata, RecoverabilityClass, RiskClass, RouteCandidate, RouteClass, RouteDecision
from core.extensions.models import ProviderToolHints
from core.tool_catalog import external_tool_execution_metadata


def test_external_route_metadata_is_immutable_and_native_serialization_stays_separate() -> None:
    metadata = ExternalRouteMetadata("fixture", "provider", 7)
    assert metadata.as_dict() == {
        "source_type": "external_mcp", "extension_id": "fixture", "provider": "provider", "catalog_generation": 7,
        "classification_source": "operator_policy",
    }
    mutable_metadata: Any = metadata
    with pytest.raises(FrozenInstanceError):
        mutable_metadata.provider = "another"
    candidate = RouteCandidate(
        route=RouteClass.EXTERNAL_MCP, representative_tool="external.fixture.read", determinism=4,
        risk=RiskClass.HIGH, recoverability=RecoverabilityClass.WEAK, capability_confidence=1,
        cost_tier=5, latency_tier=5, fallback_level=1, external_metadata=metadata,
    )
    assert candidate.as_dict()["catalog_generation"] == 7
    decision = RouteDecision(
        intent="fixture.read", selected_route=RouteClass.EXTERNAL_MCP, representative_tool=candidate.representative_tool,
        candidate_count=1, selection_reason="operator policy estimate", fallback_level=1, capability_checks=(),
        risk=RiskClass.HIGH, router_policy_version="deterministic-v1", external_metadata=metadata,
    )
    assert decision.as_dict()["classification_source"] == "operator_policy"
    with pytest.raises(ValueError):
        replace(candidate, route=RouteClass.NATIVE_FILESYSTEM)
    with pytest.raises(ValueError):
        replace(candidate, determinism=5)
    missing = replace(metadata, catalog_generation=0)
    with pytest.raises(ValueError):
        replace(candidate, external_metadata=missing)
    assert not replace(candidate, external_metadata=missing, supported=False).supported


@pytest.mark.parametrize("extension_id,provider,generation", [
    ("", "provider", 1), ("x" * 33, "provider", 1), ("fixture", "x" * 65, 1), ("fixture", "provider", -1),
])
def test_external_route_metadata_bounds(extension_id: str, provider: str, generation: int) -> None:
    with pytest.raises(ValueError):
        ExternalRouteMetadata(extension_id, provider, generation)


def test_external_execution_metadata_uses_operator_policy_and_not_provider_claims() -> None:
    _, manager, _ = bridge()
    original = manager.list_tools()[0]
    descriptor = replace(original, policy=original.policy.model_copy(update={
        "risk_class": RiskClass.HIGH, "determinism": 2, "recoverability": RecoverabilityClass.WEAK,
    }), provider_hints=ProviderToolHints(
        read_only=True, idempotent=True, destructive=False,
        raw_hints=MappingProxyType({"risk": "low", "determinism": 5, "route": "native.filesystem", "trust": "built_in"}),
    ))
    metadata = external_tool_execution_metadata(
        descriptor.public_name, descriptor.policy,
        ExternalRouteMetadata(descriptor.extension_id, descriptor.identity.provider, descriptor.catalog_generation),
    )
    assert metadata.route is RouteClass.EXTERNAL_MCP
    assert metadata.risk is RiskClass.HIGH and metadata.determinism == 2
    assert metadata.recoverability is RecoverabilityClass.WEAK
    assert metadata.external_metadata is not None
    assert metadata.external_metadata.catalog_generation == descriptor.catalog_generation
    assert not metadata.preserves_macros
