"""Provider-specific Windows handle-budget regressions; no external LSP required."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from core.errors import ToolError
from core.process_ownership import OwnedProcess
from tools.language import sessions
from tools.language.providers import LspProvider, SessionKey


@pytest.mark.parametrize("sample_platform", ["nt", "posix"])
@pytest.mark.parametrize(
    ("provider_id", "handles", "accepted"),
    [
        ("pyright", 511, True),
        ("pyright", 600, False),
        ("phpactor", 600, False),
        ("typescript", 600, True),
        ("typescript", 1024, True),
        ("typescript", 1025, False),
    ],
)
def test_real_provider_handle_budget_is_strictly_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider_id: str,
    handles: int, accepted: bool, sample_platform: str,
) -> None:
    class ProcessProbe:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def children(self, *, recursive: bool) -> list[ProcessProbe]:
            assert recursive
            return []

        def create_time(self) -> float:
            return 100.0

        def memory_info(self) -> Any:
            return SimpleNamespace(rss=16 * 1024 * 1024)

        def cpu_times(self) -> Any:
            return SimpleNamespace(user=1.0, system=0.0)

        def num_handles(self) -> int:
            return handles

        def num_fds(self) -> int:
            # POSIX psutil exposes file descriptors rather than Windows handles.
            return handles

    monkeypatch.setattr(
        sessions, "psutil",
        SimpleNamespace(Process=ProcessProbe, Error=sessions.psutil.Error),
    )
    monkeypatch.setattr(sessions, "os", SimpleNamespace(name=sample_platform))
    language = {"pyright": "python", "typescript": "typescript", "phpactor": "php"}[provider_id]
    provider = LspProvider(
        provider_id=cast(Any, provider_id),
        languages=(language,),
        executable=tmp_path / "unused-binary",
        executable_sha256="0" * 64,
        entrypoint=None,
        entrypoint_sha256=None,
        declared_version="test",
        trusted_roots=(tmp_path,),
    )
    manager = sessions.LspSessionManager((provider,), generation="test")
    key = SessionKey(str(tmp_path), "no-git", language, provider_id, "config", "test")
    session = sessions._Session(
        key, provider,
        process=cast(OwnedProcess, SimpleNamespace(pid=1, poll=lambda: None)),
    )
    manager._sessions[key] = session
    if accepted:
        manager._sample(session)
        assert session.rss_bytes == 16 * 1024 * 1024
        assert manager.snapshot()["failed"] == 0
    else:
        with pytest.raises(ToolError) as raised:
            manager._sample(session)
        assert raised.value.code == "lsp_resource_exceeded"


@pytest.mark.parametrize(
    ("package_present", "entrypoint_name", "expected"),
    [
        (False, "typescript-language-server", False),
        (True, "typescript-language-server", True),
        (True, "unrelated-server", False),
    ],
)
def test_tsserver_resolution_requires_colocated_trusted_provider(
    tmp_path: Path, package_present: bool, entrypoint_name: str, expected: bool,
) -> None:
    node_modules = tmp_path / "tools" / "node_modules"
    entrypoint = node_modules / entrypoint_name / "lib" / "cli.mjs"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("// pinned test entrypoint", encoding="utf-8")
    tsserver = node_modules / "typescript" / "lib" / "tsserver.js"
    if package_present:
        tsserver.parent.mkdir(parents=True)
        tsserver.write_text("// trusted dependency", encoding="utf-8")

    # A workspace-controlled package cannot supply an executable TS dependency.
    workspace = tmp_path / "untrusted-workspace"
    attacker_tsserver = workspace / "node_modules" / "typescript" / "lib" / "tsserver.js"
    attacker_tsserver.parent.mkdir(parents=True)
    attacker_tsserver.write_text("process.exit(99)", encoding="utf-8")

    provider = LspProvider(
        provider_id="typescript",
        languages=("typescript",),
        executable=tmp_path / "node.exe",
        executable_sha256="0" * 64,
        entrypoint=entrypoint,
        entrypoint_sha256="0" * 64,
        declared_version="test",
        trusted_roots=(workspace,),
    )
    result = sessions._trusted_typescript_tsserver(provider)
    assert (result is not None) is expected
    if expected:
        assert result == tsserver.resolve()
    assert result != attacker_tsserver.resolve()
