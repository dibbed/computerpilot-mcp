"""Owned process transport for language servers.

Every language-server process is spawned under the strongest process-tree
ownership available on this host (Windows Job Object or POSIX process group)
with an explicit minimal environment: only fixed OS variables required for
process startup are copied, ``PATH`` is narrowed to the pinned executable
directories of the reviewed command tuple, and no other parent variable
(``PYTHONPATH``, ``PYTHONHOME``, ``NODE_OPTIONS``, ``NODE_PATH``, shell loader
variables, credentials) is inherited. Environment filtering is not an OS
sandbox: the server still runs with the user's own account privileges.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from core.errors import ToolError
from core.executor import _creation_flags
from core.process_ownership import OwnedProcess, spawn_owned_process
from core.windows_job import NativeOwnershipRequiredError
from tools.language.providers import LspProvider, command_for, validate_provider

_OS_ENV_VARS = ("SystemRoot", "WINDIR", "TEMP", "TMP") if os.name == "nt" else ("LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")


def build_environment(executable_paths: Sequence[Path]) -> dict[str, str]:
    """Build the minimal launch environment; nothing else is inherited."""

    environment: dict[str, str] = {}
    for name in _OS_ENV_VARS:
        value = os.environ.get(name)
        if value:
            environment[name] = value
    directories: list[str] = []
    seen: set[str] = set()
    for executable in executable_paths:
        parent = str(Path(executable).parent)
        key = parent.casefold() if os.name == "nt" else parent
        if key not in seen:
            seen.add(key)
            directories.append(parent)
    environment["PATH"] = os.pathsep.join(directories)
    return environment


def _spawn(command: Sequence[str], root: str | Path, executable_paths: Sequence[Path]) -> OwnedProcess:
    environment = build_environment(executable_paths)
    try:
        return spawn_owned_process(
            command,
            cwd=root,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_creation_flags(),
            require_native=True,
        )
    except NativeOwnershipRequiredError as exc:
        raise ToolError(
            "lsp_ownership_unavailable",
            "Native process-tree ownership is unavailable; refusing to launch a language server.",
        ) from exc
    except FileNotFoundError as exc:
        raise ToolError("lsp_server_not_found", "Language server executable not found.") from exc
    except OSError as exc:
        raise ToolError("lsp_launch_failed", "The language server process could not be started.") from exc


def open_transport(provider: LspProvider, root: str | Path) -> OwnedProcess:
    """Spawn a pinned provider under native ownership.

    Recheck pins and root eligibility at the launch boundary.
    """

    if not provider.languages:
        raise ToolError("lsp_provider_unavailable", "The LSP provider has no eligible language.")
    validate_provider(root, provider.languages[0], providers=(provider,))
    return _spawn(command_for(provider), root, [provider.executable])


def _open_command_transport(command: Sequence[str], root: str | Path) -> OwnedProcess:
    """Private compatibility adapter for already-resolved absolute command tuples.

    This exists for the legacy one-shot client only. It is not a public
    dispatcher: commands must be absolute, and it never resolves names through
    ``PATH`` on its own.
    """

    if not command or not command[0]:
        raise ToolError("lsp_server_not_found", "Language server executable not found.")
    executable = Path(command[0])
    if not executable.is_absolute():
        raise ToolError("lsp_server_not_found", "Language server executable not found.")
    return _spawn(command, root, [executable])


def close_transport(process: OwnedProcess, *, timeout_sec: float) -> bool:
    """Close one owned language-server transport.

    A graceful window is attempted first (stdin EOF, then wait); a process
    that is still alive afterwards is reaped with ``terminate_tree(force=True)``.
    ``close_ownership`` runs only after that cleanup attempt. The return value
    is ``True`` only when process death and ownership release are both proven;
    ``False`` means the caller must quarantine the session and preserve
    evidence instead of reusing or trusting anything about this process.
    """

    ok = True
    try:
        stdin = process.stdin
        if stdin is not None and not stdin.closed:
            try:
                stdin.close()
            except OSError:
                ok = False
        if process.poll() is None:
            try:
                process.wait(timeout=timeout_sec)
            except (subprocess.TimeoutExpired, OSError):
                pass
        if process.poll() is None:
            try:
                process.terminate_tree(force=True)
            except (ToolError, OSError):
                ok = False
            try:
                process.wait(timeout=2.0)
            except (subprocess.TimeoutExpired, OSError):
                ok = False
        if process.poll() is None:
            ok = False
    finally:
        try:
            process.close_ownership()
        except (ToolError, OSError):
            ok = False
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is None or stream.closed:
                continue
            if process.poll() is None:
                # A live reader may hold the stream lock; quarantine without blocking close.
                ok = False
                continue
            try:
                stream.close()
            except OSError:
                ok = False
    return ok
