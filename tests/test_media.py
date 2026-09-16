from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any, cast

from mcp import Client
from mcp.types import CallToolResult, ImageContent
from PIL import Image, ImageGrab

from core.media import image_tool_result
from core.registry import create_server
from tools.desktop import native


def _runtime_result(value: dict[str, Any]) -> CallToolResult:
    result = cast(Any, value)
    assert isinstance(result, CallToolResult)
    return result


def test_image_tool_result_preserves_structured_metadata_and_embeds_pixels(tmp_path: Path) -> None:
    target = tmp_path / "sample.png"
    Image.new("RGB", (32, 24), "white").save(target)
    original = target.read_bytes()

    result = _runtime_result(image_tool_result({"ok": True, "path": str(target), "bytes": len(original)}, target, delivery="image"))

    assert result.structured_content is not None
    assert result.structured_content["ok"] is True
    assert result.structured_content["path"] == str(target)
    assert result.structured_content["vision"] == {
        "delivery": "image",
        "included": True,
        "preview": False,
        "mime_type": "image/png",
        "bytes": len(original),
    }
    assert [block.type for block in result.content] == ["text", "image"]
    image_block = result.content[1]
    assert isinstance(image_block, ImageContent)
    assert image_block.mime_type == "image/png"
    assert base64.b64decode(image_block.data) == original


def test_image_tool_result_path_mode_keeps_legacy_metadata_only(tmp_path: Path) -> None:
    target = tmp_path / "sample.png"
    Image.new("RGB", (8, 8), "black").save(target)

    result = _runtime_result(image_tool_result({"ok": True, "path": str(target)}, target, delivery="path"))

    assert [block.type for block in result.content] == ["text"]
    assert result.structured_content is not None
    assert result.structured_content["vision"] == {"delivery": "path", "included": False, "preview": False}


def test_image_tool_result_auto_compresses_large_sources(tmp_path: Path) -> None:
    target = tmp_path / "large.png"
    Image.effect_noise((1600, 1200), 100).convert("RGB").save(target)
    source_size = target.stat().st_size
    assert source_size > 100_000

    result = _runtime_result(image_tool_result({"ok": True, "path": str(target)}, target, delivery="auto", max_bytes=100_000))

    assert result.structured_content is not None
    vision = result.structured_content["vision"]
    assert vision["included"] is True
    assert vision["preview"] is True
    assert vision["mime_type"] == "image/jpeg"
    assert vision["bytes"] < source_size
    assert vision["width"] > 0
    assert vision["height"] > 0
    image_block = result.content[1]
    assert isinstance(image_block, ImageContent)
    assert len(base64.b64decode(image_block.data)) == vision["bytes"]


def test_desktop_screenshot_returns_image_content_through_mcp(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(native, "require_windows", lambda: None)
    monkeypatch.setattr(ImageGrab, "grab", lambda **_: Image.new("RGB", (64, 48), "white"))
    target = tmp_path / "desktop.png"

    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            result = await client.call_tool(
                "desktop_screenshot",
                {"path": str(target), "delivery": "auto"},
            )
            assert result.is_error is not True
            assert isinstance(result.structured_content, dict)
            assert result.structured_content["ok"] is True
            assert result.structured_content["vision"]["included"] is True
            assert result.structured_content["vision"]["mime_type"] == "image/png"
            assert [block.type for block in result.content] == ["text", "image"]
            image_block = result.content[1]
            assert isinstance(image_block, ImageContent)
            assert base64.b64decode(image_block.data) == target.read_bytes()

    asyncio.run(scenario())


def test_view_image_returns_model_visible_pixels_through_mcp(tmp_path: Path) -> None:
    target = tmp_path / "view.png"
    Image.new("RGB", (80, 60), "green").save(target)

    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            result = await client.call_tool("view_image", {"path": str(target)})
            assert result.is_error is not True
            assert isinstance(result.structured_content, dict)
            assert result.structured_content["ok"] is True
            assert result.structured_content["width"] == 80
            assert result.structured_content["height"] == 60
            assert result.structured_content["mime_type"] == "image/png"
            assert [block.type for block in result.content] == ["text", "image"]
            image_block = result.content[1]
            assert isinstance(image_block, ImageContent)
            assert base64.b64decode(image_block.data) == target.read_bytes()

    asyncio.run(scenario())


def test_browser_screenshot_defaults_to_path_for_chatgpt_bridge() -> None:
    tools = {tool.name: tool for tool in asyncio.run(create_server().list_tools())}
    delivery = tools["browser_screenshot"].input_schema["properties"]["delivery"]
    assert delivery["default"] == "path"
    assert delivery["enum"] == ["path", "image", "auto"]


def test_read_file_auto_exposes_image_through_existing_catalog_tool(tmp_path: Path) -> None:
    target = tmp_path / "catalog.png"
    Image.new("RGB", (72, 54), "blue").save(target)

    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            result = await client.call_tool("read_file", {"path": str(target), "delivery": "auto"})
            assert result.is_error is not True
            assert isinstance(result.structured_content, dict)
            assert result.structured_content["path"] == str(target)
            assert result.structured_content["width"] == 72
            assert result.structured_content["height"] == 54
            assert result.structured_content["mime_type"] == "image/png"
            assert [block.type for block in result.content] == ["text", "image"]
            image_block = result.content[1]
            assert isinstance(image_block, ImageContent)
            assert base64.b64decode(image_block.data) == target.read_bytes()

    asyncio.run(scenario())
