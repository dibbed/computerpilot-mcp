from __future__ import annotations

from pathlib import Path

from core.platform import (
    PlatformCapabilities,
    available_domains,
    config_home,
    domain_available,
    normalize_architecture,
    normalize_system,
    state_home,
)


def _caps(system: str) -> PlatformCapabilities:
    return PlatformCapabilities(
        system=system,  # type: ignore[arg-type]
        architecture="amd64",
        process_tree_ownership=system == "windows",
        desktop_screenshot=system == "windows",
        desktop_input=system == "windows",
        semantic_ui=system == "windows",
        system_services=system == "windows",
        installed_software=system == "windows",
        powershell=system == "windows",
        posix_shell=system != "windows",
        secure_tunnel=True,
        browser=False,
    )


def test_normalizes_supported_systems_and_architectures() -> None:
    assert normalize_system("Windows") == "windows"
    assert normalize_system("Linux") == "linux"
    assert normalize_system("Darwin") == "macos"
    assert normalize_system("Plan9") == "unknown"
    assert normalize_architecture("AMD64") == "amd64"
    assert normalize_architecture("x86_64") == "amd64"
    assert normalize_architecture("aarch64") == "arm64"
    assert normalize_architecture("mips64") == "unknown"


def test_config_and_state_homes_follow_platform_conventions() -> None:
    home = Path("/home/example")
    assert config_home(system="linux", env={}, home=home) == home / ".config"
    assert state_home(system="linux", env={}, home=home) == home / ".local/state"
    assert config_home(system="macos", env={}, home=home) == home / "Library/Application Support"
    assert config_home(system="windows", env={"APPDATA": "C:/Users/A/AppData/Roaming"}, home=home) == Path(
        "C:/Users/A/AppData/Roaming"
    )
    assert state_home(system="windows", env={"LOCALAPPDATA": "C:/Users/A/AppData/Local"}, home=home) == Path(
        "C:/Users/A/AppData/Local"
    )


def test_xdg_overrides_are_honored_cross_platform() -> None:
    env = {"XDG_CONFIG_HOME": "/cfg", "XDG_STATE_HOME": "/state"}
    assert config_home(system="linux", env=env, home=Path("/home/a")) == Path("/cfg")
    assert state_home(system="macos", env=env, home=Path("/home/a")) == Path("/state")


def test_windows_specific_domains_are_gated_on_non_windows() -> None:
    requested = ("filesystem", "windows", "desktop", "jobs")
    assert available_domains(requested, _caps("windows")) == requested
    assert available_domains(requested, _caps("linux")) == ("filesystem", "jobs")
    assert domain_available("terminal", _caps("macos")) is True
    assert domain_available("desktop", _caps("macos")) is False
