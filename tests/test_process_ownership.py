from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import psutil
import pytest

from core import process_ownership, windows_job
from core.errors import ToolError
from core.process_ownership import NativeOwnershipRequiredError, spawn_owned_process


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("root_present", [False, True])
def test_posix_termination_reaps_descendants_in_separate_sessions(
    monkeypatch: pytest.MonkeyPatch, force: bool, root_present: bool,
) -> None:
    events: list[str] = []

    class Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid
            self.alive = True

        def children(self, *, recursive: bool) -> list[Process]:
            assert recursive
            events.append("snapshot")
            return [child, grandchild]

        def terminate(self) -> None:
            events.append(f"terminate:{self.pid}")
            if self is not grandchild:
                self.alive = False

        def kill(self) -> None:
            events.append(f"kill:{self.pid}")
            self.alive = False

    parent, child, grandchild = Process(100), Process(200), Process(300)
    member = parent if root_present else Process(101)
    # The child and grandchild each started a new session and are absent from group 100.
    monkeypatch.setattr(process_ownership, "_group_processes", lambda pgid: [member])

    def signal_group(pgid: int, sig: int) -> bool:
        events.append("signal_group")
        member.alive = False
        return True

    def wait_procs(processes: list[Process], *, timeout: float) -> tuple[list[Process], list[Process]]:
        return [process for process in processes if not process.alive], [process for process in processes if process.alive]

    class Popen:
        pid = parent.pid

        def wait(self, *, timeout: float) -> int:
            return 0

    monkeypatch.setattr(process_ownership, "_signal_group", signal_group)
    monkeypatch.setattr(psutil, "wait_procs", wait_procs)
    owned = process_ownership.PosixOwnedProcess(cast(subprocess.Popen[bytes], Popen()), parent.pid)
    result = owned.terminate_tree(force=force)
    assert not child.alive and not grandchild.alive
    assert events.index("snapshot") < events.index("signal_group")
    assert set(result["targeted_pids"]) == {parent.pid, member.pid, child.pid, grandchild.pid}
    assert set(result["terminated_pids"]) == {member.pid, child.pid, grandchild.pid}
    assert not result["alive_pids"]


@pytest.mark.parametrize("denied_phase", ["inspection", "kill", "terminate", "group", "unknown_class"])
def test_posix_cleanup_attempts_remaining_targets_before_reporting_denial(
    monkeypatch: pytest.MonkeyPatch, denied_phase: str,
) -> None:
    events: list[str] = []

    class CredentialCanaryError(OSError):
        pass

    class Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid
            self.alive = True

        def children(self, *, recursive: bool) -> list[Process]:
            events.append(f"inspect:{self.pid}")
            if self is first and denied_phase == "inspection":
                raise psutil.AccessDenied(pid=self.pid)
            if self is first and denied_phase == "unknown_class":
                raise CredentialCanaryError("credential-canary-error-body")
            return [child]

        def kill(self) -> None:
            events.append(f"kill:{self.pid}")
            if self is child and denied_phase == "kill":
                raise psutil.AccessDenied(pid=self.pid)
            self.alive = False

        def terminate(self) -> None:
            events.append(f"terminate:{self.pid}")
            if self is child and denied_phase == "terminate":
                raise psutil.AccessDenied(pid=self.pid)
            self.alive = False

    first, second, child = Process(100), Process(101), Process(200)
    monkeypatch.setattr(process_ownership, "_group_processes", lambda pgid: [first, second])

    def signal_group(pgid: int, sig: int) -> bool:
        events.append("signal_group")
        first.alive = second.alive = False
        return denied_phase != "group"

    def wait_procs(processes: list[Process], *, timeout: float) -> tuple[list[Process], list[Process]]:
        return [process for process in processes if not process.alive], [process for process in processes if process.alive]

    class Popen:
        pid = first.pid

        def wait(self, *, timeout: float) -> int:
            return 0

    monkeypatch.setattr(process_ownership, "_signal_group", signal_group)
    monkeypatch.setattr(psutil, "wait_procs", wait_procs)
    owned = process_ownership.PosixOwnedProcess(cast(subprocess.Popen[bytes], Popen()), first.pid)
    with pytest.raises(ToolError) as failure:
        owned.terminate_tree(force=denied_phase != "terminate")
    assert failure.value.code == "process_cleanup_incomplete"
    classification = {
        "inspection": "inspection:AccessDenied=1",
        "kill": "signal:AccessDenied=1",
        "terminate": "signal:AccessDenied=1",
        "group": "group:Unspecified=1",
        "unknown_class": "inspection:OtherError=1",
    }[denied_phase]
    assert classification in failure.value.message
    assert "CredentialCanary" not in failure.value.message and "credential-canary" not in failure.value.message
    assert "inspect:101" in events and "signal_group" in events
    assert any(event in events for event in ("kill:101", "terminate:101"))
    assert not first.alive and not second.alive


@pytest.mark.parametrize("failure", ["none", "error", "interrupt"])
def test_posix_close_preserves_failed_cleanup_without_repeating_signals(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    owned = process_ownership.PosixOwnedProcess(cast(subprocess.Popen[bytes], object()), 100)
    attempts = 0

    def cleanup(*, force: bool) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if failure == "interrupt":
            raise KeyboardInterrupt
        if failure == "error":
            raise ToolError("process_cleanup_incomplete", "Process cleanup incomplete.")
        return {}

    monkeypatch.setattr(owned, "_terminate_group", cleanup)
    for attempt in range(2):
        if failure == "interrupt" and attempt == 0:
            with pytest.raises(KeyboardInterrupt):
                owned.close_ownership()
        elif failure != "none":
            with pytest.raises(ToolError) as error:
                owned.close_ownership()
            assert error.value.code == "process_cleanup_incomplete"
        else:
            owned.close_ownership()
    assert attempts == 1


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
