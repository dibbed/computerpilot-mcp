"""Bounded JSON validation and the Core-owned external result contract."""

from __future__ import annotations

import base64
import json
import math
from collections.abc import Mapping
from typing import Any, Literal

from mcp import types

from core.errors import ToolError
from core.extensions.models import LIMITS, ExtensionToolDescriptor, StrictDefinition
from core.extensions.registry import validate_tool_value
from core.extensions.security import CredentialRedactor


class ResultLimits(StrictDefinition):
    returned_bytes: int
    original_bytes_if_known: int | None
    item_limit: int = LIMITS.schema_nodes
    depth_limit: int = LIMITS.json_depth


class ExtensionResult(StrictDefinition):
    ok: bool
    source_type: Literal["external_mcp"] = "external_mcp"
    extension_id: str
    provider: str
    tool: str
    catalog_generation: int
    operation_id: str | None = None
    outcome: Literal["succeeded", "not_dispatched", "uncertain", "failed"]
    content: tuple[dict[str, Any], ...] = ()
    structured_content: dict[str, Any] | None = None
    truncated: bool = False
    limits: ResultLimits
    artifact: dict[str, Any] | None = None
    error: str | None = None
    message: str | None = None


def bounded_json(value: Any, max_bytes: int, *, error_code: str) -> bytes:
    """Reject excessive/non-JSON values before allocating their serialized payload."""
    pending = [(value, 0)]
    nodes = 0
    estimated_bytes = 0
    try:
        while pending:
            item, depth = pending.pop()
            nodes += 1
            if nodes > LIMITS.schema_nodes or depth > LIMITS.json_depth:
                raise ValueError("JSON structure limit")
            if isinstance(item, str):
                if len(item) > max_bytes:
                    raise ValueError("JSON string limit")
                estimated_bytes += len(item.encode("utf-8"))
            elif type(item) in (dict, list):
                if len(item) + nodes > LIMITS.schema_nodes:
                    raise ValueError("JSON collection limit")
                if isinstance(item, dict):
                    if any(not isinstance(key, str) for key in item):
                        raise ValueError("JSON key type")
                    pending.extend((key, depth + 1) for key in item)
                    pending.extend((child, depth + 1) for child in item.values())
                else:
                    pending.extend((child, depth + 1) for child in item)
            elif item is None or type(item) in (bool, int):
                pass
            elif type(item) is float and math.isfinite(item):
                pass
            else:
                raise ValueError("JSON value type")
            if estimated_bytes > max_bytes:
                raise ValueError("JSON byte limit")
        data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(data) > max_bytes:
            raise ValueError("JSON byte limit")
        return data
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise ToolError(error_code, "External data exceeds the supported JSON contract.") from exc


def validate_arguments(descriptor: ExtensionToolDescriptor, arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    if arguments is not None and not isinstance(arguments, Mapping):
        raise ToolError("invalid_arguments", "External tool arguments must be a JSON object.")
    args = dict(arguments) if arguments is not None else {}
    bounded_json(args, LIMITS.argument_bytes, error_code="invalid_arguments")
    if not validate_tool_value(descriptor.input_schema, args).valid:
        raise ToolError("invalid_arguments", "External tool arguments do not match the accepted schema.")
    return args


def normalize_result(
    descriptor: ExtensionToolDescriptor, result: types.CallToolResult, redactor: CredentialRedactor | None = None,
) -> dict[str, Any]:
    if not isinstance(result, types.CallToolResult) or result.result_type != "complete":
        raise ToolError("malformed_result", "External calls require one terminal tool result.")
    original = bounded_json(result.model_dump(mode="json", by_alias=True), LIMITS.frame_bytes, error_code="output_too_large")
    redactor = redactor or CredentialRedactor()
    if result.is_error:
        content: list[dict[str, Any]] = []
        structured = None
    else:
        structured = result.structured_content
        if descriptor.output_schema is not None and not validate_tool_value(descriptor.output_schema, structured).valid:
            raise ToolError("malformed_result", "External output does not match the accepted provider schema.")
        content = []
        for block in result.content:
            if isinstance(block, types.TextContent):
                content.append({"type": "text", "text": block.text})
            elif isinstance(block, types.ImageContent | types.AudioContent):
                try:
                    decoded = base64.b64decode(block.data, validate=True)
                except (ValueError, UnicodeError) as exc:
                    raise ToolError("malformed_result", "External binary content is invalid.") from exc
                if redactor.sensitive_binary(block.data, decoded):
                    raise ToolError("malformed_result", "Credential-bearing binary output is withheld.")
                content.append({"type": block.type, "data": block.data, "mimeType": block.mime_type})
            elif isinstance(block, types.ResourceLink):
                content.append({"type": "resource_link", "name": block.name, "uri": str(block.uri)})
            elif isinstance(block, types.EmbeddedResource):
                resource = block.resource
                if isinstance(resource, types.TextResourceContents):
                    content.append({"type": "resource", "uri": str(resource.uri), "text": resource.text})
                else:
                    try:
                        decoded = base64.b64decode(resource.blob, validate=True)
                    except (ValueError, UnicodeError) as exc:
                        raise ToolError("malformed_result", "External resource content is invalid.") from exc
                    if redactor.sensitive_binary(resource.blob, decoded):
                        raise ToolError("malformed_result", "Credential-bearing resource output is withheld.")
                    content.append({"type": "resource", "uri": str(resource.uri), "blob": resource.blob})
            else:
                raise ToolError("malformed_result", "External content type is unsupported.")
    sanitized = redactor.json({"content": content, "structured_content": structured})
    content, structured = sanitized["content"], sanitized["structured_content"]
    returned = bounded_json(sanitized, LIMITS.inline_bytes, error_code="output_too_large")
    return ExtensionResult(
        ok=not result.is_error, extension_id=descriptor.extension_id, provider=descriptor.identity.provider,
        tool=descriptor.public_name, catalog_generation=descriptor.catalog_generation,
        outcome="failed" if result.is_error else "succeeded", content=tuple(content), structured_content=structured,
        limits=ResultLimits(returned_bytes=len(returned), original_bytes_if_known=len(original)),
        error="provider_error" if result.is_error else None,
        message="The external provider reported a tool error." if result.is_error else None,
    ).model_dump(mode="json")
