"""Full startup validation and a content-addressed successful-validation cache."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

from scripts.tunnel_runtime import TunnelRuntimeError, current_runtime, detect_profile, profile_run_args

ROOT = Path(__file__).resolve().parents[1]


def fingerprint(root: Path, mode: str, profile: str) -> str:
    """Hash inputs, never persist configuration values or credentials."""
    digest = hashlib.sha256()
    digest.update(json.dumps([sys.executable, sys.version, sys.prefix, mode, profile]).encode())
    paths = {root / name for name in (
        "requirements.txt", "requirements-browser.txt", "pyproject.toml", "main.py",
        "local_pc_mcp.py", ".venv/pyvenv.cfg", ".env",
    )}
    for directory in ("core", "tools", "scripts"):
        paths.update(path for path in (root / directory).rglob("*")
                     if path.suffix in {".py", ".ps1", ".bat"} and "__pycache__" not in path.parts)
    # Metadata changes catch pip installs/uninstalls without importing packages on every start.
    site = Path(sysconfig.get_paths()["purelib"])
    for metadata in site.glob("*.dist-info"):
        paths.update(metadata / name for name in ("METADATA", "RECORD"))
    paths.add(Path(sys.executable))
    if mode == "tunnel":
        paths.update(root / name for name in ("tunnel-client.exe", "tunnel-client-runtime.exe", "cloudflared.exe"))
        try:
            paths.add(current_runtime(root).path)
        except TunnelRuntimeError:
            pass
        config_home = Path(os.getenv("APPDATA", str(Path.home() / ".config")))
        profile_dir = Path(os.getenv("TUNNEL_CLIENT_PROFILE_DIR", str(config_home / "tunnel-client")))
        paths.update(profile_dir / f"{profile}{extension}" for extension in (".yaml", ".yml"))
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


def _run(root: Path, label: str, command: list[str], timeout: int = 120) -> None:
    """Do not echo subprocess output: tunnel diagnostics can include credentials."""
    try:
        result = subprocess.run(command, cwd=root, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"{label}: {type(exc).__name__}; run the check directly for details") from exc
    if result.returncode:
        raise RuntimeError(f"{label}: exit {result.returncode}; run the check directly for details")
    print(f"PASS {label}")


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
        result = subprocess.run([rg, "--version"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"ripgrep backend: {type(exc).__name__}") from exc
    if result.returncode:
        raise RuntimeError(f"ripgrep backend: exit {result.returncode}")
    first_line = next((line.strip() for line in result.stdout.splitlines() if line.strip()), None)
    return {
        "content_search_backend": "ripgrep",
        "ripgrep_path": rg,
        "ripgrep_version": first_line,
    }


def validate(root: Path, mode: str, profile: str, browser: bool = False) -> None:
    """Run the existing complete MCP/filesystem/terminal smoke, plus environment checks."""
    python = sys.executable
    _run(root, "dependency imports", [python, "-c", "import mcp,pydantic,psutil,charset_normalizer,PIL,pytest,ruff,mypy"])
    _run(root, "pip check", [python, "-m", "pip", "check"])
    _run(root, "MCP filesystem and terminal smoke", [python, "-m", "scripts.health_check", "--json"])
    search_backend = search_backend_diagnostics()
    print("INFO search backend " + json.dumps(search_backend, sort_keys=True, separators=(",", ":")))
    if importlib.util.find_spec("playwright") is not None:
        _run(root, "browser imports", [python, "-c", "import playwright.async_api"])
    if browser:
        _run(root, "browser launch", [python, "-c", (
            "from playwright.sync_api import sync_playwright; "
            "p=sync_playwright().start(); b=p.chromium.launch(headless=True); "
            "page=b.new_page(); page.goto('about:blank'); b.close(); p.stop()"
        )])
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


def doctor(root: Path, mode: str, profile: str, browser: bool = False) -> None:
    current = fingerprint(root, mode, profile)
    cache = root / ".agent_state/startup-validation.json"
    # A failed explicit doctor must invalidate an older successful record too.
    cache.unlink(missing_ok=True)
    validate(root, mode, profile, browser)
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
            if not os.getenv("CONTROL_PLANE_API_KEY") and secret.is_file():
                os.environ["CONTROL_PLANE_API_KEY"] = secret.read_text(encoding="utf-8").strip()
            if not os.getenv("CONTROL_PLANE_API_KEY"):
                raise RuntimeError("CONTROL_PLANE_API_KEY or .secrets/control_plane_api_key.txt is required")
        if args.check:
            return 0 if cache_matches(ROOT, fingerprint(ROOT, args.mode, profile)) else 2
        doctor(ROOT, args.mode, profile, args.browser)
        print("PASS full doctor; startup fingerprint saved")
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"FAIL doctor: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
