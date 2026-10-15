from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer

from core import audit, lifecycle, recovery, tooling
from core.errors import ToolError
from core.recovery import OperationRecoveryJournal
from tools.desktop import registry
from tools.desktop.uia import ElementLocator, UIAutomationService, WindowLocator


@dataclass
class Node:
    name: str
    automation_id: str = ""
    control_type: str = "ButtonControl"
    class_name: str = ""
    pid: int = 1
    handle: int = 0
    children: list[Node] = field(default_factory=list)


class FakeBackend:
    def __init__(self) -> None:
        self.saved: list[tuple[str, str]] = []
        self.window = Node(
            "Editor",
            control_type="WindowControl",
            handle=10,
            children=[Node("Save", "save"), Node("Name", "name", "EditControl")],
        )

    def windows(self) -> list[Node]:
        return [self.window]

    def children(self, element: Node) -> list[Node]:
        return element.children

    def properties(self, element: Node) -> dict[str, Any]:
        return vars(element)

    def runtime_id(self, element: Node) -> list[int]:
        return [42, self.window.children.index(element) + 1]

    def invoke(self, element: Node) -> None:
        self.saved.append(("invoke", element.automation_id))

    def set_value(self, element: Node, value: str) -> None:
        self.saved.append(("value", value))

    def select(self, element: Node) -> None:
        self.saved.append(("select", element.automation_id))


def test_semantic_lookup_and_mutations_redact_values() -> None:
    backend = FakeBackend()
    service = UIAutomationService(backend)
    window = WindowLocator(title="Editor")

    found = service.find_elements(window, ElementLocator(control_type="ButtonControl"))
    invoked = service.invoke(window, ElementLocator(automation_id="save"))
    valued = service.set_value(window, ElementLocator(automation_id="name"), "secret")

    assert found["total_count"] == 1
    assert invoked["element"]["name"] == "Save"
    assert valued["value_chars"] == 6 and "secret" not in str(valued)
    assert backend.saved == [("invoke", "save"), ("value", "secret")]


def test_semantic_patterns_use_control_identity_without_coordinate_fallback() -> None:
    backend = FakeBackend()
    service = UIAutomationService(backend)
    window = WindowLocator(handle=10)

    service.invoke(window, ElementLocator(automation_id="save", control_type="ButtonControl"))
    service.set_value(window, ElementLocator(automation_id="name", control_type="EditControl"), "value")
    service.select(window, ElementLocator(automation_id="save", control_type="ButtonControl"))

    assert backend.saved == [("invoke", "save"), ("value", "value"), ("select", "save")]
    assert not hasattr(backend, "click")


def test_mutation_re_resolves_and_duplicate_match_fails_closed() -> None:
    backend = FakeBackend()
    service = UIAutomationService(backend)
    window = WindowLocator(handle=10)
    backend.window.children.append(Node("Save", "save-2"))

    with pytest.raises(ToolError, match="matched 2"):
        service.invoke(window, ElementLocator(name="Save"))


def test_bounded_traversal_and_timeout() -> None:
    backend = FakeBackend()
    service = UIAutomationService(backend)
    window = WindowLocator(title_contains="dit")
    with pytest.raises(ToolError, match="exceeded"):
        service.find_elements(window, ElementLocator(name="missing"), max_nodes=1)
    with pytest.raises(ToolError, match="did not appear"):
        service.wait_for_element(window, ElementLocator(name="missing"), timeout_sec=0, interval_sec=0)


@pytest.fixture
def native_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> OperationRecoveryJournal:
    journal = OperationRecoveryJournal()
    journal.configure(tmp_path / "recovery.jsonl", "uia-test")
    monkeypatch.setattr(recovery, "OPERATION_RECOVERY", journal)
    monkeypatch.setattr(tooling, "OPERATION_RECOVERY", journal)
    runtime = lifecycle.RuntimeLifecycle()
    monkeypatch.setattr(lifecycle, "RUNTIME_LIFECYCLE", runtime)
    monkeypatch.setattr(tooling, "RUNTIME_LIFECYCLE", runtime)
    monkeypatch.setattr(audit, "SETTINGS", replace(audit.SETTINGS, state_dir=tmp_path))
    return journal


@pytest.mark.parametrize("action", ["invoke", "set_value", "select"])
def test_direct_uia_mutations_capture_private_safe_target_before_effect(
    action: str, native_journal: OperationRecoveryJournal, tmp_path: Path,
) -> None:
    backend = FakeBackend()
    backend.window.pid = os.getpid()
    private = "private-uia-value-and-title-canary"
    backend.window.name = private
    backend.window.children[1].automation_id = private
    service = UIAutomationService(backend)
    element = ElementLocator(automation_id=private)
    args: list[Any] = [WindowLocator(handle=10), element]
    if action == "set_value":
        args.append(private)
    getattr(service, action)(*args)
    operations = native_journal.list_operations()
    assert operations["total_count"] == 1
    operation = operations["items"][0]
    assert operation["state"] == "succeeded"
    assert operation["target"] is not None
    assert operation["native"]["dispatch_stage"] == "may_dispatch"
    target = operation["native"]["target"]
    assert target["pid"] == os.getpid() and target["process_created"] > 0
    assert target["window_handle"] == 10 and target["element_runtime_id"] == [42, 2]
    assert len(backend.saved) == 1
    assert private not in (tmp_path / "recovery.jsonl").read_text(encoding="utf-8")
    assert private not in json.dumps(native_journal.summary())


@pytest.mark.parametrize("call_style", ["service", "wrapped", "wire"])
def test_uia_error_after_effect_remains_uncertain_without_replay(
    call_style: str, native_journal: OperationRecoveryJournal, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeBackend()
    service = UIAutomationService(backend)

    def apply_then_fail(element: Node, value: str) -> None:
        backend.saved.append(("value", value))
        raise ToolError("uia_pattern_unsupported", "Result was lost after the effect.")

    monkeypatch.setattr(backend, "set_value", apply_then_fail)
    if call_style == "service":
        with pytest.raises(ToolError):
            service.set_value(WindowLocator(handle=10), ElementLocator(automation_id="name"), "private")
    elif call_style == "wrapped":
        result = tooling.compact_errors("ui_set_value")(service.set_value)(
            WindowLocator(handle=10), ElementLocator(automation_id="name"), "private",
        )
        assert result["ok"] is False
    else:
        monkeypatch.setattr(registry, "UI_AUTOMATION", service)
        server = MCPServer("uia-recovery-test")
        registry.register(server)

        async def scenario() -> None:
            async with Client(server) as client:
                result = await client.call_tool("ui_set_value", {
                    "window": {"handle": 10}, "element": {"automation_id": "name"}, "value": "private",
                })
                assert result.structured_content and result.structured_content["ok"] is False

        asyncio.run(scenario())
    operations = native_journal.list_operations()
    assert operations["total_count"] == 1
    assert operations["items"][0]["state"] == "uncertain"
    native_journal.compact()
    assert native_journal.summary()["uncertain_count"] == 1
    assert backend.saved == [("value", "private")]


def test_uia_journal_failure_prevents_dispatch(
    native_journal: OperationRecoveryJournal, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeBackend()
    service = UIAutomationService(backend)

    def unavailable(*args: Any, **kwargs: Any) -> None:
        raise ToolError("operation_journal_unavailable", "Storage unavailable.")

    monkeypatch.setattr(native_journal, "_append_locked", unavailable)
    with pytest.raises(ToolError, match="Storage unavailable"):
        service.set_value(WindowLocator(handle=10), ElementLocator(automation_id="name"), "private")
    assert backend.saved == []


@pytest.mark.parametrize("stage", ["prepared", "may_dispatch", "result"])
@pytest.mark.parametrize("call_style", ["service", "wrapped", "wire"])
def test_uia_checkpoint_failure_preserves_effect_boundary(
    stage: str, call_style: str, native_journal: OperationRecoveryJournal, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeBackend()
    original = native_journal._append_unlocked_file

    def append(record: dict[str, Any]) -> None:
        if record.get("native", {}).get("dispatch_stage") == stage or (
            stage == "result" and record["type"] == "result"
        ):
            raise ToolError("operation_journal_unavailable", "Storage unavailable.")
        original(record)

    monkeypatch.setattr(native_journal, "_append_unlocked_file", append)
    service = UIAutomationService(backend)
    error_code, hint = None, None
    if call_style == "service":
        with pytest.raises(ToolError) as error:
            service.set_value(WindowLocator(handle=10), ElementLocator(automation_id="name"), "private")
        error_code, hint = error.value.code, error.value.hint
    elif call_style == "wrapped":
        result = tooling.compact_errors("ui_set_value")(service.set_value)(
            WindowLocator(handle=10), ElementLocator(automation_id="name"), "private",
        )
        assert result["ok"] is False
        error_code, hint = result["error"], result.get("hint")
    else:
        monkeypatch.setattr(registry, "UI_AUTOMATION", service)
        server = MCPServer("uia-result-write-failure")
        registry.register(server)

        async def scenario() -> dict[str, Any]:
            async with Client(server) as client:
                result = await client.call_tool("ui_set_value", {
                    "window": {"handle": 10}, "element": {"automation_id": "name"}, "value": "private",
                })
                assert result.structured_content and result.structured_content["ok"] is False
                return result.structured_content

        result = asyncio.run(scenario())
        error_code, hint = result["error"], result.get("hint")
    assert len(backend.saved) == (1 if stage == "result" else 0)
    operation = native_journal.list_operations()["items"][0]
    assert operation["state"] == ("uncertain" if stage == "result" else "failed")
    if stage == "result":
        assert error_code == "operation_outcome_uncertain"
        assert hint and operation["operation_id"] in hint


def test_direct_uia_drain_denies_effect_even_when_recovery_disabled(
    native_journal: OperationRecoveryJournal, tmp_path: Path,
) -> None:
    backend = FakeBackend()
    native_journal.configure(None, None)
    control, status = tmp_path / "control.json", tmp_path / "status.json"
    lifecycle.RUNTIME_LIFECYCLE.configure(control, status)
    control.write_text(json.dumps(lifecycle.lifecycle_control_request(
        "drain", "uia-drain", time.time() + 10,
    )), encoding="utf-8")
    with pytest.raises(ToolError) as error:
        UIAutomationService(backend).set_value(WindowLocator(handle=10), ElementLocator(automation_id="name"), "private")
    assert error.value.code == "runtime_draining"
    assert backend.saved == []


def test_enclosing_admission_is_preserved_when_recovery_is_disabled(
    native_journal: OperationRecoveryJournal, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    native_journal.configure(None, None)
    backend = FakeBackend()
    control, status = tmp_path / "control.json", tmp_path / "status.json"
    runtime = lifecycle.RUNTIME_LIFECYCLE
    runtime.configure(control, status)

    @tooling.compact_errors("ui_set_value")
    def admitted() -> dict[str, Any]:
        control.write_text(json.dumps(lifecycle.lifecycle_control_request(
            "drain", "uia-drain", time.time() + 10,
        )), encoding="utf-8")
        assert runtime.snapshot()["active_mutations"] == 1
        return UIAutomationService(backend).set_value(
            WindowLocator(handle=10), ElementLocator(automation_id="name"), "private",
        )

    result = admitted()
    assert result["ok"] is True
    assert backend.saved == [("value", "private")]
    assert runtime.snapshot()["state"] == "DRAINING"
    assert runtime.snapshot()["active_mutations"] == 0


def test_inherited_context_cannot_bypass_drain_after_parent_finishes(
    native_journal: OperationRecoveryJournal, tmp_path: Path,
) -> None:
    native_journal.configure(None, None)
    backend = FakeBackend()
    runtime = lifecycle.RUNTIME_LIFECYCLE
    control, status = tmp_path / "control.json", tmp_path / "status.json"
    runtime.configure(control, status)

    async def scenario() -> None:
        release = asyncio.Event()
        children: list[asyncio.Task[None]] = []

        @tooling.compact_errors("ui_set_value")
        async def parent() -> dict[str, bool]:
            async def child() -> None:
                await release.wait()
                UIAutomationService(backend).set_value(
                    WindowLocator(handle=10), ElementLocator(automation_id="name"), "private",
                )

            children.append(asyncio.create_task(child()))
            return {"ok": True}

        assert (await parent())["ok"] is True
        assert runtime.snapshot()["active_mutations"] == 0
        control.write_text(json.dumps(lifecycle.lifecycle_control_request(
            "drain", "uia-drain", time.time() + 10,
        )), encoding="utf-8")
        assert runtime.poll_control() == "DRAINING"
        release.set()
        with pytest.raises(ToolError) as error:
            await children[0]
        assert error.value.code == "runtime_draining"

    asyncio.run(scenario())
    assert backend.saved == []


def test_uia_mutation_audit_omits_locator_contents(
    native_journal: OperationRecoveryJournal, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeBackend()
    private = "private-automation-id-canary"
    backend.window.children[1].automation_id = private
    monkeypatch.setattr(registry, "UI_AUTOMATION", UIAutomationService(backend))
    events: list[Any] = []
    monkeypatch.setattr(registry, "audit_action", lambda *args, **kwargs: events.append((args, kwargs)))
    server = MCPServer("uia-private-audit")
    registry.register(server)

    async def scenario() -> None:
        async with Client(server) as client:
            result = await client.call_tool("ui_set_value", {
                "window": {"handle": 10}, "element": {"automation_id": private}, "value": "private-value",
            })
            assert result.structured_content and result.structured_content["ok"] is True

    asyncio.run(scenario())
    assert events and private not in json.dumps(events)
