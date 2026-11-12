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


@pytest.mark.parametrize(
    "case", ["exited", "live", "reused", "denied", "wait_failed", "close_failed", "gone_before_handle", "denied_metadata"],
)
def test_capacity_exit_probe_uses_identity_and_signaled_handle(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    closed: list[int] = []

    def open_process(*args: object) -> int:
        if case in {"denied", "gone_before_handle", "denied_metadata"}:
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
    monkeypatch.setattr(windows_job, "sys", SimpleNamespace(platform="win32"))
    def metadata(pid: int) -> SimpleNamespace:
        if case == "gone_before_handle":
            raise psutil.NoSuchProcess(pid)
        if case == "denied_metadata":
            raise psutil.AccessDenied(pid)
        return SimpleNamespace(create_time=lambda: 20.0 if case == "reused" else 10.0)

    monkeypatch.setattr(psutil, "Process", metadata)
    assert windows_job.process_identity_exited(42, 10.0) is (
        True if case in {"exited", "reused", "gone_before_handle"} else False if case == "live" else None
    )
    assert closed == ([] if case in {"denied", "gone_before_handle", "denied_metadata"} else [123])


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


def test_concurrent_ownership_close_runs_native_termination_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    first_terminating = Event()
    allow_first_to_finish = Event()
    second_entered_cleanup = Event()
    calls: list[str] = []
    calls_lock = Lock()

    def note(action: str) -> None:
        with calls_lock:
            calls.append(action)
            if action == "capture" and calls.count("capture") == 2:
                second_entered_cleanup.set()

    def terminate(code: int) -> bool:
        note("terminate")
        first_terminating.set()
        assert allow_first_to_finish.wait(3.0)
        return True

    class Job:
        closed = False

        def terminate(self, code: int) -> bool:
            return terminate(code)

        def active_process_count(self) -> int:
            return 0

        def close(self) -> None:
            note("close")
            self.closed = True

    job = Job()
    owned = windows_job.OwnedProcess(
        cast(Any, SimpleNamespace(pid=123, wait=lambda timeout: 0)), cast(Any, job), "windows_job",
    )

    def capture(**kwargs: object) -> list[tuple[int, float]]:
        note("capture")
        return []

    monkeypatch.setattr(owned, "_capture_tree_identities", capture)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(owned.close_ownership)
            assert first_terminating.wait(3.0)
            second = pool.submit(owned.close_ownership)
            assert not second_entered_cleanup.wait(0.15), "Two threads entered the same native cleanup"
            allow_first_to_finish.set()
            first.result(timeout=3.0)
            second.result(timeout=3.0)
    finally:
        allow_first_to_finish.set()
    assert calls.count("capture") == 1
    assert calls.count("terminate") == 1
    assert calls.count("close") == 1


def test_late_termination_after_native_close_never_targets_a_bare_reused_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import executor

    job = SimpleNamespace(
        closed=False, terminate=lambda code: True, active_process_count=lambda: 0,
    )
    job.close = lambda: setattr(job, "closed", True)
    owned = windows_job.OwnedProcess(
        cast(Any, SimpleNamespace(pid=123, wait=lambda timeout: 0)), cast(Any, job), "windows_job",
    )
    monkeypatch.setattr(owned, "_capture_tree_identities", lambda **kwargs: [])
    owned.close_ownership()
    assert owned.job is None

    signaled: list[int] = []

    def unsafe_fallback(pid: int, **kwargs: Any) -> dict[str, Any]:
        signaled.append(pid)
        return {"targeted_pids": [pid], "terminated_pids": [pid], "alive_pids": []}

    monkeypatch.setattr(executor, "terminate_process_tree", unsafe_fallback)
    result = owned.terminate_tree()

    assert signaled == [], "A closed native owner must never signal a possibly reused PID"
    assert result["targeted_pids"] == []
    assert result["alive_pids"] == []


@pytest.mark.parametrize("identity_available", [True, False])
def test_psutil_fallback_termination_never_signals_an_unverified_pid(
    monkeypatch: pytest.MonkeyPatch, identity_available: bool,
) -> None:
    from core import executor

    owned = windows_job.OwnedProcess(
        cast(Any, SimpleNamespace(pid=123, poll=lambda: None)),
        None,
        "psutil_fallback",
    )
    owned._root_created = 10.0 if identity_available else None
    calls: list[tuple[int, float | None]] = []

    def terminate(pid: int, *, force: bool, expected_created: float | None = None) -> dict[str, Any]:
        calls.append((pid, expected_created))
        return {"targeted_pids": [], "terminated_pids": [], "alive_pids": []}

    monkeypatch.setattr(executor, "terminate_process_tree", terminate)
    if identity_available:
        owned.terminate_tree()
        assert calls == [(123, 10.0)]
    else:
        with pytest.raises(ToolError) as failure:
            owned.terminate_tree()
        assert failure.value.code == "process_identity_unavailable"
        assert calls == []


def test_native_termination_and_close_do_not_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    in_terminate = Event()
    resume_terminate = Event()
    close_entered = Event()
    calls: list[str] = []
    lock = Lock()

    def terminate(code: int) -> bool:
        with lock:
            calls.append("terminate")
        in_terminate.set()
        assert resume_terminate.wait(3.0)
        return True

    job = SimpleNamespace(closed=False, terminate=terminate, active_process_count=lambda: 0)
    job.close = lambda: setattr(job, "closed", True)
    owned = windows_job.OwnedProcess(
        cast(Any, SimpleNamespace(pid=123, wait=lambda timeout: 0)), cast(Any, job), "windows_job",
    )

    def capture(**kwargs: Any) -> list[tuple[int, float]]:
        with lock:
            calls.append("capture")
            if calls.count("capture") > 1:
                close_entered.set()
        return []

    monkeypatch.setattr(owned, "_capture_tree_identities", capture)
    monkeypatch.setattr(windows_job.psutil, "Process", lambda pid: (_ for _ in ()).throw(psutil.NoSuchProcess(pid)))
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(owned.terminate_tree)
            assert in_terminate.wait(3.0)
            second = pool.submit(owned.close_ownership)
            assert not close_entered.wait(0.15), "Native termination and close overlapped"
            resume_terminate.set()
            first.result(timeout=3.0)
            second.result(timeout=3.0)
    finally:
        resume_terminate.set()


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


def test_reap_identities_does_not_import_the_runtime_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[int] = []
    process = SimpleNamespace(
        pid=123, create_time=lambda: 10.0, children=lambda recursive: [], kill=lambda: killed.append(123),
    )
    monkeypatch.setattr(windows_job.psutil, "Process", lambda pid: process)
    states = iter([True, False])
    monkeypatch.setattr(
        windows_job.OwnedProcess, "_identity_live", lambda self, pid, created: next(states),
    )
    # A bootstrap interpreter may install pywin32 after startup, so importing
    # core.executor (and therefore mcp/pywintypes) during cleanup is unsafe.
    monkeypatch.setitem(sys.modules, "core.executor", None)
    owned = windows_job.OwnedProcess(cast(Any, SimpleNamespace(pid=123)), None, "windows_job")

    terminated, alive = owned._reap_identities([(123, 10.0)])

    assert killed == [123]
    assert terminated == [123]
    assert alive == []


@pytest.mark.parametrize("eventually_exits", [True, False])
def test_job_termination_rechecks_a_captured_child_before_reporting_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, eventually_exits: bool,
) -> None:
    job = SimpleNamespace(closed=False, terminate=lambda code: True, active_process_count=lambda: 0)
    process = SimpleNamespace(pid=223, wait=lambda timeout: 0)
    owned = windows_job.OwnedProcess(cast(Any, process), cast(Any, job), "windows_job")
    monkeypatch.setattr(owned, "_capture_tree_identities", lambda **kwargs: [(223, 10.0)])
    checks: list[list[tuple[int, float]]] = []

    def reap(identities: list[tuple[int, float]]) -> tuple[list[int], list[int]]:
        checks.append(identities)
        if len(checks) == 1:
            return [], [223]
        return ([223], []) if eventually_exits else ([], [223])

    monkeypatch.setattr(owned, "_reap_identities", reap)
    result = owned.terminate_tree()

    assert checks == [[(223, 10.0)], [(223, 10.0)]]
    assert result["alive_pids"] == ([] if eventually_exits else [223])
    assert result["job_terminated"] is True
    assert result["job_quiescent"] is True


def test_reap_identities_uses_one_shared_wait_budget_for_many_stubborn_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    monkeypatch.setattr(
        windows_job, "time",
        SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda duration: clock.__setitem__(0, clock[0] + duration)),
    )
    owned = windows_job.OwnedProcess(cast(Any, SimpleNamespace(pid=900)), None, "windows_job")
    monkeypatch.setattr(owned, "_identity_live", lambda pid, created: True)
    signals: list[int] = []
    monkeypatch.setattr(windows_job, "_terminate_identity_tree", lambda pid, created: signals.append(pid))
    identities = [(pid, 10.0) for pid in range(901, 911)]

    terminated, alive = owned._reap_identities(identities)

    assert signals == list(range(901, 911))
    assert terminated == []
    assert alive == list(range(901, 911))
    assert clock[0] <= 3.2, "Cleanup must not spend a full timeout on every stubborn descendant"


def test_identity_tree_termination_skips_a_reused_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[int] = []
    process = SimpleNamespace(
        pid=123, create_time=lambda: 20.0, children=lambda recursive: [], kill=lambda: killed.append(123),
    )
    monkeypatch.setattr(windows_job.psutil, "Process", lambda pid: process)

    windows_job._terminate_identity_tree(123, 10.0)

    assert killed == []


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


@pytest.mark.parametrize(
    ("identity_exited", "expected_alive"),
    [(True, []), (False, [123])],
)
def test_unsettled_job_diagnostics_check_signaled_process_identity(
    monkeypatch: pytest.MonkeyPatch, identity_exited: bool, expected_alive: list[int],
) -> None:
    # Windows may retain a visible PID and report is_running=True even after
    # its process handle is signaled. Job accounting can lag that transition.
    counter = [0.0]
    monkeypatch.setattr(
        windows_job, "time",
        SimpleNamespace(monotonic=lambda: counter.__setitem__(0, counter[0] + 10.0) or counter[0],
                        sleep=lambda delay: None),
    )
    job = SimpleNamespace(closed=False, terminate=lambda code: True, active_process_count=lambda: 1)
    owned = windows_job.OwnedProcess(cast(Any, SimpleNamespace(pid=123, wait=lambda timeout: 0)),
                                    cast(Any, job), "windows_job")
    monkeypatch.setattr(owned, "_capture_tree_identities", lambda **kwargs: [(123, 10.0)])
    process = SimpleNamespace(
        pid=123, children=lambda recursive: [], create_time=lambda: 10.0,
        is_running=lambda: True, status=lambda: psutil.STATUS_RUNNING,
    )
    monkeypatch.setattr(windows_job.psutil, "Process", lambda pid: process)
    monkeypatch.setattr(windows_job, "process_identity_exited", lambda pid, created: identity_exited)
    monkeypatch.setattr(owned, "_reap_identities", lambda identities: ([123], []))

    result = owned.terminate_tree()

    assert result["job_quiescent"] is False
    assert result["alive_pids"] == expected_alive


def test_ppid_scan_uses_fast_snapshot_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(windows_job, "_snapshot_process_ppids", lambda: [(125, 123)])

    def process(pid: int) -> SimpleNamespace:
        return SimpleNamespace(
            pid=pid,
            create_time=lambda: 10.0 if pid == 123 else 12.0,
            children=lambda recursive=False: [],
        )

    monkeypatch.setattr(windows_job.psutil, "Process", process)
    owned = windows_job.OwnedProcess(cast(Any, SimpleNamespace(pid=123)), None, "windows_job")
    owned._root_created = 10.0
    owned._known_identities = {123: 10.0}

    identities = owned._capture_tree_identities(include_ppid_scan=True)

    assert (125, 12.0) in identities
