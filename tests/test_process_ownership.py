from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from core import process_ownership, windows_job
from core.process_ownership import NativeOwnershipRequiredError, spawn_owned_process


def test_require_native_parameter_defaults_to_false() -> None:
    import inspect

    sig_common = inspect.signature(process_ownership.spawn_owned_process)
    assert sig_common.parameters["require_native"].default is False

    sig_win = inspect.signature(windows_job.spawn_owned_process)
    assert sig_win.parameters["require_native"].default is False


def test_native_ownership_required_error_exported() -> None:
    assert issubclass(NativeOwnershipRequiredError, RuntimeError)
    assert process_ownership.NativeOwnershipRequiredError is windows_job.NativeOwnershipRequiredError


def test_posix_groups_disabled_raises_native_ownership_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(process_ownership, "posix_process_groups_enabled", lambda: False)

    with pytest.raises(NativeOwnershipRequiredError, match="POSIX process groups are disabled"):
        spawn_owned_process(
            [sys.executable, "-c", "print('ok')"],
            cwd=tmp_path,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0,
            require_native=True,
        )


def test_posix_groups_disabled_with_default_require_native_preserves_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(process_ownership, "posix_process_groups_enabled", lambda: False)

    called: list[str] = []

    def mock_regular_popen(*args: Any, **kwargs: Any) -> Any:
        called.append(kwargs.get("backend", ""))
        return "fallback_process"

    monkeypatch.setattr(process_ownership, "_regular_popen", mock_regular_popen)

    res = spawn_owned_process(
        [sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0,
    )
    assert res == "fallback_process"
    assert called == ["psutil"]


def test_posix_spawn_failure_with_require_native_raises_native_ownership_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(process_ownership, "posix_process_groups_enabled", lambda: True)

    def failing_popen(*args: Any, **kwargs: Any) -> Any:
        raise OSError("Simulated POSIX session spawn failure")

    monkeypatch.setattr(subprocess, "Popen", failing_popen)

    with pytest.raises(NativeOwnershipRequiredError, match="Failed to spawn POSIX process in a new session"):
        spawn_owned_process(
            [sys.executable, "-c", "print('ok')"],
            cwd=tmp_path,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0,
            require_native=True,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object tests require Windows")
def test_windows_job_disabled_raises_native_ownership_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_WINDOWS_JOB_OBJECTS", "0")

    with pytest.raises(NativeOwnershipRequiredError, match="Windows Job Objects are disabled"):
        spawn_owned_process(
            [sys.executable, "-c", "print('ok')"],
            cwd=tmp_path,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0,
            require_native=True,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object tests require Windows")
def test_windows_job_create_failure_raises_native_ownership_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_WINDOWS_JOB_OBJECTS", "1")

    def failing_create() -> Any:
        raise OSError("Simulated Job Object creation failure")

    monkeypatch.setattr(windows_job.WindowsJob, "create", staticmethod(failing_create))

    marker = tmp_path / "never_run.txt"
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"

    with pytest.raises(NativeOwnershipRequiredError, match="Failed to create Windows Job Object"):
        spawn_owned_process(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0,
            require_native=True,
        )

    assert not marker.exists(), "Code must not execute if Job Object creation fails under require_native=True"


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object tests require Windows")
def test_windows_job_assignment_failure_reaps_suspended_process_and_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_WINDOWS_JOB_OBJECTS", "1")

    def failing_assign(self: Any, process_handle: int) -> None:
        raise OSError(f"Simulated assignment failure for handle {process_handle}")

    monkeypatch.setattr(windows_job.WindowsJob, "assign_process_handle", failing_assign)

    marker = tmp_path / "assignment_marker.txt"
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"

    with pytest.raises(NativeOwnershipRequiredError, match="Failed to assign or resume process in Windows Job Object"):
        spawn_owned_process(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0,
            require_native=True,
        )

    assert not marker.exists(), "Suspended process must be reaped and fallback must NOT run under require_native=True"


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object tests require Windows")
def test_windows_job_successful_native_spawn(tmp_path: Path) -> None:
    marker = tmp_path / "success_marker.txt"
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('success')"

    owned = spawn_owned_process(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0,
        require_native=True,
    )

    try:
        assert owned.ownership_backend == "windows_job"
        owned.wait(timeout=5)
        assert marker.exists()
        assert marker.read_text() == "success"
    finally:
        owned.close_ownership()
