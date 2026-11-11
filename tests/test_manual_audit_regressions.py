"""Regressions derived from observed ComputerPilot manual-integration failures."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from mcp.server import MCPServer

from tools.desktop import native
from tools.testing import registry as testing_registry


def test_hotkey_latin_virtual_keys_do_not_depend_on_keyboard_layout(monkeypatch: Any) -> None:
    class LayoutWithoutLatin:
        def VkKeyScanW(self, key: str) -> int:
            assert key not in "sSaA19"
            return -1

    monkeypatch.setattr(native, "_USER32", LayoutWithoutLatin())
    assert native.virtual_key("s") == native.virtual_key("S") == 0x53
    assert native.virtual_key("a") == 0x41
    assert native.virtual_key("1") == 0x31
    assert native.virtual_key("ctrl") == 0x11


def test_hotkey_releases_pressed_modifiers_if_input_fails(monkeypatch: Any) -> None:
    events: list[tuple[int, bool]] = []

    def send_key(code: int, key_up: bool = False) -> None:
        events.append((code, key_up))
        if code == 0x53 and not key_up:
            raise OSError("synthetic send failure")

    monkeypatch.setattr(native, "require_windows", lambda: None)
    monkeypatch.setattr(native, "_send_key", send_key)
    with pytest.raises(OSError, match="synthetic send failure"):
        native.send_hotkey(["ctrl", "s"], 0)
    assert events == [(0x11, False), (0x53, False), (0x11, True)]


def test_watch_bootstraps_worker_outside_computerpilot_root(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}

    class RecordingStore:
        def submit(
            self, command: list[str], cwd: Path, timeout: float,
            key: str, *, queue_timeout_sec: float | None,
        ) -> dict[str, Any]:
            captured.update(command=command, cwd=cwd, timeout=timeout, key=key)
            return {"ok": True, "job_id": "test-job"}

    monkeypatch.setattr(testing_registry, "audit_action", lambda *a, **k: None)
    server = MCPServer("manual-watch-regression")
    testing_registry.register(server, background_enabled=True, store_getter=lambda: cast(Any, RecordingStore()))
    tool = server._tool_manager.get_tool("start_validation_watch")
    assert tool is not None

    output = tool.fn(repo=str(tmp_path), poll_sec=0.1, debounce_sec=0.0, max_runtime_sec=1.0)
    assert output["ok"] is True
    assert captured["cwd"] == tmp_path
    assert captured["command"][1] == "-c"
    finished = subprocess.run(
        captured["command"], cwd=tmp_path, capture_output=True, text=True,
        timeout=15, check=False,
    )
    assert finished.returncode == 0, finished.stderr
    events = [json.loads(line)["event"] for line in finished.stdout.splitlines()]
    assert events[0] == "ready" and events[-1] == "stopped"
