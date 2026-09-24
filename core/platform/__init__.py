"""Host platform detection and explicit capability negotiation."""

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

PlatformName = Literal["windows", "linux", "macos", "unknown"]
Architecture = Literal["amd64", "arm64", "unknown"]

_PORTABLE_DOMAINS = frozenset({
    "filesystem",
    "terminal",
    "process",
    "system",
    "project",
    "language",
    "testing",
    "git",
    "browser",
    "memory",
    "jobs",
    "recovery",
    "workflows",
})
_WINDOWS_ONLY_DOMAINS = frozenset({"windows", "desktop"})


def normalize_system(value: str | None = None) -> PlatformName:
    raw = (value or platform.system()).strip().casefold()
    if raw == "windows":
        return "windows"
    if raw == "linux":
        return "linux"
    if raw in {"darwin", "mac", "macos"}:
        return "macos"
    return "unknown"


def normalize_architecture(value: str | None = None) -> Architecture:
    raw = (value or platform.machine()).strip().casefold()
    if raw in {"amd64", "x86_64", "x64"}:
        return "amd64"
    if raw in {"arm64", "aarch64"}:
        return "arm64"
    return "unknown"


def config_home(
    *,
    system: PlatformName | None = None,
    env: dict[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    current = system or normalize_system()
    values = os.environ if env is None else env
    user_home = home or Path.home()

    xdg = values.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser()

    if current == "windows":
        appdata = values.get("APPDATA", "").strip()
        return Path(appdata).expanduser() if appdata else user_home / "AppData" / "Roaming"

    if current == "macos":
        return user_home / "Library" / "Application Support"

    return user_home / ".config"


def state_home(
    *,
    system: PlatformName | None = None,
    env: dict[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    current = system or normalize_system()
    values = os.environ if env is None else env
    user_home = home or Path.home()

    xdg = values.get("XDG_STATE_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser()

    if current == "windows":
        local = values.get("LOCALAPPDATA", "").strip()
        return Path(local).expanduser() if local else user_home / "AppData" / "Local"

    if current == "macos":
        return user_home / "Library" / "Application Support"

    return user_home / ".local" / "state"


@dataclass(frozen=True, slots=True)
class PlatformCapabilities:
    system: PlatformName
    architecture: Architecture
    process_tree_ownership: bool
    desktop_screenshot: bool
    desktop_input: bool
    semantic_ui: bool
    system_services: bool
    installed_software: bool
    powershell: bool
    posix_shell: bool
    secure_tunnel: bool
    browser: bool

    @property
    def platform_key(self) -> str:
        return f"{self.system}-{self.architecture}"

    def as_dict(self) -> dict[str, str | bool]:
        return asdict(self)


def detect_capabilities(
    *,
    system: PlatformName | None = None,
    architecture: Architecture | None = None,
) -> PlatformCapabilities:
    current = system or normalize_system()
    arch = architecture or normalize_architecture()

    is_windows = current == "windows"
    is_posix = current in {"linux", "macos"}
    supported_host = current in {"windows", "linux", "macos"} and arch in {"amd64", "arm64"}

    return PlatformCapabilities(
        system=current,
        architecture=arch,
        # POSIX atomic process-group ownership is introduced in the next migration phase.
        process_tree_ownership=is_windows,
        desktop_screenshot=is_windows,
        desktop_input=is_windows,
        semantic_ui=is_windows and importlib.util.find_spec("uiautomation") is not None,
        system_services=(
            is_windows
            or (current == "linux" and (shutil.which("systemctl") is not None or Path("/etc/init.d").is_dir()))
            or (current == "macos" and shutil.which("launchctl") is not None)
        ),
        installed_software=(
            is_windows
            or (
                current == "linux"
                and any(shutil.which(name) is not None for name in ("dpkg-query", "rpm", "pacman"))
            )
            or (
                current == "macos"
                and (
                    shutil.which("system_profiler") is not None
                    or Path("/Applications").is_dir()
                    or (Path.home() / "Applications").is_dir()
                )
            )
        ),
        powershell=(shutil.which("pwsh") is not None) or (is_windows and shutil.which("powershell") is not None),
        posix_shell=is_posix and shutil.which("sh") is not None,
        secure_tunnel=supported_host,
        browser=importlib.util.find_spec("playwright") is not None,
    )


def domain_available(domain: str, capabilities: PlatformCapabilities | None = None) -> bool:
    caps = capabilities or detect_capabilities()
    if domain in _PORTABLE_DOMAINS:
        return True
    if domain == "windows":
        return caps.system_services or caps.installed_software
    if domain == "desktop":
        return caps.desktop_input or caps.desktop_screenshot or caps.semantic_ui
    return domain not in _WINDOWS_ONLY_DOMAINS


def available_domains(
    domains: tuple[str, ...],
    capabilities: PlatformCapabilities | None = None,
) -> tuple[str, ...]:
    caps = capabilities or detect_capabilities()
    return tuple(domain for domain in domains if domain_available(domain, caps))
