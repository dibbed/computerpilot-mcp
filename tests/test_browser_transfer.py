import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from core.config import SETTINGS
from tools.browser import registry
from tools.browser.manager import BrowserManager, PoolKey, Session

ToolFunction = Callable[..., Any]


class Capture:
    def __init__(self) -> None:
        self.functions: dict[str, ToolFunction] = {}

    def tool(self, **kwargs: Any) -> Callable[[ToolFunction], ToolFunction]:
        def decorate(fn: ToolFunction) -> ToolFunction:
            self.functions[fn.__name__] = fn
            return fn

        return decorate


def test_browser_upload_requires_real_file_and_uses_semantic_file_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        source = tmp_path / "report.txt"
        source.write_text("hello", encoding="utf-8")
        locator = SimpleNamespace(count=AsyncMock(return_value=1), set_input_files=AsyncMock())
        locator.nth = lambda _: locator
        page = SimpleNamespace(url="https://example.test/", get_by_label=lambda label, **kwargs: locator)
        manager = BrowserManager()
        manager._sessions["s"] = Session(PoolKey("chromium", True), SimpleNamespace(), page, "chromium", True, generation=2)
        monkeypatch.setattr(registry, "MANAGER", manager)
        monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
        capture = Capture()
        registry.register(cast(Any, capture))

        result = await capture.functions["browser_upload"](
            str(source),
            session_id="s",
            label="Upload document",
        )

        locator.set_input_files.assert_awaited_once_with(str(source.resolve()), timeout=30_000.0)
        assert result["ok"] is True
        assert result["bytes"] == 5
        assert result["generation"] == 3

    asyncio.run(run())


class _DownloadContext:
    def __init__(self, download: Any) -> None:
        self._download = download

    async def __aenter__(self) -> "_DownloadContext":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    @property
    async def value(self) -> Any:
        return self._download


def test_browser_download_sanitizes_filename_and_stays_in_controlled_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        locator = SimpleNamespace(count=AsyncMock(return_value=1), click=AsyncMock())
        locator.nth = lambda _: locator

        async def save_as(path: str) -> None:
            Path(path).write_bytes(b"payload")

        download = SimpleNamespace(suggested_filename="../bad:name?.txt", save_as=AsyncMock(side_effect=save_as))
        page = SimpleNamespace(
            url="https://example.test/",
            get_by_role=lambda role, **kwargs: locator,
            expect_download=lambda **kwargs: _DownloadContext(download),
        )
        manager = BrowserManager()
        manager._sessions["s"] = Session(PoolKey("chromium", True), SimpleNamespace(), page, "chromium", True, generation=6)
        monkeypatch.setattr(registry, "MANAGER", manager)
        monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
        monkeypatch.setattr(registry, "SETTINGS", replace(SETTINGS, state_dir=tmp_path))
        capture = Capture()
        registry.register(cast(Any, capture))

        result = await capture.functions["browser_download"](
            session_id="s",
            role="button",
            name="Download",
        )

        output = Path(result["path"])
        assert output.parent == tmp_path / "browser_downloads"
        assert output.name.endswith("_bad_name_.txt")
        assert output.read_bytes() == b"payload"
        assert result["bytes"] == 7
        assert result["generation"] == 7

    asyncio.run(run())


def test_browser_download_removes_oversized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        locator = SimpleNamespace(count=AsyncMock(return_value=1), click=AsyncMock())
        locator.nth = lambda _: locator

        async def save_as(path: str) -> None:
            Path(path).write_bytes(b"0123456789")

        download = SimpleNamespace(suggested_filename="big.bin", save_as=AsyncMock(side_effect=save_as))
        page = SimpleNamespace(
            url="https://example.test/",
            get_by_role=lambda role, **kwargs: locator,
            expect_download=lambda **kwargs: _DownloadContext(download),
        )
        manager = BrowserManager()
        manager._sessions["s"] = Session(PoolKey("chromium", True), SimpleNamespace(), page, "chromium", True)
        monkeypatch.setattr(registry, "MANAGER", manager)
        monkeypatch.setattr(registry, "audit_action", lambda *a, **k: None)
        monkeypatch.setattr(registry, "SETTINGS", replace(SETTINGS, state_dir=tmp_path, browser_download_max_bytes=5))
        capture = Capture()
        registry.register(cast(Any, capture))

        result = await capture.functions["browser_download"](
            session_id="s",
            role="button",
            name="Download",
        )

        assert result["ok"] is False
        assert result["error"] == "browser_download_too_large"
        assert list((tmp_path / "browser_downloads").glob("*")) == []

    asyncio.run(run())
