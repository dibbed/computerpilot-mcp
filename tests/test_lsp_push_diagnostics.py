"""Bounded push diagnostic adaptation; no external provider required."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_lsp_sessions import manager_provider, protocol_transport

from core.errors import ToolError
from tools.language.transport import _frame

URI = "file:///C:/fixture/main.ts"


def publication(uri: str = URI, *, version: int | None = None, items: list[Any] | None = None) -> bytes:
    params: dict[str, Any] = {"uri": uri, "diagnostics": items or []}
    if version is not None:
        params["version"] = version
    return _frame({"method": "textDocument/publishDiagnostics", "params": params})


def receive(channel: Any, uri: str = URI) -> dict[str, Any]:
    return channel.receive_diagnostics(uri, deadline=time.monotonic() + 0.25, cancel=threading.Event())


def test_push_diagnostics_return_only_actual_matching_publication() -> None:
    channel, process = protocol_transport(b"")
    try:
        channel.begin_request()
        channel.track_document(URI, 2)
        process.ready.set()
        process.stdout.feed(publication("file:///C:/outside.ts") + publication(version=1) + publication(version=2))
        assert receive(channel) == {"kind": "full", "items": []}
        assert len(channel._diagnostics) == 1
        assert channel.notification_counts["textDocument/publishDiagnostics"] == 3
    finally:
        assert channel.close()


def test_push_diagnostics_versionless_windows_uri_and_private_data() -> None:
    from tools.language import transport

    wire_uri = "file:///c%3A/fixture/main.ts" if transport.os.name == "nt" else URI
    channel, process = protocol_transport(b"")
    try:
        channel.begin_request()
        channel.track_document(URI, 1)
        process.ready.set()
        process.stdout.feed(publication(wire_uri, items=[{"data": {"token": "private-canary", "value": 1}}]))
        assert receive(channel) == {"kind": "full", "items": [{"data": {"token": "<redacted>", "value": 1}}]}
    finally:
        assert channel.close()


def test_document_sync_invalidates_retained_and_already_queued_publications() -> None:
    channel, process = protocol_transport(b"")
    try:
        channel.begin_request()
        channel.track_document(URI, 1)
        process.ready.set()
        process.stdout.feed(publication())
        assert receive(channel)["items"] == []
        process.stdout.feed(publication(items=[{"message": "old queued"}]))
        with channel._condition:
            assert channel._condition.wait_for(lambda: bool(channel._messages), timeout=1)
        channel.track_document(URI, 2)
        assert not channel._diagnostics and not channel._messages
        with pytest.raises(ToolError) as absent:
            receive(channel)
        assert absent.value.code == "lsp_timeout"
        process.stdout.feed(publication(version=2, items=[{"message": "new"}]))
        assert receive(channel)["items"] == [{"message": "new"}]
        channel.track_document(URI, None)
        assert not channel._diagnostics and not channel._diagnostic_versions
    finally:
        assert channel.close()


def test_absent_push_diagnostics_never_fabricates_clean_success() -> None:
    channel, process = protocol_transport(b"")
    try:
        channel.begin_request()
        channel.track_document(URI, 1)
        process.ready.set()
        with pytest.raises(ToolError) as absent:
            receive(channel)
        assert absent.value.code == "lsp_timeout"
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(ToolError) as cancelled:
            channel.receive_diagnostics(URI, deadline=time.monotonic() + 1, cancel=cancel)
        assert cancelled.value.code == "lsp_cancelled"
    finally:
        assert channel.close()


@pytest.mark.parametrize("failure", ["cancel", "deadline", "reader_failure"])
def test_cached_publication_still_obeys_request_failure(failure: str) -> None:
    channel, process = protocol_transport(b"")
    try:
        channel.begin_request()
        channel.track_document(URI, 1)
        process.ready.set()
        process.stdout.feed(publication())
        assert receive(channel)["items"] == []
        cancel = threading.Event()
        if failure == "cancel":
            cancel.set()
        if failure == "reader_failure":
            channel._fail(ToolError("lsp_protocol_error", "test-only reader failure"))
        with pytest.raises(ToolError) as rejected:
            channel.receive_diagnostics(
                URI, deadline=time.monotonic() + (-1 if failure == "deadline" else 1), cancel=cancel,
            )
        assert rejected.value.code == {
            "cancel": "lsp_cancelled", "deadline": "lsp_timeout", "reader_failure": "lsp_protocol_error",
        }[failure]
    finally:
        assert channel.close()


def test_reopen_only_document_sync_preserves_diagnostic_tracking(tmp_path: Path) -> None:
    from tools.language import sessions

    provider, _log = manager_provider(tmp_path, sync_kind=0)
    target = tmp_path / "main.py"
    target.write_text("first = 1\n", encoding="utf-8")
    channel, _process = protocol_transport(b"")
    manager = sessions.LspSessionManager((provider,), generation="reopen")
    session = sessions._Session(sessions.make_session_key(tmp_path, "python", provider, "reopen"), provider)
    session.transport = channel
    session.capabilities = {"textDocumentSync": {"openClose": True, "change": 0}}
    try:
        manager._prepare_document(session, target.as_uri(), time.monotonic() + 2)
        target.write_text("second = 2\n", encoding="utf-8")
        document = manager._prepare_document(session, target.as_uri(), time.monotonic() + 2)
        assert document.version == 2
        assert channel._diagnostic_versions[sessions.os.path.normcase(target.as_uri())] == 2
    finally:
        assert channel.close()


def test_push_diagnostic_retention_and_document_admission_are_bounded() -> None:
    channel, process = protocol_transport(b"", max_message_bytes=500, max_request_bytes=8000)
    try:
        process.ready.set()
        for index in range(16):
            channel.begin_request()
            uri = URI + str(index)
            channel.track_document(uri, 1)
            process.stdout.feed(publication(uri, items=[{"message": "x" * 200}]))
            assert receive(channel, uri)["items"]
            assert sum(size for _report, size in channel._diagnostics.values()) <= 500
        with pytest.raises(ToolError):
            channel.track_document(URI + "overflow", 1)
    finally:
        assert channel.close()


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_intelephense_cache_owned_lifecycle_and_isolated_licence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup_failure: bool,
) -> None:
    from tools.language import sessions

    provider, log = manager_provider(tmp_path)
    assert provider.entrypoint is not None
    source = provider.entrypoint.read_text(encoding="utf-8").replace(
        "            initialize_seen = True",
        '            initialize_seen = True\n            _append_log(log_path, "init:" + json.dumps(message["params"]))',
    )
    provider.entrypoint.write_text(source, encoding="utf-8")
    provider = replace(
        provider, provider_id="intelephense", languages=("php",),
        entrypoint_sha256=hashlib.sha256(provider.entrypoint.read_bytes()).hexdigest(),
    )
    state = tmp_path / "isolated-state"
    monkeypatch.setattr(sessions, "SETTINGS", replace(sessions.SETTINGS, state_dir=state))
    manager = sessions.LspSessionManager((provider,), generation="cache-lifecycle")
    real_remove = sessions.shutil.rmtree
    if cleanup_failure:
        def denied_remove(path: Path) -> None:
            raise OSError("test-only deletion refusal")
        monkeypatch.setattr(sessions.shutil, "rmtree", denied_remove)

    async def scenario() -> Path:
        import json

        try:
            await manager.arequest(tmp_path, "php", "workspace/symbol", {"query": "x"}, timeout_sec=5)
            session = next(iter(manager._sessions.values()))
            cache = session.cache_directory
            assert cache is not None and cache.is_dir()
            assert cache.parent == state / "lsp"
            init = next(json.loads(row[5:]) for row in log.read_text(encoding="utf-8").splitlines() if row.startswith("init:"))
            assert init["initializationOptions"] == {
                "storagePath": str(cache), "globalStoragePath": str(cache),
                "licenceKey": str(cache / "licence-disabled.txt"),
            }
            return cache
        finally:
            await asyncio.to_thread(manager.close_all)

    cache = asyncio.run(scenario())
    assert manager.snapshot()["quarantined"] == int(cleanup_failure)
    assert cache.exists() is cleanup_failure
    if cleanup_failure:
        real_remove(cache)


@pytest.mark.parametrize("quota", ["bytes", "entries", "deadline"])
def test_intelephense_cache_scan_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quota: str) -> None:
    from tools.language import sessions

    (tmp_path / "one").write_bytes(b"12")
    (tmp_path / "two").write_bytes(b"34")
    if quota == "bytes":
        monkeypatch.setattr(sessions, "_MAX_CACHE_BYTES", 3)
    if quota == "entries":
        monkeypatch.setattr(sessions, "_MAX_CACHE_ENTRIES", 1)
    deadline = time.monotonic() + (1 if quota != "deadline" else -1)
    with pytest.raises(ToolError) as over_quota:
        sessions._check_cache(tmp_path, deadline)
    assert over_quota.value.code == "lsp_resource_exceeded"


def test_empty_intelephense_cache_still_obeys_scan_deadline(tmp_path: Path) -> None:
    from tools.language import sessions

    with pytest.raises(ToolError) as expired:
        sessions._check_cache(tmp_path, time.monotonic() - 1)
    assert expired.value.code == "lsp_resource_exceeded"


@pytest.mark.parametrize("failure", ["quota", "launch"])
def test_intelephense_cache_admission_and_launch_failure_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    from tools.language import sessions

    provider, _log = manager_provider(tmp_path)
    provider = replace(provider, provider_id="intelephense", languages=("php",))
    state = tmp_path / "isolated-state"
    monkeypatch.setattr(sessions, "SETTINGS", replace(sessions.SETTINGS, state_dir=state))
    storage = state / "lsp"
    storage.mkdir(parents=True)
    if failure == "quota":
        (storage / "preserved-old-cache").mkdir()
        monkeypatch.setattr(sessions, "_MAX_CACHE_DIRECTORIES", 1)
    launched = []

    def denied_spawn(*args: Any) -> Any:
        launched.append(args)
        raise ToolError("lsp_launch_failed", "test-only launch failure")

    monkeypatch.setattr(sessions, "open_transport", denied_spawn)
    manager = sessions.LspSessionManager((provider,), generation="denied-cache")
    with pytest.raises(ToolError):
        asyncio.run(manager.arequest(tmp_path, "php", "workspace/symbol", {"query": "x"}, timeout_sec=2))
    manager.close_all()
    assert len(launched) == int(failure == "launch")
    assert sorted(entry.name for entry in storage.iterdir()) == (
        [".admission.lock", "preserved-old-cache"] if failure == "quota" else [".admission.lock"]
    )
