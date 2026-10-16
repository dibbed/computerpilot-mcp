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
from core.errors import ToolError
from core.windows_job import NativeOwnershipRequiredError


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
        self._close_failed = False
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
        members = _group_processes(self.pgid)
        errors: dict[str, int] = {}

        def record_error(stage: str, error: Exception | None = None) -> None:
            kind = "Unspecified" if error is None else "OtherError"
            if type(error) in {psutil.AccessDenied, psutil.Error, OSError, PermissionError, ProcessLookupError}:
                kind = type(error).__name__
            key = f"{stage}:{kind}"
            errors[key] = errors.get(key, 0) + 1

        by_pid = {process.pid: process for process in members}
        # Nested owned commands start new sessions; capture them before killing their parents.
        for process in members:
            try:
                by_pid.update((child.pid, child) for child in process.children(recursive=True))
            except psutil.NoSuchProcess:
                pass
            except (psutil.Error, OSError) as error:
                record_error("inspection", error)
        processes = list(by_pid.values())
        targeted = sorted({process.pid for process in processes} | {self.pid})

        def signal_processes(targets: list[psutil.Process], *, force: bool) -> None:
            for process in reversed(targets):
                try:
                    process.kill() if force else process.terminate()
                except psutil.NoSuchProcess:
                    pass
                except (psutil.Error, OSError) as error:
                    record_error("signal", error)

        if force:
            sigkill = int(getattr(signal, "SIGKILL", signal.SIGTERM))
            # Signal the owned group while a captured member still proves it exists.
            # Darwin may report failure once individually reaped members leave no live
            # process for killpg(), even though cleanup already succeeded.
            if not _signal_group(self.pgid, sigkill):
                record_error("group")
            signal_processes(processes, force=True)
            gone, alive = psutil.wait_procs(processes, timeout=3.0)
        else:
            if not _signal_group(self.pgid, int(signal.SIGTERM)):
                record_error("group")
            signal_processes(processes, force=False)
            gone, alive = psutil.wait_procs(processes, timeout=3.0)
            if alive:
                sigkill = int(getattr(signal, "SIGKILL", signal.SIGTERM))
                if not _signal_group(self.pgid, sigkill):
                    record_error("group")
                signal_processes(alive, force=True)
                gone_after, alive = psutil.wait_procs(alive, timeout=2.0)
                gone.extend(gone_after)
        try:
            self.process.wait(timeout=0.1)
        except (subprocess.TimeoutExpired, OSError):
            pass
        if errors or alive:
            classifications = ", ".join(f"{key}={count}" for key, count in sorted(errors.items())) or "none"
            raise ToolError(
                "process_cleanup_incomplete",
                f"Process-tree cleanup incomplete: {len(alive)} process(es) remain and {sum(errors.values())} cleanup error(s)"
                f" ({classifications}).",
            )
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
                if self._close_failed:
                    raise ToolError("process_cleanup_incomplete", "Process-tree ownership cleanup remains incomplete.")
                return
            self._closed = True
            # Failed close stays failed; never resignal a potentially reused process group.
            try:
                self._terminate_group(force=False)
            except BaseException:
                self._close_failed = True
                raise


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
    require_native: bool = False,
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
            require_native=require_native,
        )

    if not posix_process_groups_enabled():
        if require_native:
            raise NativeOwnershipRequiredError(
                "POSIX process groups are disabled but native process-tree ownership is required."
            )
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
    except Exception as exc:
        if require_native:
            raise NativeOwnershipRequiredError(
                f"Failed to spawn POSIX process in a new session: {exc}"
            ) from exc
        raise
    return PosixOwnedProcess(process, process.pid)
