"""MCP registration for screenshots, foreground windows, mouse, and keyboard."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import BaseModel, ConfigDict, Field

from core.audit import audit_action
from core.config import SETTINGS, ensure_runtime_dirs, resolve_path
from core.errors import ToolError
from core.media import ImageDelivery, image_tool_result
from core.tooling import MUTATING, READ_ONLY, compact_errors
from tools.desktop import native
from tools.desktop.uia import UI_AUTOMATION, ElementLocator, WindowLocator


class WindowLocatorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=500)
    title_contains: str | None = Field(default=None, max_length=500)
    pid: int | None = Field(default=None, ge=1)
    handle: int | None = Field(default=None, ge=1)

    def locator(self) -> WindowLocator:
        return WindowLocator(**self.model_dump())


class ElementLocatorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=500)
    name_contains: str | None = Field(default=None, max_length=500)
    automation_id: str | None = Field(default=None, max_length=500)
    control_type: str | None = Field(default=None, max_length=200)
    class_name: str | None = Field(default=None, max_length=500)

    def locator(self) -> ElementLocator:
        return ElementLocator(**self.model_dump())


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("ui_list_windows")
    def ui_list_windows(
        locator: WindowLocatorInput | None = None,
        offset: Annotated[int, Field(ge=0, le=10_000_000)] = 0,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        """List top-level Windows UI Automation windows with optional semantic filters."""

        return UI_AUTOMATION.list_windows(locator.locator() if locator else None, offset=offset, limit=limit)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("ui_find_elements")
    def ui_find_elements(
        window: WindowLocatorInput,
        element: ElementLocatorInput,
        offset: Annotated[int, Field(ge=0, le=10_000_000)] = 0,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        max_depth: Annotated[int, Field(ge=1, le=50)] = 12,
        max_nodes: Annotated[int, Field(ge=1, le=20_000)] = 5_000,
    ) -> dict[str, Any]:
        """Find elements within exactly one window using bounded breadth-first traversal."""

        return UI_AUTOMATION.find_elements(
            window.locator(), element.locator(), offset=offset, limit=limit, max_depth=max_depth, max_nodes=max_nodes,
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("ui_get_element")
    def ui_get_element(window: WindowLocatorInput, element: ElementLocatorInput) -> dict[str, Any]:
        """Resolve exactly one semantic UI element and return a stable metadata snapshot."""

        return UI_AUTOMATION.get_element(window.locator(), element.locator())

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("ui_invoke")
    def ui_invoke(window: WindowLocatorInput, element: ElementLocatorInput) -> dict[str, Any]:
        """Re-resolve exactly one element and invoke its native UI Automation pattern."""

        audit_action("ui_invoke", target="semantic_element", details={"locator": element.model_dump(exclude_none=True)})
        return UI_AUTOMATION.invoke(window.locator(), element.locator())

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("ui_set_value")
    def ui_set_value(
        window: WindowLocatorInput,
        element: ElementLocatorInput,
        value: Annotated[str, Field(max_length=100_000)],
    ) -> dict[str, Any]:
        """Re-resolve one element and set its Value pattern without logging the value."""

        audit_action(
            "ui_set_value",
            target="semantic_element",
            details={"locator": element.model_dump(exclude_none=True), "value_chars": len(value)},
        )
        return UI_AUTOMATION.set_value(window.locator(), element.locator(), value)

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("ui_select")
    def ui_select(window: WindowLocatorInput, element: ElementLocatorInput) -> dict[str, Any]:
        """Re-resolve one element and select it through SelectionItem."""

        audit_action("ui_select", target="semantic_element", details={"locator": element.model_dump(exclude_none=True)})
        return UI_AUTOMATION.select(window.locator(), element.locator())

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("ui_wait_for_element")
    def ui_wait_for_element(
        window: WindowLocatorInput,
        element: ElementLocatorInput,
        timeout_sec: Annotated[float, Field(gt=0, le=300)] = 10,
        interval_sec: Annotated[float, Field(gt=0, le=5)] = 0.1,
    ) -> dict[str, Any]:
        """Wait until exactly one matching semantic element exists or timeout."""

        return UI_AUTOMATION.wait_for_element(
            window.locator(), element.locator(), timeout_sec=timeout_sec, interval_sec=interval_sec,
        )

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("desktop_screenshot")
    def desktop_screenshot(
        path: Annotated[str | None, Field(max_length=32_767)] = None,
        all_screens: bool = True,
        x: Annotated[int | None, Field(ge=-100_000, le=100_000)] = None,
        y: Annotated[int | None, Field(ge=-100_000, le=100_000)] = None,
        width: Annotated[int | None, Field(gt=0, le=100_000)] = None,
        height: Annotated[int | None, Field(gt=0, le=100_000)] = None,
        image_format: Literal["png", "jpeg"] = "png",
        quality: Annotated[int, Field(ge=1, le=100)] = 90,
        delivery: ImageDelivery = "auto",
    ) -> dict[str, Any]:
        """Capture the desktop and return metadata plus optional MCP image content."""

        native.require_windows()
        try:
            from PIL import ImageGrab
        except ImportError as exc:
            raise ToolError("pillow_not_installed", "Desktop screenshots require Pillow.") from exc
        supplied = [x is not None, y is not None, width is not None, height is not None]
        if any(supplied) and not all(supplied):
            raise ToolError("incomplete_region", "x, y, width, and height must be supplied together.")
        if all(supplied):
            assert x is not None and y is not None and width is not None and height is not None
            bbox = (x, y, x + width, y + height)
        else:
            bbox = None
        ensure_runtime_dirs()
        if path:
            output = resolve_path(path)
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            output = SETTINGS.screenshot_dir / f"desktop_{stamp}.{image_format}"
        output.parent.mkdir(parents=True, exist_ok=True)
        image = ImageGrab.grab(bbox=bbox, all_screens=all_screens)
        save_args = {"quality": quality} if image_format == "jpeg" else {}
        image.save(output, format=image_format.upper(), **save_args)
        audit_action("desktop_screenshot", target=output, details={"all_screens": all_screens, "region": bbox})
        metadata = {"ok": True, "path": str(output), "bytes": output.stat().st_size, "width": image.width, "height": image.height}
        return image_tool_result(metadata, output, delivery=delivery)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    @compact_errors("active_window")
    def active_window() -> dict[str, Any]:
        """Return title, PID, handle, and rectangle for the foreground window."""

        return native.foreground_window()

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("mouse_click")
    def mouse_click(
        x: Annotated[int, Field(ge=-100_000, le=100_000)],
        y: Annotated[int, Field(ge=-100_000, le=100_000)],
        button: Literal["left", "right", "middle"] = "left",
        clicks: Annotated[int, Field(ge=1, le=10)] = 1,
        interval_ms: Annotated[int, Field(ge=0, le=5_000)] = 100,
    ) -> dict[str, Any]:
        """Move the pointer and click through the native Windows input API."""

        audit_action("mouse_click", target=f"{x},{y}", details={"button": button, "clicks": clicks})
        native.click_mouse(x, y, button, clicks, interval_ms)
        return {"ok": True, "x": x, "y": y, "button": button, "clicks": clicks}

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("keyboard_type")
    def keyboard_type(
        text: Annotated[str, Field(max_length=10_000)],
        interval_ms: Annotated[int, Field(ge=0, le=1_000)] = 0,
    ) -> dict[str, Any]:
        """Type Unicode text into the active window without echoing the text back."""

        audit_action("keyboard_type", target="active_window", details={"text_chars": len(text), "interval_ms": interval_ms})
        native.type_unicode(text, interval_ms)
        return {"ok": True, "typed_chars": len(text)}

    @mcp.tool(annotations=MUTATING, structured_output=True)
    @compact_errors("hotkey")
    def hotkey(
        keys: Annotated[list[str], Field(min_length=1, max_length=10)],
        hold_ms: Annotated[int, Field(ge=0, le=5_000)] = 30,
    ) -> dict[str, Any]:
        """Press a bounded native Windows key combination such as ctrl+shift+s."""

        audit_action("hotkey", target="active_window", details={"keys": [key.casefold() for key in keys], "hold_ms": hold_ms})
        native.send_hotkey(keys, hold_ms)
        return {"ok": True, "keys": [key.casefold() for key in keys]}
