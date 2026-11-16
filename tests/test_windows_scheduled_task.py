from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from mcp import Client
from mcp.server import MCPServer

from tools.windows import registry


def test_scheduled_task_status_exposes_only_local_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_require_windows", lambda: None)
    registered = SimpleNamespace(State=4, Enabled=True, LastTaskResult=267009, XML="private task definition")
    folder = Mock(GetTask=Mock(return_value=registered))
    service = Mock(GetFolder=Mock(return_value=folder))
    create_object = Mock(return_value=service)
    com = SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock(), COMError=OSError)

    def load(name: str) -> Any:
        return com if name == "comtypes" else SimpleNamespace(CreateObject=create_object)

    monkeypatch.setattr(registry, "import_module", load, raising=False)
    server = MCPServer("scheduled-task-test")
    registry.register(server)

    async def scenario() -> None:
        async with Client(server) as client:
            denied = await client.call_tool("windows_scheduled_task", {"task_path": r"\Folder\..\Name"})
            assert denied.structured_content and denied.structured_content["error"] == "invalid_task_path"
            create_object.assert_not_called()
            result = await client.call_tool("windows_scheduled_task", {"task_path": r"\Folder\Name"})
            assert result.structured_content == {
                "ok": True, "task_path": r"\Folder\Name", "state": "running",
                "enabled": True, "last_task_result": 267009,
            }

    asyncio.run(scenario())
    create_object.assert_called_once_with("Schedule.Service")
    service.Connect.assert_called_once_with()
    service.GetFolder.assert_called_once_with(r"\Folder")
    folder.GetTask.assert_called_once_with("Name")
    com.CoInitialize.assert_called_once_with()
    com.CoUninitialize.assert_called_once_with()
