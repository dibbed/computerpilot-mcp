"""MCP registration for screenshots, foreground windows, mouse, and keyboard."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from core.audit import audit_action
from core.config import SETTINGS, ensure_runtime_dirs, resolve_path
from core.errors import ToolError
from core.tooling import MUTATING, READ_ONLY, compact_errors
from tools.desktop import native


def register(mcp: MCPServer) -> None:
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
    ) -> dict[str, Any]:
        """Capture all screens or one rectangle to a file without embedding image bytes."""

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
        return {"ok": True, "path": str(output), "bytes": output.stat().st_size, "width": image.width, "height": image.height}

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
