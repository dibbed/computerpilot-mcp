"""Startup cache invalidation and failed validation must fail closed."""

import json
import subprocess
from pathlib import Path

import pytest

from core.platform import PlatformCapabilities
from scripts import doctor
from scripts.tunnel_runtime import TunnelRuntimeSelection


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(doctor.sysconfig, "get_paths", lambda: {"purelib": str(tmp_path / "site")})
    monkeypatch.setenv("TUNNEL_CLIENT_PROFILE_DIR", str(tmp_path / "profiles"))
    for name in ("main.py", "requirements.txt", "requirements-browser.txt", "pyproject.toml", "core/config.py",
                 "tools/example.py", "scripts/bootstrap.ps1", "local_pc_mcp.py", "START_MCP.bat", "start_mcp.sh", "tunnel-client.exe",
                 "tunnel-client-runtime.exe", "cloudflared.exe", "profiles/demo.yaml", "site/example.dist-info/METADATA"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original", encoding="utf-8")
    (tmp_path / "start_mcp.sh").chmod(0o755)
    return tmp_path


@pytest.mark.parametrize("name", ["main.py", "requirements.txt", "requirements-browser.txt", "pyproject.toml",
                                      "core/config.py", "tools/example.py", "scripts/bootstrap.ps1", "local_pc_mcp.py",
                                      "tunnel-client.exe", "tunnel-client-runtime.exe", "cloudflared.exe",
                                      "profiles/demo.yaml", "site/example.dist-info/METADATA"])
def test_fingerprint_invalidates_same_size_edits(root: Path, name: str) -> None:
    before = doctor.fingerprint(root, "tunnel", "demo")
    (root / name).write_text("modified", encoding="utf-8")
    assert doctor.fingerprint(root, "tunnel", "demo") != before


def test_success_cache_contains_no_secrets(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_PLANE_API_KEY", "private-credential")
    monkeypatch.setattr(doctor, "validate", lambda *args: None)
    doctor.doctor(root, "tunnel", "demo")
    assert doctor.cache_matches(root, doctor.fingerprint(root, "tunnel", "demo"))
    record = (root / ".agent_state/startup-validation.json").read_text()
    assert "private-credential" not in record
    assert set(json.loads(record)) == {"version", "fingerprint", "ok"}
    monkeypatch.setenv("CONTROL_PLANE_API_KEY", "new-credential")
    assert not doctor.cache_matches(root, doctor.fingerprint(root, "tunnel", "demo"))


def test_failure_removes_previous_success(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "validate", lambda *args: None)
    doctor.doctor(root, "local-http", "demo")

    def fail(*args: object) -> None:
        raise RuntimeError("pip check failed")

    monkeypatch.setattr(doctor, "validate", fail)
    with pytest.raises(RuntimeError, match="pip check"):
        doctor.doctor(root, "local-http", "demo")
    assert not doctor.cache_matches(root, doctor.fingerprint(root, "local-http", "demo"))


def test_change_during_doctor_not_cached(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "validate", lambda *args: (root / "main.py").write_text("changed"))
    with pytest.raises(RuntimeError, match="changed during"):
        doctor.doctor(root, "local-http", "demo")
    assert not (root / ".agent_state/startup-validation.json").exists()


def _platform_caps(**overrides: object) -> PlatformCapabilities:
    values: dict[str, object] = {
        "system": "linux",
        "architecture": "amd64",
        "process_tree_ownership": True,
        "desktop_screenshot": False,
        "desktop_input": False,
        "semantic_ui": False,
        "system_services": True,
        "installed_software": True,
        "powershell": False,
        "posix_shell": True,
        "secure_tunnel": True,
        "browser": False,
    }
    values.update(overrides)
    return PlatformCapabilities(**values)  # type: ignore[arg-type]


def test_platform_diagnostics_validates_launcher_and_lock(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "detect_capabilities", lambda: _platform_caps())
    result = doctor.platform_diagnostics(root)
    assert result["platform"] == "linux-amd64"
    assert result["launcher"] == "start_mcp.sh"
    assert result["process_tree_ownership"] is True
    assert not (root / ".agent_state/doctor-platform.lock").exists()


def test_platform_diagnostics_fails_without_process_ownership(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        doctor,
        "detect_capabilities",
        lambda: _platform_caps(process_tree_ownership=False),
    )
    with pytest.raises(RuntimeError, match="Process-tree ownership"):
        doctor.platform_diagnostics(root)


def test_full_validation_includes_smoke_and_tunnel(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(doctor, "_run", lambda root, label, command, timeout=120: calls.append(command))
    monkeypatch.setattr(
        doctor,
        "current_runtime",
        lambda root: TunnelRuntimeSelection(
            path=root / "tunnel-client.exe",
            version="0.0.14",
            source="test",
            platform_key="windows-amd64",
        ),
    )
    monkeypatch.setattr(
        doctor,
        "search_backend_diagnostics",
        lambda: {"content_search_backend": "ripgrep", "ripgrep_path": "rg.exe", "ripgrep_version": "ripgrep 14.1.0"},
    )
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: None)
    doctor.validate(root, "tunnel", "demo", browser=True)
    assert any(command[-2:] == ["pip", "check"] for command in calls)
    assert any("scripts.health_check" in command for command in calls)
    assert any(command[-3:] == ["--profile", "demo", "--json"] for command in calls)
    assert any("chromium.launch" in " ".join(command) for command in calls)



def test_search_backend_diagnostics_reports_python_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    assert doctor.search_backend_diagnostics() == {
        "content_search_backend": "python",
        "ripgrep_path": None,
        "ripgrep_version": None,
    }


def test_search_backend_diagnostics_reports_ripgrep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda name: r"C:\\Tools\\rg.exe")
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "ripgrep 14.1.0\nfeatures", ""),
    )
    assert doctor.search_backend_diagnostics() == {
        "content_search_backend": "ripgrep",
        "ripgrep_path": r"C:\\Tools\\rg.exe",
        "ripgrep_version": "ripgrep 14.1.0",
    }

def test_subprocess_failure_redacts_output(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 7, b"secret", b"secret"))
    with pytest.raises(RuntimeError, match="exit 7") as error:
        doctor._run(root, "Tunnel", ["fake"])
    assert "secret" not in str(error.value)


def test_cache_probe_never_runs_full_validation(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "ROOT", root)
    monkeypatch.setattr(doctor, "validate", lambda *args: pytest.fail("full validation on fast check"))
    monkeypatch.setattr(doctor.sys, "argv", ["doctor", "--check", "--mode", "local-http"])
    assert doctor.main() == 2
    cache = root / ".agent_state/startup-validation.json"
    cache.parent.mkdir()
    cache.write_text(json.dumps({"version": 1, "fingerprint": doctor.fingerprint(root, "local-http", ""), "ok": True}))
    assert doctor.main() == 0


def test_interpreter_and_profile_invalidate(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    initial = doctor.fingerprint(root, "tunnel", "demo")
    assert doctor.fingerprint(root, "tunnel", "other") != initial
    monkeypatch.setattr(doctor.sys, "version", "different Python version")
    assert doctor.fingerprint(root, "tunnel", "demo") != initial


@pytest.mark.parametrize("contents", ["broken", "null", '{"ok": false}', '{"ok": true}'])
def test_corrupt_or_incomplete_cache_is_miss(root: Path, contents: str) -> None:
    cache = root / ".agent_state/startup-validation.json"
    cache.parent.mkdir()
    cache.write_text(contents)
    assert not doctor.cache_matches(root, doctor.fingerprint(root, "local-http", "demo"))


def test_doctor_main_loads_secret_with_utf8_bom(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONTROL_PLANE_API_KEY", raising=False)
    monkeypatch.setattr(doctor, "ROOT", root)
    secret = root / ".secrets" / "control_plane_api_key.txt"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_bytes(b"\xef\xbb\xbfsecret-from-doctor-bom\r\n")
    selection = TunnelRuntimeSelection(root / "tunnel-client.exe", "0.0.14", "test", "windows-amd64")
    monkeypatch.setattr(doctor, "current_runtime", lambda r: selection)
    monkeypatch.setattr(doctor, "detect_profile", lambda: "demo")
    monkeypatch.setattr(doctor, "doctor", lambda *args: None)
    monkeypatch.setattr(doctor.sys, "argv", ["doctor", "--mode", "tunnel", "--profile", "demo"])
    assert doctor.main() == 0
    assert doctor.os.environ["CONTROL_PLANE_API_KEY"] == "secret-from-doctor-bom"
