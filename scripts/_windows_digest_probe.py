"""Temporary Windows-only diagnostic: print the normalized historical catalogue digest."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from core import registry  # noqa: E402


def encode(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


async def main() -> None:
    descriptions = {
        "run_process": "Run an executable with a timeout and optional per-stream output limits.",
        "run_powershell": "Run PowerShell non-interactively with optional output limits and a hard timeout.",
        "replace_exact": "Replace an exact small fragment only when its match count is known.",
        "submit_job": (
            "Submit a retry-safe durable command. Execution timeout starts after launch; "
            "optional queue timeout bounds pre-launch waiting."
        ),
        "job_status": "Read a durable job status by ID after reconnect or server restart.",
    }
    terminal_tools = {"run_process", "run_powershell", "run_cmd", "run_shell"}
    historical_access = "read only when the command writes nothing; read commands may overlap."
    results = {}
    for profile in ("full", "coding", "coding_core"):
        registry.SETTINGS = replace(
            registry.SETTINGS,
            tool_profile=profile,
            persistent_enabled=False,
            extensions_enabled=False,
        )
        tools = await registry.create_server().list_tools()
        metadata = [
            tool.model_dump(mode="json", by_alias=True, exclude_none=True, exclude={"output_schema"})
            for tool in tools
        ]
        normalized = []
        for item in metadata:
            if item["name"] in descriptions:
                item["description"] = descriptions[item["name"]]
            if item["name"] in terminal_tools:
                properties = item.get("inputSchema", {}).get("properties", {})
                properties.pop("operation_id", None)
                if "access" in properties:
                    properties["access"] = {**properties["access"], "description": historical_access}
            normalized.append(item)
        historical = [item for item in normalized if item["name"] != "cancel_operation"]
        encoded = encode(historical)
        results[profile] = {
            "tool_count": len(tools),
            "normalized_bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }
    print(f"::error title=Windows historical catalogue digests::{json.dumps(results, separators=(',', ':'))}")


if __name__ == "__main__":
    asyncio.run(main())
