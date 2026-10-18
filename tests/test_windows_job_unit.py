from __future__ import annotations

import ctypes
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, cast

import psutil
import pytest

from core import windows_job
from core.errors import ToolError


@pytest.mark.parametrize("case", ["exited", "live", "reused", "denied", "wait_failed", "close_failed"])
def test_capacity_exit_probe_uses_identity_and_signaled_handle(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    closed: list[int] = []

    def open_process(*args: object) -> int:
        if case == "denied":
            raise PermissionError("private-canary")
        return 123

    def close(handle: int) -> None:
        closed.append(handle)
        if case == "close_failed":
            raise OSError("private-canary")

    kernel = SimpleNamespace(
        OpenProcess=open_process, SYNCHRONIZE=0x100000, WAIT_OBJECT_0=0, WAIT_TIMEOUT=258,
        WaitForSingleObject=lambda handle, timeout: 258 if case == "live" else 0xFFFFFFFF if case == "wait_failed" else 0,
        CloseHandle=close,
    )
    monkeypatch.setitem(sys.modules, "_winapi", kernel)
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: 20.0 if case == "reused" else 10.0))
    assert windows_job.process_identity_exited(42, 10.0) is (
        True if case in {"exited", "reused"} else False if case == "live" else None
    )
    assert closed == ([] if case == "denied" else [123])


@pytest.mark.parametrize("root_pending", [False, True])
def test_zero_accounting_still_requires_owned_root_handle_exit(root_pending: bool) -> None:
    events: list[str] = []

    def wait(timeout: float) -> int:
        events.append("root_exit")
        assert 0 <= timeout <= 5
        if root_pending:
            raise subprocess.TimeoutExpired("owned", timeout)
        return 1

    job = SimpleNamespace(
        closed=False, terminate=lambda code: True, active_process_count=lambda: 0,
        close=lambda: events.append("close"),
    )
    owned = windows_job.OwnedProcess(cast(Any, SimpleNamespace(pid=123, wait=wait)), cast(Any, job), "windows_job")
    if root_pending:
        with pytest.raises(ToolError) as failure:
            owned.close_ownership()
        assert failure.value.code == "process_cleanup_incomplete"
    else:
        owned.close_ownership()
    assert events == ["root_exit", "close"]


def test_native_job_accounting_query_is_bounded_and_denials_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    def query(handle: object, kind: object, info: Any, size: object, returned: object) -> bool:
        assert size == 48
        ctypes.cast(info, ctypes.POINTER(windows_job._JOBOBJECT_BASIC_ACCOUNTING_INFORMATION)).contents.ActiveProcesses = 2
        return True

    kernel = SimpleNamespace(QueryInformationJobObject=query, CloseHandle=lambda handle: True)
    monkeypatch.setattr(windows_job, "_kernel32", lambda: kernel)
    job = windows_job.WindowsJob(123)
    assert job.active_process_count() == 2
    kernel.QueryInformationJobObject = lambda *args: False
    with pytest.raises(ToolError):
        job.active_process_count()
    job.close()
    assert job.active_process_count() == 0


def test_job_close_failure_keeps_handle_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(windows_job, "_kernel32", lambda: SimpleNamespace(CloseHandle=lambda handle: False))
    job = windows_job.WindowsJob(123)
    with pytest.raises(ToolError) as raised:
        job.close()
    assert raised.value.code == "process_cleanup_incomplete"
    assert not job.closed


@pytest.mark.parametrize("failure", ["query", "active", "terminate"])
def test_native_cleanup_requires_positive_job_quiescence_and_keeps_failure(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    events: list[str] = []

    def active_count() -> int:
        events.append("query")
        if failure == "query":
            raise PermissionError("credential-canary")
        return 1

    def close() -> None:
        events.append("close")
        job.closed = True

    job = SimpleNamespace(
        closed=False, terminate=lambda code: failure != "terminate", active_process_count=active_count, close=close,
    )
    ticks = iter([0.0, 10.0, 20.0])
    monkeypatch.setattr(windows_job, "time", SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda delay: None), raising=False)
    owned = windows_job.OwnedProcess(cast(Any, SimpleNamespace(pid=123)), cast(Any, job), "windows_job")
    for _ in range(2):
        with pytest.raises(ToolError) as raised:
            owned.close_ownership()
        assert raised.value.code == "process_cleanup_incomplete"
        assert "credential-canary" not in raised.value.message
    assert events.count("close") == 1


def test_job_cleanup_never_kills_a_process_observed_outside_native_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[int] = []
    reused = SimpleNamespace(pid=123, children=lambda **kwargs: [], kill=lambda: killed.append(123))
    job = SimpleNamespace(closed=False, terminate=lambda code: True)
    owned = windows_job.OwnedProcess(cast(Any, SimpleNamespace(pid=123, wait=lambda timeout: 0)), cast(Any, job), "windows_job")
    monkeypatch.setattr(windows_job.psutil, "Process", lambda pid: reused)
    monkeypatch.setattr(windows_job.psutil, "wait_procs", lambda processes, timeout: ([], [reused]))

    result = owned.terminate_tree()

    assert killed == []
    assert result["alive_pids"] == [123]


@pytest.mark.parametrize("error", [psutil.AccessDenied(123), psutil.ZombieProcess(123)])
def test_job_termination_survives_process_inspection_failure(
    monkeypatch: pytest.MonkeyPatch, error: psutil.Error,
) -> None:
    terminated: list[int] = []

    def terminate(code: int) -> bool:
        terminated.append(code)
        return True

    job = SimpleNamespace(closed=False, terminate=terminate)
    process = SimpleNamespace(pid=123, wait=lambda timeout: 0)
    owned = windows_job.OwnedProcess(cast(Any, process), cast(Any, job), "windows_job")

    def unavailable(pid: int) -> psutil.Process:
        raise error

    monkeypatch.setattr(windows_job.psutil, "Process", unavailable)
    result = owned.terminate_tree()

    assert terminated == [1]
    assert result["backend"] == "windows_job"
