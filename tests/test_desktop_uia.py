from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from core.errors import ToolError
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
