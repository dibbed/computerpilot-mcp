from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from mcp import Client

from core.registry import create_server


def _structured(result: Any) -> dict[str, Any]:
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def test_apply_code_action_requires_complete_source_hash_guard(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("old\n", encoding="utf-8")
    action = {
        "edit": {
            "changes": {
                target.as_uri(): [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 3},
                        },
                        "newText": "new",
                    }
                ]
            }
        }
    }

    async def scenario() -> None:
        async with Client(create_server(), raise_exceptions=True) as client:
            missing = _structured(
                await client.call_tool(
                    "apply_code_action",
                    {"root": str(tmp_path), "action": action, "dry_run": True},
                )
            )
            assert missing["ok"] is False
            assert missing["error"] == "lsp_edit_precondition_required"

            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            guarded = _structured(
                await client.call_tool(
                    "apply_code_action",
                    {
                        "root": str(tmp_path),
                        "action": action,
                        "expected_sha256": {"a.py": digest},
                        "dry_run": True,
                    },
                )
            )
            assert guarded["ok"] is True
            assert guarded["dry_run"] is True
            assert guarded["file_count"] == 1

    asyncio.run(scenario())
    assert target.read_text(encoding="utf-8") == "old\n"
