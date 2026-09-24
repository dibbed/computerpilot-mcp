"""Windows Job Object process-tree ownership with suspended assign-before-run spawning."""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
from collections.abc import Sequence
from ctypes import wintypes
from pathlib import Path
from typing import IO, Any

import psutil

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_TH32CS_SNAPTHREAD = 0x00000004
_THREAD_SUSPEND_RESUME = 0x0002
_RESUME_FAILED = 0xFFFFFFFF
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_CREATE_SUSPENDED = 0x00000004

_INT64 = ctypes.c_int64
_SIZE_T = ctypes.c_size_t
_DWORD = wintypes.DWORD


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", _INT64),
        ("PerJobUserTimeLimit", _INT64),
        ("LimitFlags", _DWORD),
        ("MinimumWorkingSetSize", _SIZE_T),
        ("MaximumWorkingSetSize", _SIZE_T),
        ("ActiveProcessLimit", _DWORD),
        ("Affinity", _SIZE_T),
        ("PriorityClass", _DWORD),
        ("SchedulingClass", _DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", _SIZE_T),
        ("JobMemoryLimit", _SIZE_T),
        ("PeakProcessMemoryUsed", _SIZE_T),
        ("PeakJobMemoryUsed", _SIZE_T),
    ]


class _THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", _DWORD),
        ("cntUsage", _DWORD),
        ("th32ThreadID", _DWORD),
        ("th32OwnerProcessID", _DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", _DWORD),
    ]


_KERNEL32: Any | None = None
_KERNEL32_LOCK = threading.Lock()


def _kernel32() -> Any:
    global _KERNEL32
    if os.name != "nt":
        raise OSError("Windows Job Objects are only available on Windows.")
    if _KERNEL32 is not None:
        return _KERNEL32
    with _KERNEL32_LOCK:
        if _KERNEL32 is not None:
            return _KERNEL32
        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            raise OSError("ctypes.WinDLL is unavailable on this Windows runtime.")
        kernel32 = win_dll("kernel32", use_last_error=True)
        specs: tuple[tuple[str, list[Any], Any], ...] = (
            ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            ("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, _DWORD], wintypes.BOOL),
            ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            ("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
            ("CreateToolhelp32Snapshot", [_DWORD, _DWORD], wintypes.HANDLE),
            ("Thread32First", [wintypes.HANDLE, ctypes.POINTER(_THREADENTRY32)], wintypes.BOOL),
            ("Thread32Next", [wintypes.HANDLE, ctypes.POINTER(_THREADENTRY32)], wintypes.BOOL),
            ("OpenThread", [_DWORD, wintypes.BOOL, _DWORD], wintypes.HANDLE),
            ("ResumeThread", [wintypes.HANDLE], _DWORD),
            ("IsProcessInJob", [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
            ("GetCurrentProcess", [], wintypes.HANDLE),
        )
        for name, argtypes, restype in specs:
            function = getattr(kernel32, name)
            function.argtypes = argtypes
            function.restype = restype
        _KERNEL32 = kernel32
        return kernel32


def _last_error() -> int:
    getter = getattr(ctypes, "get_last_error", None)
    return int(getter()) if callable(getter) else 0


def windows_job_objects_enabled() -> bool:
    if os.name != "nt":
        return False
    return os.getenv("MCP_WINDOWS_JOB_OBJECTS", "1").strip().casefold() not in {"0", "false", "no", "off"}


def current_process_in_job() -> bool | None:
    if os.name != "nt":
        return None
    try:
        kernel32 = _kernel32()
        result = wintypes.BOOL()
        if not kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, ctypes.byref(result)):
            return None
        return bool(result.value)
    except OSError:
        return None


class WindowsJob:
    """One non-inheritable kill-on-close Job Object handle."""

    def __init__(self, handle: int) -> None:
        self._handle: int | None = handle
        self._lock = threading.Lock()

    @classmethod
    def create(cls) -> WindowsJob:
        kernel32 = _kernel32()
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(f"CreateJobObjectW failed: {_last_error()}")
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = _last_error()
            kernel32.CloseHandle(handle)
            raise OSError(f"SetInformationJobObject failed: {error}")
        return cls(int(handle))

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._handle is None

    def assign_process_handle(self, process_handle: int) -> None:
        with self._lock:
            handle = self._handle
            if handle is None:
                raise OSError("Job Object handle is closed.")
            setter = getattr(ctypes, "set_last_error", None)
            if callable(setter):
                setter(0)
            if not _kernel32().AssignProcessToJobObject(handle, process_handle):
                raise OSError(f"AssignProcessToJobObject failed: {_last_error()}")

    def terminate(self, exit_code: int = 1) -> bool:
        with self._lock:
            handle = self._handle
            if handle is None:
                return True
            return bool(_kernel32().TerminateJobObject(handle, exit_code))

    def close(self) -> None:
        with self._lock:
            handle = self._handle
            if handle is None:
                return
            self._handle = None
        _kernel32().CloseHandle(handle)


def _primary_suspended_thread_id(pid: int) -> int:
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if not snapshot or int(snapshot) == _INVALID_HANDLE_VALUE:
        raise OSError(f"CreateToolhelp32Snapshot failed: {_last_error()}")
    try:
        entry = _THREADENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        thread_ids: list[int] = []
        ok = kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while ok:
            if int(entry.th32OwnerProcessID) == pid:
                thread_ids.append(int(entry.th32ThreadID))
            ok = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        if len(thread_ids) != 1:
            raise OSError(f"Expected exactly one suspended primary thread for PID {pid}; found {thread_ids}.")
        return thread_ids[0]
    finally:
        kernel32.CloseHandle(snapshot)


def _resume_primary_thread(pid: int) -> None:
    kernel32 = _kernel32()
    thread_id = _primary_suspended_thread_id(pid)
    thread = kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, thread_id)
    if not thread:
        raise OSError(f"OpenThread failed: {_last_error()}")
    try:
        if int(kernel32.ResumeThread(thread)) == _RESUME_FAILED:
            raise OSError(f"ResumeThread failed: {_last_error()}")
    finally:
        kernel32.CloseHandle(thread)


def _close_popen_pipes(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


class OwnedProcess:
    """Small Popen proxy retaining process-tree ownership until explicitly released."""

    def __init__(self, process: subprocess.Popen[bytes], job: WindowsJob | None, backend: str) -> None:
        self.process = process
        self.job = job
        self.ownership_backend = backend

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def stdin(self) -> IO[bytes] | None:
        return self.process.stdin

    @property
    def stdout(self) -> IO[bytes] | None:
        return self.process.stdout

    @property
    def stderr(self) -> IO[bytes] | None:
        return self.process.stderr

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    def poll(self) -> int | None:
        return self.process.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self.process.wait(timeout=timeout)

    def kill(self) -> None:
        self.process.kill()

    def terminate(self) -> None:
        self.process.terminate()

    def terminate_tree(self, *, force: bool = True) -> dict[str, Any]:
        job = self.job
        if job is not None and not job.closed:
            try:
                parent = psutil.Process(self.pid)
                processes = [*parent.children(recursive=True), parent]
            except psutil.NoSuchProcess:
                processes = []
            targeted = [process.pid for process in processes]
            job.terminate(1)
            gone, alive = psutil.wait_procs(processes, timeout=5)
            if alive:
                for process in alive:
                    try:
                        process.kill()
                    except psutil.NoSuchProcess:
                        pass
                gone_after, alive = psutil.wait_procs(alive, timeout=2)
                gone.extend(gone_after)
            try:
                self.process.wait(timeout=0.1)
            except (subprocess.TimeoutExpired, OSError):
                pass
            return {
                "targeted_pids": targeted,
                "terminated_pids": sorted({process.pid for process in gone}),
                "alive_pids": sorted({process.pid for process in alive}),
                "backend": "windows_job",
            }
        from core.executor import terminate_process_tree
        result = terminate_process_tree(self.pid, force=force)
        result["backend"] = self.ownership_backend
        return result

    def close_ownership(self) -> None:
        job = self.job
        if job is not None:
            job.close()
            self.job = None


def _regular_popen(
    command: Sequence[str],
    *,
    cwd: str | Path,
    env: dict[str, str] | None,
    stdin: int | IO[Any] | None,
    stdout: int | IO[Any] | None,
    stderr: int | IO[Any] | None,
    creationflags: int,
    backend: str,
) -> OwnedProcess:
    process = subprocess.Popen(
        list(command),
        cwd=str(cwd),
        env=env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        creationflags=creationflags,
    )
    return OwnedProcess(process, None, backend)


def spawn_owned_process(
    command: Sequence[str],
    *,
    cwd: str | Path,
    env: dict[str, str] | None,
    stdin: int | IO[Any] | None,
    stdout: int | IO[Any] | None,
    stderr: int | IO[Any] | None,
    creationflags: int,
) -> OwnedProcess:
    """Spawn atomically inside a kill-on-close Job Object, with safe fallback before user code runs."""
    if not windows_job_objects_enabled():
        return _regular_popen(
            command,
            cwd=cwd,
            env=env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
            backend="psutil",
        )
    try:
        job = WindowsJob.create()
    except OSError:
        return _regular_popen(
            command,
            cwd=cwd,
            env=env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
            backend="psutil_fallback",
        )
    try:
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd),
            env=env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags | _CREATE_SUSPENDED,
        )
    except BaseException:
        job.close()
        raise
    try:
        raw_handle = getattr(process, "_handle", None)
        if raw_handle is None:
            raise OSError("CPython Popen process handle is unavailable.")
        job.assign_process_handle(int(raw_handle))
        _resume_primary_thread(process.pid)
        return OwnedProcess(process, job, "windows_job")
    except BaseException:
        try:
            process.kill()
            process.wait(timeout=5)
        except Exception:
            pass
        _close_popen_pipes(process)
        job.close()
        return _regular_popen(
            command,
            cwd=cwd,
            env=env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
            backend="psutil_fallback",
        )
