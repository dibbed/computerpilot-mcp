"""Cross-platform process-tree ownership.

Windows keeps the existing Job Object backend. POSIX hosts spawn commands in a
new session/process group so cancellation and owner shutdown can reap the whole
tree without relying only on descendant discovery.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import IO, Any, Protocol

import psutil

from core import windows_job


class OwnedProcess(Protocol):
    """Common process ownership contract consumed by executor/jobs/workflows."""

    process: subprocess.Popen[bytes]
    ownership_backend: str

    @property
    def pid(self) -> int: ...

    @property
    def stdin(self) -> IO[bytes] | None: ...

    @property
    def stdout(self) -> IO[bytes] | None: ...

    @property
    def stderr(self) -> IO[bytes] | None: ...

    @property
    def returncode(self) -> int | None: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...

    def terminate(self) -> None: ...

    def terminate_tree(self, *, force: bool = True) -> dict[str, Any]: ...

    def close_ownership(self) -> None: ...


def posix_process_groups_enabled() -> bool:
    if os.name == "nt":
        return False
    return os.getenv("MCP_POSIX_PROCESS_GROUPS", "1").strip().casefold() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _process_group_id(pid: int) -> int | None:
    getter = getattr(os, "getpgid", None)
    if getter is None:
        return None
    try:
        return int(getter(pid))
    except (ProcessLookupError, PermissionError, OSError):
        return None


def _group_processes(pgid: int) -> list[psutil.Process]:
    rows: list[psutil.Process] = []
    for process in psutil.process_iter(attrs=["pid"]):
        try:
            pid = int(process.info["pid"])
        except (KeyError, TypeError, ValueError):
            continue
        if _process_group_id(pid) == pgid:
            rows.append(process)
    return rows


def _signal_group(pgid: int, sig: int) -> bool:
    kill_group = getattr(os, "killpg", None)
    if kill_group is None:
        return False
    try:
        kill_group(pgid, sig)
        return True
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False


class PosixOwnedProcess:
    """Popen proxy retaining one dedicated POSIX process-group ownership."""

    def __init__(self, process: subprocess.Popen[bytes], pgid: int, backend: str = "posix_process_group") -> None:
        self.process = process
        self.pgid = pgid
        self.ownership_backend = backend
        self._closed = False
        self._lock = threading.Lock()

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

    def _terminate_group(self, *, force: bool) -> dict[str, Any]:
        processes = _group_processes(self.pgid)
        targeted = sorted({process.pid for process in processes} | {self.pid})
        if force:
            sigkill = int(getattr(signal, "SIGKILL", signal.SIGTERM))
            _signal_group(self.pgid, sigkill)
            gone, alive = psutil.wait_procs(processes, timeout=3.0)
        else:
            _signal_group(self.pgid, int(signal.SIGTERM))
            gone, alive = psutil.wait_procs(processes, timeout=3.0)
            if alive:
                sigkill = int(getattr(signal, "SIGKILL", signal.SIGTERM))
                _signal_group(self.pgid, sigkill)
                gone_after, alive = psutil.wait_procs(alive, timeout=2.0)
                gone.extend(gone_after)
        try:
            self.process.wait(timeout=0.1)
        except (subprocess.TimeoutExpired, OSError):
            pass
        return {
            "targeted_pids": targeted,
            "terminated_pids": sorted({process.pid for process in gone}),
            "alive_pids": sorted({process.pid for process in alive}),
            "backend": self.ownership_backend,
        }

    def terminate_tree(self, *, force: bool = True) -> dict[str, Any]:
        return self._terminate_group(force=force)

    def close_ownership(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        # Mirrors JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: descendants may outlive the
        # root, so releasing ownership must reap the dedicated process group.
        self._terminate_group(force=False)


class PsutilOwnedProcess:
    """Compatibility fallback when platform-native ownership is disabled."""

    def __init__(self, process: subprocess.Popen[bytes], backend: str) -> None:
        self.process = process
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
        # Import lazily to avoid an executor/process-ownership import cycle.
        from core.executor import terminate_process_tree

        result = terminate_process_tree(self.pid, force=force)
        result["backend"] = self.ownership_backend
        return result

    def close_ownership(self) -> None:
        return


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
    return PsutilOwnedProcess(process, backend)


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
    """Spawn under the strongest process-tree ownership available on this host."""

    if os.name == "nt":
        return windows_job.spawn_owned_process(
            command,
            cwd=cwd,
            env=env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
        )

    if not posix_process_groups_enabled():
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

    process = subprocess.Popen(
        list(command),
        cwd=str(cwd),
        env=env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        creationflags=creationflags,
        start_new_session=True,
    )
    return PosixOwnedProcess(process, process.pid)
