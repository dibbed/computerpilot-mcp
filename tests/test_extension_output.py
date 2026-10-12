from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import types
from test_extension_security import admitted_call

from core import artifacts
from core.artifact_retention import flush_artifact_retention
from core.errors import ToolError
from core.extensions.models import LIMITS
from core.extensions.output import normalize_result
from core.extensions.security import CredentialRedactor


def test_large_text_returns_bounded_explicit_preview_without_default_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "SETTINGS", replace(artifacts.SETTINGS, state_dir=tmp_path))
    raw = types.CallToolResult(content=[types.TextContent(type="text", text="پاسخ" * 30_000)])
    manager, lease, _ = admitted_call({"type": "object"}, raw)

    async def scenario() -> None:
        result = await manager.execute_guarded_call(lease)
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
        assert result["ok"] and result["truncated"]
        assert result["artifact"] is None and result["structured_content"] is None
        assert len(encoded) <= LIMITS.inline_bytes
        assert result["limits"]["returned_bytes"] == len(encoded)
        assert result["limits"]["original_bytes_if_known"] > len(encoded)
        assert not (tmp_path / "artifacts").exists()

    anyio.run(scenario)


def test_opted_in_artifact_contains_only_sanitized_bounded_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "SETTINGS", replace(artifacts.SETTINGS, state_dir=tmp_path))
    canary = "artifact-private-credential-471b"
    raw = types.CallToolResult(content=[types.TextContent(type="text", text=("fixture " + canary) * 8000)])
    manager, lease, _ = admitted_call({"type": "object"}, raw)
    descriptor = manager.registry.get_tool(lease.public_name)
    assert descriptor is not None
    descriptor = replace(descriptor, policy=descriptor.policy.model_copy(update={"allow_output_artifact": True}))
    result = normalize_result(descriptor, raw, CredentialRedactor([canary]))
    assert result["truncated"] and result["artifact"]
    artifact = result["artifact"]
    payload = Path(artifact["path"]).read_bytes()
    assert canary.encode() not in payload
    assert artifact["sha256"] == hashlib.sha256(payload).hexdigest()
    assert artifact["bytes"] == len(payload) <= LIMITS.artifact_bytes
    assert artifact["media_type"] == "application/json"
    assert flush_artifact_retention()
    assert Path(artifact["path"]).exists()


@pytest.mark.parametrize("arguments", [{"value": "x" * (LIMITS.argument_bytes + 1)}, {"value": float("nan")}, {"value": [0] * 2049}])
def test_extreme_arguments_never_reach_provider(arguments: dict[str, Any]) -> None:
    manager, lease, session = admitted_call({"type": "object"}, types.CallToolResult(content=[]))

    async def scenario() -> None:
        with pytest.raises(ToolError) as rejected:
            await manager.execute_guarded_call(lease, arguments)
        assert rejected.value.code == "invalid_arguments"
        session.send_request.assert_not_awaited()

    anyio.run(scenario)


def test_provider_error_discards_remote_payload_in_core_envelope() -> None:
    raw = types.CallToolResult(content=[types.TextContent(type="text", text="private-provider-payload")], is_error=True)
    manager, lease, _ = admitted_call({"type": "object"}, raw)

    async def scenario() -> None:
        result = await manager.execute_guarded_call(lease)
        assert not result["ok"] and result["outcome"] == "failed"
        assert result["error"] == "provider_error"
        assert "private-provider-payload" not in json.dumps(result)

    anyio.run(scenario)


def test_queued_call_keeps_the_validated_nested_argument_snapshot() -> None:
    manager, lease, session = admitted_call({"type": "object"}, types.CallToolResult(content=[]))
    arguments = {"nested": {"value": "before-queue"}}

    async def scenario() -> None:
        for _ in range(manager._global_call_semaphore.value):
            await manager._global_call_semaphore.acquire()
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(manager.execute_guarded_call, lease, arguments)
            with anyio.fail_after(2):
                while manager._global_call_semaphore.statistics().tasks_waiting == 0:
                    await anyio.sleep(0)
            arguments["nested"]["value"] = "after-queue"
            manager._global_call_semaphore.release()
        assert session.send_request.await_args.args[0].params.arguments == {"nested": {"value": "before-queue"}}

    anyio.run(scenario)


@pytest.mark.parametrize("raw", [
    types.CallToolResult(content=[types.ImageContent(type="image", mime_type="image/png", data="!not-base64!")]),
    types.CallToolResult(content=[types.TextContent(type="text", text="x" * (LIMITS.frame_bytes + 1))]),
])
def test_malformed_or_oversize_output_is_safe_failure(raw: types.CallToolResult) -> None:
    manager, lease, _ = admitted_call({"type": "object"}, raw)

    async def scenario() -> None:
        with pytest.raises(ToolError) as failed:
            await manager.execute_guarded_call(lease)
        assert failed.value.code in {"malformed_result", "output_too_large"}

    anyio.run(scenario)


def test_unserializable_provider_values_do_not_expose_serializer_exceptions() -> None:
    private_type = type("private_provider_value_credential", (), {})
    raw = types.CallToolResult(content=[], structured_content={"value": private_type()})
    manager, lease, _ = admitted_call({"type": "object"}, raw)

    async def scenario() -> None:
        with pytest.raises(ToolError) as failed:
            await manager.execute_guarded_call(lease)
        assert failed.value.code == "malformed_result"
        assert "private_provider_value_credential" not in str(failed.value)

    anyio.run(scenario)
