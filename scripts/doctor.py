"""Full startup validation and a content-addressed successful-validation cache."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
from pathlib import Path
from typing import Any

from core.file_lock import exclusive_file_lock
from core.platform import detect_capabilities
from scripts.tunnel_runtime import (
    TunnelRuntimeError,
    clean_control_plane_key,
    current_runtime,
    detect_profile,
    profile_directory,
    profile_run_args,
)

ROOT = Path(__file__).resolve().parents[1]


def fingerprint(root: Path, mode: str, profile: str) -> str:
    """Hash inputs, never persist configuration values or credentials."""
    digest = hashlib.sha256()
    digest.update(json.dumps([sys.executable, sys.version, sys.prefix, mode, profile]).encode())
    paths = {root / name for name in (
        "requirements.txt", "requirements-runtime.txt", "requirements-quality.txt",
        "requirements-browser.txt", "requirements-dev.txt", "pyproject.toml", "main.py",
        "local_pc_mcp.py", "start_mcp.sh", ".venv/pyvenv.cfg", ".env",
    )}
    for directory in ("core", "tools", "scripts"):
        paths.update(path for path in (root / directory).rglob("*")
                     if path.suffix in {".py", ".ps1", ".bat", ".sh"} and "__pycache__" not in path.parts)
    # Metadata changes catch pip installs/uninstalls without importing packages on every start.
    site = Path(sysconfig.get_paths()["purelib"])
    for metadata in site.glob("*.dist-info"):
        paths.update(metadata / name for name in ("METADATA", "RECORD"))
    paths.add(Path(sys.executable))
    if mode == "tunnel":
        try:
            paths.add(current_runtime(root).path)
        except TunnelRuntimeError:
            pass
        profile_dir = profile_directory()
        paths.add(profile_dir / f"{profile}.yaml")
        for name in ("TUNNEL_CLIENT_CONFIG", "TUNNEL_CLIENT_PROFILE_FILE", "CLOUDFLARED_PATH", "CA_BUNDLE"):
            if os.getenv(name):
                paths.add(Path(os.environ[name]))
        paths.add(root / ".secrets/control_plane_api_key.txt")
    for name, value in sorted(os.environ.items()):
        if name.startswith(("MCP_", "TUNNEL_", "CONTROL_PLANE_", "CLOUDFLARED_", "ADMIN_UI_")) or name in {
            "PATH", "PYTHONPATH", "PLAYWRIGHT_BROWSERS_PATH", "CA_BUNDLE",
        }:
            digest.update(json.dumps([name, value]).encode())
    for path in sorted(paths, key=str):
        digest.update(str(path.absolute()).encode())
        if path.is_file():
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        else:
            digest.update(b"<missing>")
    return digest.hexdigest()


def cache_matches(root: Path, current: str) -> bool:
    try:
        record = json.loads((root / ".agent_state/startup-validation.json").read_text(encoding="utf-8"))
        return record == {"version": 1, "fingerprint": current, "ok": True}
    except (OSError, ValueError):
        return False


def _run(root: Path, label: str, command: list[str], timeout: float = 120) -> None:
    """Do not echo subprocess output: tunnel diagnostics can include credentials."""
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise RuntimeError(f"{label}: timeout must be finite and positive")
    print(f"RUN {label} (timeout {timeout:g}s)", flush=True)
    started = time.perf_counter()
    try:
        # Keep fingerprint/cache probes independent of executor dependencies.
        from core.executor import run_bounded

        result = run_bounded(
            command, cwd=root, timeout_sec=timeout, stdout_limit=0, stderr_limit=0, require_native=True,
        )
    except Exception as exc:
        raise RuntimeError(f"{label}: {type(exc).__name__}; run the check directly for details") from None
    if result["timed_out"]:
        raise RuntimeError(f"{label}: TimeoutExpired after {timeout:g}s; owned process tree stopped")
    if not result["capture_complete"]:
        raise RuntimeError(f"{label}: incomplete output drain; run the check directly for details")
    if not result["ok"]:
        raise RuntimeError(f"{label}: exit {result['exit_code']}; run the check directly for details")
    print(f"PASS {label} ({time.perf_counter() - started:.2f}s)", flush=True)


def search_backend_diagnostics() -> dict[str, str | None]:
    """Report the exact content-search backend without exposing user data."""

    rg = shutil.which("rg")
    if rg is None:
        return {
            "content_search_backend": "python",
            "ripgrep_path": None,
            "ripgrep_version": None,
        }
    try:
        from core.executor import run_bounded

        result = run_bounded(
            [rg, "--version"], cwd=ROOT, timeout_sec=10, stdout_limit=4096, stderr_limit=0,
            output_mode="head", encoding="utf-8", require_native=True,
        )
    except Exception as exc:
        raise RuntimeError(f"ripgrep backend: {type(exc).__name__}") from None
    if result["timed_out"]:
        raise RuntimeError("ripgrep backend: TimeoutExpired after 10s; owned process tree stopped")
    if not result["capture_complete"]:
        raise RuntimeError("ripgrep backend: incomplete output drain")
    if not result["ok"]:
        raise RuntimeError(f"ripgrep backend: exit {result['exit_code']}")
    first_line = next((line.strip() for line in result["stdout"]["text"].splitlines() if line.strip()), "")
    if result["stdout"]["truncated"] or re.fullmatch(
        r"ripgrep [0-9]+\.[0-9]+\.[0-9]+(?: \(rev [0-9a-f]{7,40}\))?", first_line,
    ) is None:
        raise RuntimeError("ripgrep backend: invalid or truncated version response")
    return {
        "content_search_backend": "ripgrep",
        "ripgrep_path": rg,
        "ripgrep_version": first_line,
    }


def platform_diagnostics(root: Path) -> dict[str, object]:
    """Validate the host contract required by the portable MCP runtime."""

    capabilities = detect_capabilities()
    if capabilities.system == "unknown":
        raise RuntimeError("Unsupported operating system.")
    if capabilities.architecture == "unknown":
        raise RuntimeError("Unsupported CPU architecture.")
    if not capabilities.process_tree_ownership:
        raise RuntimeError("Process-tree ownership is unavailable on this host.")
    if not capabilities.secure_tunnel:
        raise RuntimeError("Secure Tunnel release mapping is unavailable on this host.")
    if capabilities.system in {"linux", "macos"} and not capabilities.posix_shell:
        raise RuntimeError("A POSIX sh executable is required on Linux/macOS.")

    launcher = root / ("START_MCP.bat" if capabilities.system == "windows" else "start_mcp.sh")
    if not launcher.is_file():
        raise RuntimeError(f"Platform launcher is missing: {launcher.name}")
    if capabilities.system in {"linux", "macos"} and not os.access(launcher, os.X_OK):
        raise RuntimeError("start_mcp.sh must be executable.")

    lock_path = root / ".agent_state" / "doctor-platform.lock"
    try:
        with exclusive_file_lock(lock_path, timeout_sec=2, label="doctor platform lock"):
            pass
    finally:
        lock_path.unlink(missing_ok=True)

    return {
        "platform": capabilities.platform_key,
        "process_tree_ownership": capabilities.process_tree_ownership,
        "system_services": capabilities.system_services,
        "installed_software": capabilities.installed_software,
        "powershell": capabilities.powershell,
        "posix_shell": capabilities.posix_shell,
        "secure_tunnel": capabilities.secure_tunnel,
        "browser": capabilities.browser,
        "launcher": launcher.name,
    }


def _probe_process(
    command: list[str], cwd: Path, env: dict[str, str], *, read_stdout: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command, cwd=cwd, env=env, check=True, timeout=10, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if read_stdout else subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


async def _probe_codebase_memory_session(
    executable: str, repo: Path, env: dict[str, str], git: str | None,
) -> None:
    import anyio
    from mcp import ClientSession

    from core.extensions.transport import bounded_stdio_transport

    source = repo / "fixture.py"

    def commit(symbol: str) -> None:
        assert git is not None
        source.write_text(f"def {symbol}():\n    return 1\n", encoding="utf-8")
        _probe_process([git, "add", "--", source.name], repo, env)
        _probe_process([
            git, "-c", "user.name=Doctor", "-c", "user.email=doctor@example.invalid",
            "commit", "--quiet", "-m", symbol,
        ], repo, env)

    phase = "MCP connection"
    try:
        print("RUN Codebase Memory MCP connection (isolated)", flush=True)
        with anyio.fail_after(45):
            async with bounded_stdio_transport(
                [executable], cwd=repo, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                print("PASS Codebase Memory MCP connection (isolated)", flush=True)
                if git is None:
                    print("SKIP Codebase Memory Git watcher (Git unavailable on PATH)", flush=True)
                    return

                async def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                    result = await session.call_tool(name, arguments)
                    if result.is_error or result.structured_content is None:
                        raise RuntimeError(f"{name}: invalid result")
                    return result.structured_content

                print("RUN Codebase Memory Git watcher (isolated fixture)", flush=True)
                phase = "Git watcher fixture setup"
                _probe_process([git, "init", "--quiet", "--template="], repo, env)
                commit("doctor_original_symbol")
                phase = "Git watcher fixture index"
                index = await call("index_repository", {"repo_path": str(repo), "mode": "fast"})
                project = index.get("project")
                if index.get("status") != "indexed" or not isinstance(project, str) or not project:
                    raise RuntimeError("fixture was not indexed")
                query = {"project": project, "format": "json"}
                phase = "Git watcher registration"
                while True:
                    status = await call("index_status", query)
                    watch = status["watch"]
                    if watch["watched"] is not True:
                        raise RuntimeError("fixture watch was not registered")
                    if watch["strategy"] == "git":
                        break
                    if watch["strategy"] != "pending":
                        raise RuntimeError("fixture watch is not using Git")
                    await anyio.sleep(1)

                phase = "Git watcher refresh after commit"
                commit("doctor_watched_symbol")
                while True:
                    found = await call("search_graph", {
                        **query, "name_pattern": "^doctor_watched_symbol$", "limit": 1,
                    })
                    if found.get("total") == 1:
                        break
                    await anyio.sleep(1)
                print("PASS Codebase Memory Git watcher (committed symbol became searchable)", flush=True)
    except Exception as exc:
        if phase == "MCP connection":
            print("SKIP Codebase Memory Git watcher (MCP connection unavailable)", flush=True)
        raise RuntimeError(f"Codebase Memory {phase}: {type(exc).__name__}") from None


def codebase_memory_diagnostics() -> None:
    executable = shutil.which("codebase-memory-mcp")
    if executable is None:
        print("SKIP Codebase Memory MCP connection (codebase-memory-mcp unavailable on PATH)", flush=True)
        print("SKIP Codebase Memory Git watcher (MCP connection unavailable)", flush=True)
        return
    git = shutil.which("git")
    env = {name: os.environ[name] for name in (
        "SystemRoot", "WINDIR", "COMSPEC", "HOME", "TMP", "TEMP", "TMPDIR", "USERPROFILE", "LOCALAPPDATA", "LANG",
    ) if name in os.environ}
    # The default Windows temp ancestry can fail the provider's private-directory check.
    temp_parent = Path.home() if os.name == "nt" else None
    failures: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="cbmd-", dir=temp_parent) as temporary:
            base = Path(temporary).resolve()
            repo, cache, runtime = (base / name for name in ("repo", "cache", "run"))
            for directory in (repo, cache, runtime):
                directory.mkdir()
            (cache / "config.json").write_text('{"ui_enabled":false}', encoding="utf-8")
            env.update(
                PATH=str(Path(git).parent) if git else "",
                CBM_ALLOWED_ROOT=str(repo),
                CBM_CACHE_DIR=str(cache),
                CBM_RUNTIME_DIR=str(runtime),
                CBM_WORKERS="1",
                GIT_CONFIG_GLOBAL=os.devnull,
                GIT_CONFIG_NOSYSTEM="1",
            )
            # Only start versions whose daemon isolation and tool response contracts were verified.
            version = _probe_process([executable, "--version"], repo, env, read_stdout=True).stdout.strip()
            if version != b"codebase-memory-mcp 0.12.1":
                print("SKIP Codebase Memory MCP connection (isolated probe supports 0.12.1)", flush=True)
                print("SKIP Codebase Memory Git watcher (MCP connection unavailable)", flush=True)
                return
            try:
                asyncio.run(_probe_codebase_memory_session(executable, repo, env, git))
            except RuntimeError as exc:
                failures.append(str(exc))
            finally:
                try:
                    _probe_process([executable, "daemon", "stop"], repo, env)
                except (OSError, subprocess.SubprocessError) as exc:
                    failures.append(f"Codebase Memory isolated daemon cleanup: {type(exc).__name__}")
    except OSError as exc:
        if not failures:
            raise
        failures.append(f"Codebase Memory temporary directory cleanup: {type(exc).__name__}")
    if failures:
        raise RuntimeError(" | ".join(failures))


def validate(root: Path, mode: str, profile: str, browser: bool = False, codebase_memory: bool = False) -> None:
    """Run the existing complete MCP/filesystem/terminal smoke, plus environment checks."""
    python = sys.executable
    platform_info = platform_diagnostics(root)
    print("INFO platform " + json.dumps(platform_info, sort_keys=True, separators=(",", ":")))
    _run(root, "dependency imports", [python, "-c", "import mcp,pydantic,psutil,charset_normalizer,PIL,pytest,ruff,mypy"])
    _run(root, "pip check", [python, "-m", "pip", "check"])
    _run(root, "MCP filesystem and terminal smoke", [python, "-m", "scripts.health_check", "--json"])
    search_backend = search_backend_diagnostics()
    print("INFO search backend " + json.dumps(search_backend, sort_keys=True, separators=(",", ":")))
    if browser or importlib.util.find_spec("playwright") is not None:
        try:
            _run(root, "Playwright package import", [python, "-c", "import playwright.async_api"])
        except RuntimeError:
            print("SKIP Chromium launch (Playwright import failed)", flush=True)
            raise
    else:
        print("SKIP Playwright package import (package unavailable)", flush=True)
    if browser:
        _run(root, "Chromium launch", [python, "-c", (
            "from playwright.sync_api import sync_playwright\n"
            "with sync_playwright() as playwright:\n"
            "    browser = playwright.chromium.launch(headless=True)\n"
            "    page = browser.new_page()\n"
            "    page.goto('about:blank')\n"
            "    page.set_content('<title>Doctor</title>')\n"
            "    if page.title() != 'Doctor':\n"
            "        raise RuntimeError('Chromium page check failed')\n"
            "    browser.close()\n"
        )])
    else:
        print("SKIP Chromium launch (use --browser)", flush=True)
    if codebase_memory:
        codebase_memory_diagnostics()
    else:
        print("SKIP Codebase Memory MCP connection (use --codebase-memory)", flush=True)
        print("SKIP Codebase Memory Git watcher (use --codebase-memory)", flush=True)
    if mode == "tunnel":
        selection = current_runtime(root)
        _run(root, f"Secure MCP Tunnel runtime v{selection.version}", [str(selection.path), "--version"], timeout=15)
        if selection.path.name.casefold() in {"tunnel-client.exe", "tunnel-client"}:
            try:
                _run(
                    root,
                    "Secure MCP Tunnel doctor",
                    [str(selection.path), "doctor", *profile_run_args(profile), "--json"],
                )
            except RuntimeError as exc:
                # Network, profile, or port conditions can make the upstream doctor fail
                # while the local MCP itself remains valid. Keep the failure observable.
                if "exit" in str(exc):
                    print(f"WARN tunnel doctor: {exc} (non-fatal)")
                else:
                    raise


def doctor(root: Path, mode: str, profile: str, browser: bool = False, codebase_memory: bool = False) -> None:
    current = fingerprint(root, mode, profile)
    cache = root / ".agent_state/startup-validation.json"
    # A failed explicit doctor must invalidate an older successful record too.
    cache.unlink(missing_ok=True)
    validate(root, mode, profile, browser, codebase_memory)
    if fingerprint(root, mode, profile) != current:
        raise RuntimeError("Startup inputs changed during validation; run doctor again")
    cache.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=cache.parent, prefix="startup-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "fingerprint": current, "ok": True}, stream)
        os.replace(temporary, cache)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Only check successful fingerprint; exit 2 when validation is required")
    parser.add_argument("--mode", choices=("tunnel", "local-http"), default=os.getenv("MCP_START_MODE", "tunnel"))
    parser.add_argument("--profile", default=os.getenv("MCP_TUNNEL_PROFILE", ""))
    parser.add_argument("--browser", action="store_true", help="Also launch a disposable headless Chromium")
    parser.add_argument("--codebase-memory", action="store_true", help="Also probe isolated Codebase Memory and its Git watcher")
    args = parser.parse_args()
    try:
        if sys.version_info < (3, 10):  # noqa: UP036 - doctor diagnoses unsupported interpreters
            raise RuntimeError("Python 3.10 or newer is required")
        profile = args.profile
        if args.mode == "tunnel":
            if not profile:
                profile = detect_profile()
            current_runtime(ROOT)
            secret = ROOT / ".secrets/control_plane_api_key.txt"
            existing = clean_control_plane_key(os.getenv("CONTROL_PLANE_API_KEY", ""))
            if existing:
                os.environ["CONTROL_PLANE_API_KEY"] = existing
            elif secret.is_file():
                try:
                    os.environ["CONTROL_PLANE_API_KEY"] = clean_control_plane_key(secret.read_bytes())
                except (OSError, ValueError):
                    pass
            if not os.getenv("CONTROL_PLANE_API_KEY"):
                raise RuntimeError("CONTROL_PLANE_API_KEY or .secrets/control_plane_api_key.txt is required")
        if args.check:
            return 0 if cache_matches(ROOT, fingerprint(ROOT, args.mode, profile)) else 2
        doctor(ROOT, args.mode, profile, args.browser, args.codebase_memory)
        print("PASS full doctor; startup fingerprint saved")
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"FAIL doctor: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
