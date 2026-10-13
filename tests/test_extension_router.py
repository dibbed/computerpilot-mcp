from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from types import MappingProxyType
from typing import Any

import pytest
from test_execution_router import _windows_caps
from test_extension_bridge import bridge

from core.execution_models import ExternalRouteMetadata, RecoverabilityClass, RiskClass, RouteCandidate, RouteClass, RouteDecision
from core.extensions.models import ProviderToolHints
from core.tool_catalog import external_tool_execution_metadata


def routing_fixture(intent: str = "filesystem.read") -> tuple[Any, Any, Any]:
    server, manager, session = bridge()
    descriptor = manager.list_tools()[0]
    owner = manager.get_owner(descriptor.extension_id)
    assert owner is not None
    policy = descriptor.policy.model_copy(update={"intents": (intent,), "determinism": 4})
    owner.definition = owner.definition.model_copy(update={"tools": {descriptor.remote_name: policy}})
    generation = manager.registry.next_catalog_generation(descriptor.extension_id)
    manager.registry.publish_extension_catalog(
        descriptor.extension_id, generation, (replace(descriptor, policy=policy, catalog_generation=generation),),
    )
    return server, manager, session


def routed(manager: Any, *, registered: set[str] | None = None, profile: str = "full") -> Any:
    from core.execution_router import ExecutionRouter

    return ExecutionRouter(
        registered_tools=registered if registered is not None else {tool.public_name for tool in manager.list_tools()},
        capabilities=_windows_caps(), extension_manager=manager, active_profile=profile,
    )


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


@pytest.mark.parametrize("gate", [
    "absent", "disabled", "unhealthy", "approval", "trust", "profile", "all", "mutation", "policy", "platform", "combined",
])
def test_external_candidate_uses_the_shared_live_gate_before_scoring(gate: str) -> None:
    from core.execution_models import ExecutionIntent
    from core.extensions.models import ExtensionState, MutationClass, TrustClass
    from core.platform import normalize_system

    _, manager, session = routing_fixture()
    descriptor = manager.list_tools()[0]
    owner = manager.get_owner(descriptor.extension_id)
    assert owner is not None
    profile = "full"
    if gate == "absent":
        manager.registry.retire_extension_catalog(descriptor.extension_id)
    elif gate in {"disabled", "combined"}:
        owner.is_enabled = False
        if gate == "combined":
            owner.state_machine.transition(ExtensionState.DEGRADED)
            owner.definition = owner.definition.model_copy(update={"launch_approved": False})
    elif gate == "unhealthy":
        owner.state_machine.transition(ExtensionState.DEGRADED)
    elif gate == "approval":
        owner.definition = owner.definition.model_copy(update={"launch_approved": False})
    elif gate == "trust":
        owner.definition = owner.definition.model_copy(update={"trust_class": TrustClass.UNTRUSTED})
    elif gate in {"profile", "all"}:
        profile = "minimal" if gate == "profile" else "all"
    elif gate in {"mutation", "policy"}:
        generation = manager.registry.next_catalog_generation(descriptor.extension_id)
        manager.registry.publish_extension_catalog(descriptor.extension_id, generation, (replace(
            descriptor, policy=descriptor.policy.model_copy(update=(
                {"mutation_class": MutationClass.UNKNOWN} if gate == "mutation" else {"allow_call": False}
            )),
            catalog_generation=generation,
        ),))
    elif gate == "platform":
        owner.definition = owner.definition.model_copy(update={"platforms": ("linux" if normalize_system() != "linux" else "windows",)})
    result = routed(manager, profile=profile).candidates(ExecutionIntent("filesystem.read"))
    candidate = next(item for item in result["items"] if item["route"] == "external.mcp")
    assert not candidate["supported"] and candidate["rejection_code"]
    assert not result["valid_candidate_count"]
    session.send_request.assert_not_awaited()


def test_catalog_change_during_projection_cannot_select_a_stale_route(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.execution_models import ExecutionIntent

    _, manager, session = routing_fixture()
    original = manager.tool_eligibility

    def retire(name: str, profile: str = "full") -> Any:
        eligibility = original(name, profile)
        manager.registry.retire_extension_catalog("security-fixture")
        return eligibility

    monkeypatch.setattr(manager, "tool_eligibility", retire)
    result = routed(manager).candidates(ExecutionIntent("filesystem.read"))
    rejected = next(item for item in result["items"] if item["route"] == "external.mcp")
    assert not rejected["supported"] and rejected["rejection_code"] == "stale_generation"
    assert not result["valid_candidate_count"]
    session.send_request.assert_not_awaited()


def test_unknown_intent_requires_exact_operator_mapping_and_a_live_catalog() -> None:
    from core.errors import ToolError
    from core.execution_models import ExecutionIntent

    _, manager, session = routing_fixture("operator.read")
    result = routed(manager).recommend(ExecutionIntent("operator.read"))
    assert result["ok"] and result["selected_route"] == "external.mcp"
    assert result["classification_source"] == "operator_policy"
    with pytest.raises(ToolError, match="Unknown execution intent"):
        routed(manager).recommend(ExecutionIntent("operator.read.unapproved"))
    descriptor = manager.list_tools()[0]
    manager.registry.retire_extension_catalog(descriptor.extension_id)
    result = routed(manager).recommend(ExecutionIntent("operator.read"))
    assert not result["ok"]
    assert result["candidates"][0]["catalog_generation"] == 0
    session.send_request.assert_not_awaited()


def test_external_route_auth_and_schema_rejections_do_not_disclose_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from core.execution_models import ExecutionIntent
    from core.extensions.models import ExtensionEnvironment

    _, manager, session = routing_fixture()
    descriptor = manager.list_tools()[0]
    owner = manager.get_owner(descriptor.extension_id)
    assert owner is not None
    owner.definition = owner.definition.model_copy(update={"environment": ExtensionEnvironment.model_validate({
        "references": {"token": {"name": "ROUTER_OPERATOR_TOKEN"}},
    })})
    policy = descriptor.policy.model_copy(update={"required_env_refs": ("token",)})
    generation = manager.registry.next_catalog_generation(descriptor.extension_id)
    manager.registry.publish_extension_catalog(descriptor.extension_id, generation, (replace(
        descriptor, policy=policy, catalog_generation=generation,
    ),))
    monkeypatch.delenv("ROUTER_OPERATOR_TOKEN", raising=False)
    result = routed(manager).candidates(ExecutionIntent("filesystem.read"))
    rejected = next(item for item in result["items"] if item["route"] == "external.mcp")
    assert rejected["rejection_code"] == "missing_auth_refs"
    assert "ROUTER_OPERATOR_TOKEN" not in json.dumps(result)

    monkeypatch.setenv("ROUTER_OPERATOR_TOKEN", "security-fixture")
    result = routed(manager).candidates(ExecutionIntent("filesystem.read"))
    assert "security-fixture" not in json.dumps(result)
    assert not result["valid_candidate_count"]
    session.send_request.assert_not_awaited()


def test_changed_schema_is_a_bounded_rejected_candidate() -> None:
    from core.execution_models import ExecutionIntent
    from core.extensions.models import ExtensionToolDescriptor

    _, manager, session = routing_fixture()
    descriptor = manager.list_tools()[0]
    generation = manager.registry.next_catalog_generation(descriptor.extension_id)
    changed = ExtensionToolDescriptor.create(
        extension_id=descriptor.extension_id, provider=descriptor.identity.provider, remote_name=descriptor.remote_name,
        description=descriptor.description, input_schema={"type": "object", "properties": {"changed": {"type": "integer"}}},
        output_schema=descriptor.output_schema, policy=descriptor.policy, catalog_generation=generation,
    )
    manager.registry.publish_extension_catalog(descriptor.extension_id, generation, (changed,))
    result = routed(manager).candidates(ExecutionIntent("filesystem.read"))
    rejected = next(item for item in result["items"] if item["route"] == "external.mcp")
    assert not rejected["supported"] and rejected["rejection_code"] == "schema_withheld"
    assert not result["valid_candidate_count"]
    session.send_request.assert_not_awaited()


def test_removed_operator_intent_mapping_is_not_reused_from_old_configuration() -> None:
    from core.execution_models import ExecutionIntent

    _, manager, _ = routing_fixture()
    descriptor = manager.list_tools()[0]
    generation = manager.registry.next_catalog_generation(descriptor.extension_id)
    manager.registry.publish_extension_catalog(descriptor.extension_id, generation, (replace(
        descriptor, policy=descriptor.policy.model_copy(update={"intents": ()}), catalog_generation=generation,
    ),))
    result = routed(manager).candidates(ExecutionIntent("filesystem.read"))
    assert not any(item["route"] == "external.mcp" for item in result["items"])


def test_actual_registry_router_uses_manager_and_preserves_legacy_exposure_limit() -> None:
    import anyio
    from mcp import types
    from mcp.server.context import ServerRequestContext

    from core.extensions.bridge import ExtensionMCPServer
    from core.extensions.models import LIMITS
    from core.registry import create_server

    _, manager, session = routing_fixture("operator.read")
    server = create_server()
    assert isinstance(server, ExtensionMCPServer)
    server.extension_manager = manager

    async def scenario() -> None:
        result = await server.call_tool("execution_recommend", {"intent": "operator.read"})
        assert isinstance(result, types.CallToolResult)
        assert result.structured_content is not None and result.structured_content["selected_route"] == "external.mcp"

        async def native_route(ctx: Any) -> Any:
            return await server.call_tool("execution_candidates", {"intent": "filesystem.read"})

        from types import SimpleNamespace

        for _ in range(LIMITS.notification_pending + 1):
            legacy_session: Any = SimpleNamespace(client_params=object())
            ctx: ServerRequestContext[Any, Any] = ServerRequestContext(
                protocol_version="2025-11-25", session=legacy_session, lifespan_context={}, method="tools/call",
            )
            observed = await server.observe_client(ctx, native_route)
        assert isinstance(observed, types.CallToolResult) and observed.structured_content is not None
        assert not any(item["route"] == "external.mcp" for item in observed.structured_content["items"])
        session.send_request.assert_not_awaited()

    anyio.run(scenario)


def test_recommendation_and_explanations_use_one_live_candidate_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    from core.execution_models import ExecutionIntent

    _, manager, _ = routing_fixture("operator.read")
    spy = MagicMock(wraps=manager.routing_candidates)
    monkeypatch.setattr(manager, "routing_candidates", spy)
    result = routed(manager).recommend(ExecutionIntent("operator.read"))
    assert result["ok"]
    assert spy.call_count == 1
    assert result["representative_tool"] == result["candidates"][0]["representative_tool"]


@pytest.mark.parametrize("intent,native_tool", [
    ("filesystem.read", "ui_invoke"), ("browser.interact", "browser_click_semantic"),
    ("desktop.raw.interact", "mouse_click"),
])
def test_external_ranking_uses_reviewed_policy_estimates_without_blanket_native_priority(intent: str, native_tool: str) -> None:
    from core.execution_models import ExecutionIntent

    _, manager, _ = routing_fixture(intent)
    descriptor = manager.list_tools()[0]
    strong = descriptor.policy.model_copy(update={"recoverability": RecoverabilityClass.STRONG, "risk_class": RiskClass.LOW})
    generation = manager.registry.next_catalog_generation(descriptor.extension_id)
    manager.registry.publish_extension_catalog(descriptor.extension_id, generation, (replace(
        descriptor, policy=strong, catalog_generation=generation,
    ),))
    request = ExecutionIntent(intent, allow_raw_desktop=True, preferred_tool=native_tool)
    result = routed(manager, registered={native_tool, descriptor.public_name}).recommend(request)
    assert result["selected_route"] == "external.mcp"
    assert "operator policy estimates" in result["selection_reason"]
    assert result["router_policy_version"] == "deterministic-v1"


def test_native_five_stays_ahead_of_external_four_and_macro_guard_stays_hard() -> None:
    from core.execution_models import ExecutionIntent

    _, manager, _ = routing_fixture("filesystem.read")
    descriptor = manager.list_tools()[0]
    result = routed(manager, registered={"read_file", descriptor.public_name}).recommend(ExecutionIntent(
        "filesystem.read", preferred_tool=descriptor.public_name,
    ))
    assert result["selected_route"] == "native.filesystem"
    assert "classification_source" not in result
    rejected = routed(manager).candidates(ExecutionIntent("filesystem.read", requires_macro_preservation=True))
    external = next(item for item in rejected["items"] if item["route"] == "external.mcp")
    assert not external["supported"] and external["rejection_code"] == "macro_preservation_unsupported"


def test_external_ties_are_stable_and_reasons_never_copy_raw_provider_descriptions() -> None:
    import json

    from core.execution_models import ExecutionIntent
    from core.extensions.models import ExtensionToolDescriptor

    _, manager, session = routing_fixture("operator.read")
    original = manager.list_tools()[0]
    owner = manager.get_owner(original.extension_id)
    assert owner is not None
    second_policy = original.policy.model_copy(update={"remote_name": "z_read"})
    owner.definition = owner.definition.model_copy(update={"tools": {"z_read": second_policy, original.remote_name: original.policy}})
    generation = manager.registry.next_catalog_generation(original.extension_id)
    second = ExtensionToolDescriptor.create(
        extension_id=original.extension_id, provider=original.identity.provider, remote_name="z_read",
        description="RAW_UNTRUSTED_PROVIDER_INSTRUCTION", input_schema=original.input_schema,
        output_schema=original.output_schema, policy=second_policy, catalog_generation=generation,
    )
    manager.registry.publish_extension_catalog(original.extension_id, generation, (
        second, replace(original, description="RAW_UNTRUSTED_PROVIDER_INSTRUCTION", catalog_generation=generation),
    ))
    router = routed(manager)
    request = ExecutionIntent("operator.read", preferred_tool=second.public_name)
    results = [router.recommend(request, max_candidates=1) for _ in range(5)]
    assert all(result == results[0] for result in results)
    assert results[0]["representative_tool"] == original.public_name
    assert results[0]["truncated"]
    assert "RAW_UNTRUSTED_PROVIDER_INSTRUCTION" not in json.dumps(results)
    session.send_request.assert_not_awaited()
