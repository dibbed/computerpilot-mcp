"""Bounded MCP image delivery helpers for screenshot-producing tools."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any, Literal, cast

from mcp.types import CallToolResult, ContentBlock, ImageContent, TextContent
from PIL import Image

from core.config import SETTINGS

ImageDelivery = Literal["path", "image", "auto"]

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _mime_type(path: Path) -> str:
    return _MIME_BY_SUFFIX.get(path.suffix.casefold(), "application/octet-stream")


def _jpeg_preview(source: Path, *, max_bytes: int, quality: int) -> tuple[bytes, int, int]:
    """Encode a bounded JPEG preview, preserving as much screenshot detail as possible."""

    with Image.open(source) as opened:
        image = opened.convert("RGB")
        current = image
        current_quality = quality

        while True:
            buffer = io.BytesIO()
            current.save(buffer, format="JPEG", quality=current_quality, optimize=True)
            payload = buffer.getvalue()
            if len(payload) <= max_bytes:
                return payload, current.width, current.height

            if current_quality > 60:
                current_quality = max(60, current_quality - 10)
                continue

            if current.width <= 512 and current.height <= 512:
                return payload, current.width, current.height

            next_width = max(1, int(current.width * 0.8))
            next_height = max(1, int(current.height * 0.8))
            current = current.resize((next_width, next_height), Image.Resampling.LANCZOS)
            current_quality = quality


def _vision_payload(
    path: Path,
    *,
    delivery: ImageDelivery,
    max_bytes: int,
    jpeg_quality: int,
) -> tuple[bytes | None, str | None, bool, int | None, int | None]:
    if delivery == "path":
        return None, None, False, None, None

    raw = path.read_bytes()
    source_mime = _mime_type(path)
    if delivery == "image" or len(raw) <= max_bytes:
        return raw, source_mime, False, None, None

    preview, width, height = _jpeg_preview(path, max_bytes=max_bytes, quality=jpeg_quality)
    return preview, "image/jpeg", True, width, height


def image_tool_result(
    metadata: dict[str, Any],
    path: str | Path,
    *,
    delivery: ImageDelivery = "auto",
    max_bytes: int | None = None,
    jpeg_quality: int | None = None,
) -> dict[str, Any]:
    """Return structured metadata plus an MCP image block without embedding base64 in JSON."""

    source = Path(path)
    byte_limit = SETTINGS.vision_max_bytes if max_bytes is None else max_bytes
    quality = SETTINGS.vision_jpeg_quality if jpeg_quality is None else jpeg_quality
    image_bytes, mime_type, preview, preview_width, preview_height = _vision_payload(
        source,
        delivery=delivery,
        max_bytes=max(1, byte_limit),
        jpeg_quality=min(max(quality, 40), 95),
    )

    structured = dict(metadata)
    vision: dict[str, Any] = {
        "delivery": delivery,
        "included": image_bytes is not None,
        "preview": preview,
    }
    if image_bytes is not None and mime_type is not None:
        vision.update({"mime_type": mime_type, "bytes": len(image_bytes)})
        if preview_width is not None and preview_height is not None:
            vision.update({"width": preview_width, "height": preview_height})
    structured["vision"] = vision

    text = json.dumps(structured, ensure_ascii=False, separators=(",", ":"))
    content: list[ContentBlock] = [TextContent(type="text", text=text)]
    if image_bytes is not None and mime_type is not None:
        content.append(
            ImageContent(
                type="image",
                data=base64.b64encode(image_bytes).decode("ascii"),
                mime_type=mime_type,
            )
        )

    result = CallToolResult(content=content, structured_content=structured)
    # MCPServer recognizes CallToolResult at runtime; the cast preserves the existing
    # dict return annotation/output schema used by screenshot tools and callers.
    return cast(dict[str, Any], result)
