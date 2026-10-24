from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import anyio
import pytest

from scripts import health_check


def test_silent_startup_has_deadline_and_safe_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class SilentClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            seen.update(kwargs)

        async def __aenter__(self) -> None:
            await asyncio.Event().wait()

        async def __aexit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(health_check, "Client", SilentClient)
    monkeypatch.setattr(health_check, "HEALTH_TOTAL_TIMEOUT_SEC", 0.05, raising=False)

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="mcp_startup"):
            await asyncio.wait_for(health_check.run_checks(), timeout=0.5)

    asyncio.run(scenario())
    assert seen["read_timeout_seconds"] == 30


def test_mcp_failure_payload_is_not_echoed() -> None:
    result = SimpleNamespace(is_error=True, content=[SimpleNamespace(text="secret-canary")])
    with pytest.raises(RuntimeError) as error:
        health_check._structured(result, "create_file")
    assert "create_file" in str(error.value) and "secret-canary" not in str(error.value)


def test_shielded_shutdown_past_deadline_cannot_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    class Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            with anyio.CancelScope(shield=True):
                await anyio.sleep(0.1)

        async def list_tools(self) -> Any:
            return SimpleNamespace(tools=[])

        async def call_tool(self, operation: str, arguments: dict[str, Any]) -> Any:
            from pathlib import Path

            data: dict[str, Any] = {"ok": True}
            if operation == "server_health":
                data["workflow_health"] = {}
            elif operation == "create_file":
                Path(arguments["path"]).write_text(arguments["content"], encoding="utf-8")
            elif operation == "read_file":
                data["content"] = Path(arguments["path"]).read_text(encoding="utf-8")
            elif operation == "replace_exact":
                path = Path(arguments["path"])
                path.write_text(path.read_text(encoding="utf-8").replace(arguments["old"], arguments["new"]), encoding="utf-8")
            elif operation == "run_process":
                data.update(exit_code=0, stdout={"text": "MCP_TERMINAL_OK"})
            elif operation == "delete_file":
                Path(arguments["path"]).unlink()
            return SimpleNamespace(is_error=False, structured_content=data)

    monkeypatch.setattr(health_check, "Client", Client)
    monkeypatch.setattr(health_check, "REQUIRED_TOOLS", set())
    monkeypatch.setattr(health_check, "HEALTH_TOTAL_TIMEOUT_SEC", 0.05)
    with pytest.raises(RuntimeError, match="shutdown"):
        asyncio.run(health_check.run_checks())
