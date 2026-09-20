"""Semantic Windows UI Automation with bounded locator-based traversal."""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol

from core.errors import ToolError


class UIABackend(Protocol):
    def windows(self) -> list[Any]: ...
    def children(self, element: Any) -> list[Any]: ...
    def properties(self, element: Any) -> dict[str, Any]: ...
    def invoke(self, element: Any) -> None: ...
    def set_value(self, element: Any, value: str) -> None: ...
    def select(self, element: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class WindowLocator:
    title: str | None = None
    title_contains: str | None = None
    pid: int | None = None
    handle: int | None = None


@dataclass(frozen=True, slots=True)
class ElementLocator:
    name: str | None = None
    name_contains: str | None = None
    automation_id: str | None = None
    control_type: str | None = None
    class_name: str | None = None


class NativeUIABackend:
    """Lazy adapter around uiautomation so non-Windows startup stays portable."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise ToolError("windows_only", "Semantic UI Automation requires Windows.")
        try:
            import uiautomation as automation
        except ImportError as exc:
            raise ToolError("uiautomation_not_installed", "Install uiautomation to use semantic desktop tools.") from exc
        self._automation = automation

    def windows(self) -> list[Any]:
        return list(self._automation.GetRootControl().GetChildren())

    def children(self, element: Any) -> list[Any]:
        return list(element.GetChildren())

    def properties(self, element: Any) -> dict[str, Any]:
        rect = element.BoundingRectangle
        return {
            "name": str(element.Name or ""),
            "automation_id": str(element.AutomationId or ""),
            "control_type": str(element.ControlTypeName or ""),
            "class_name": str(element.ClassName or ""),
            "pid": int(element.ProcessId or 0),
            "handle": int(element.NativeWindowHandle or 0),
            "enabled": bool(element.IsEnabled),
            "rect": {
                "left": int(rect.left),
                "top": int(rect.top),
                "right": int(rect.right),
                "bottom": int(rect.bottom),
            },
        }

    def invoke(self, element: Any) -> None:
        try:
            element.GetInvokePattern().Invoke()
        except Exception as exc:
            raise ToolError("uia_pattern_unsupported", "Element does not support the Invoke pattern.") from exc

    def set_value(self, element: Any, value: str) -> None:
        try:
            element.GetValuePattern().SetValue(value)
        except Exception as exc:
            raise ToolError("uia_pattern_unsupported", "Element does not support the Value pattern.") from exc

    def select(self, element: Any) -> None:
        try:
            element.GetSelectionItemPattern().Select()
        except Exception as exc:
            raise ToolError("uia_pattern_unsupported", "Element does not support the SelectionItem pattern.") from exc


class UIAutomationService:
    def __init__(self, backend: UIABackend | None = None) -> None:
        self._backend = backend

    @property
    def backend(self) -> UIABackend:
        if self._backend is None:
            self._backend = NativeUIABackend()
        return self._backend

    @staticmethod
    def _matches(values: dict[str, Any], locator: WindowLocator | ElementLocator) -> bool:
        if isinstance(locator, WindowLocator):
            return (
                (locator.title is None or values.get("name") == locator.title)
                and (locator.title_contains is None or locator.title_contains.casefold() in str(values.get("name", "")).casefold())
                and (locator.pid is None or values.get("pid") == locator.pid)
                and (locator.handle is None or values.get("handle") == locator.handle)
            )
        return (
            (locator.name is None or values.get("name") == locator.name)
            and (locator.name_contains is None or locator.name_contains.casefold() in str(values.get("name", "")).casefold())
            and (locator.automation_id is None or values.get("automation_id") == locator.automation_id)
            and (locator.control_type is None or str(values.get("control_type", "")).casefold() == locator.control_type.casefold())
            and (locator.class_name is None or values.get("class_name") == locator.class_name)
        )

    @staticmethod
    def _snapshot(values: dict[str, Any]) -> dict[str, Any]:
        allowed = {"name", "automation_id", "control_type", "class_name", "pid", "handle", "enabled", "rect"}
        return {key: value for key, value in values.items() if key in allowed}

    def _windows(self, locator: WindowLocator | None = None) -> list[tuple[Any, dict[str, Any]]]:
        matches: list[tuple[Any, dict[str, Any]]] = []
        for window in self.backend.windows():
            values = self.backend.properties(window)
            if locator is None or self._matches(values, locator):
                matches.append((window, values))
        return matches

    def list_windows(self, locator: WindowLocator | None = None, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        matches = self._windows(locator)
        page = matches[offset:offset + limit]
        return {
            "items": [self._snapshot(values) for _, values in page],
            "count": len(page),
            "total_count": len(matches),
            "offset": offset,
            "has_more": offset + len(page) < len(matches),
        }

    def _resolve_window(self, locator: WindowLocator) -> Any:
        matches = self._windows(locator)
        if not matches:
            raise ToolError("uia_window_not_found", "No window matched the locator.")
        if len(matches) != 1:
            raise ToolError("uia_window_ambiguous", f"Window locator matched {len(matches)} windows; refine it.")
        return matches[0][0]

    def _find_raw(
        self,
        window: WindowLocator,
        locator: ElementLocator,
        *,
        max_depth: int = 12,
        max_nodes: int = 5_000,
    ) -> list[tuple[Any, dict[str, Any]]]:
        root = self._resolve_window(window)
        queue: deque[tuple[Any, int]] = deque([(root, 0)])
        matches: list[tuple[Any, dict[str, Any]]] = []
        visited = 0
        while queue:
            element, depth = queue.popleft()
            visited += 1
            if visited > max_nodes:
                raise ToolError("uia_traversal_limit", f"UI traversal exceeded {max_nodes} nodes.")
            values = self.backend.properties(element)
            if depth > 0 and self._matches(values, locator):
                matches.append((element, values))
            if depth < max_depth:
                queue.extend((child, depth + 1) for child in self.backend.children(element))
        return matches

    def find_elements(
        self,
        window: WindowLocator,
        locator: ElementLocator,
        *,
        offset: int = 0,
        limit: int = 100,
        max_depth: int = 12,
        max_nodes: int = 5_000,
    ) -> dict[str, Any]:
        matches = self._find_raw(window, locator, max_depth=max_depth, max_nodes=max_nodes)
        page = matches[offset:offset + limit]
        return {
            "items": [self._snapshot(values) for _, values in page],
            "count": len(page),
            "total_count": len(matches),
            "offset": offset,
            "has_more": offset + len(page) < len(matches),
        }

    def get_element(self, window: WindowLocator, locator: ElementLocator) -> dict[str, Any]:
        element, values = self._resolve_element(window, locator)
        del element
        return self._snapshot(values)

    def _resolve_element(self, window: WindowLocator, locator: ElementLocator) -> tuple[Any, dict[str, Any]]:
        matches = self._find_raw(window, locator)
        if not matches:
            raise ToolError("uia_element_not_found", "No element matched the locator.")
        if len(matches) != 1:
            raise ToolError("uia_element_ambiguous", f"Element locator matched {len(matches)} elements; refine it.")
        return matches[0]

    def invoke(self, window: WindowLocator, locator: ElementLocator) -> dict[str, Any]:
        element, values = self._resolve_element(window, locator)
        self.backend.invoke(element)
        return {"ok": True, "element": self._snapshot(values), "action": "invoke"}

    def set_value(self, window: WindowLocator, locator: ElementLocator, value: str) -> dict[str, Any]:
        element, values = self._resolve_element(window, locator)
        self.backend.set_value(element, value)
        return {"ok": True, "element": self._snapshot(values), "action": "set_value", "value_chars": len(value)}

    def select(self, window: WindowLocator, locator: ElementLocator) -> dict[str, Any]:
        element, values = self._resolve_element(window, locator)
        self.backend.select(element)
        return {"ok": True, "element": self._snapshot(values), "action": "select"}

    def wait_for_element(
        self,
        window: WindowLocator,
        locator: ElementLocator,
        *,
        timeout_sec: float = 10,
        interval_sec: float = 0.1,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_sec
        while True:
            matches = self._find_raw(window, locator)
            if len(matches) == 1:
                return self._snapshot(matches[0][1])
            if len(matches) > 1:
                raise ToolError("uia_element_ambiguous", f"Element locator matched {len(matches)} elements; refine it.")
            if time.monotonic() >= deadline:
                raise ToolError("uia_wait_timeout", f"Element did not appear within {timeout_sec:g} seconds.")
            time.sleep(interval_sec)


UI_AUTOMATION = UIAutomationService()
