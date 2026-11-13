from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts import bootstrap


def test_recovers_incomplete_wheel_in_managed_venv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    venv = tmp_path / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=90)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = Path(subprocess.check_output(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True, timeout=15,
    ).strip())
    metadata = site / "repair_demo-1.0.dist-info"
    metadata.mkdir()
    wheel_metadata = "Metadata-Version: 2.1\nName: repair-demo\nVersion: 1.0\n"
    old_metadata = site / "~epair_demo-0.9.dist-info"
    old_metadata.mkdir()
    old_package = site / "~epair_demo"
    old_package.mkdir()
    wheel = tmp_path / "repair_demo-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("repair_demo.py", "READY = True\n")
        archive.writestr(f"{metadata.name}/METADATA", wheel_metadata)
        archive.writestr(f"{metadata.name}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(f"{metadata.name}/RECORD", "")
    (tmp_path / "requirements.txt").write_text("repair-demo==1.0\n", encoding="utf-8")
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_FIND_LINKS", str(tmp_path))
    monkeypatch.setenv("MCP_START_MODE", "local-http")
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    (venv / ".requirements.sha256").write_text(
        bootstrap.requirements_fingerprint(tmp_path / "requirements.txt"), encoding="ascii",
    )

    subprocess.run([str(python), "-c", (
        "from pathlib import Path\n"
        "from scripts import bootstrap\n"
        f"bootstrap.ROOT = Path({str(tmp_path)!r})\n"
        "bootstrap._required_imports = lambda: ('repair_demo',)\n"
        "bootstrap._full_doctor = lambda mode, profile: None\n"
        "assert bootstrap.validate() == ('local-http', 'default')\n"
        "import repair_demo\n"
        "assert repair_demo.READY\n"
    )], cwd=Path(__file__).resolve().parents[1], check=True, timeout=90)
    subprocess.run([str(python), "-m", "pip", "check"], check=True, timeout=15)
    assert (metadata / "RECORD").is_file()
    assert not old_metadata.exists() and not old_package.exists()
    assert list(venv.glob(f"repair-quarantine-*/**/{old_metadata.name}"))
    assert list(venv.glob(f"repair-quarantine-*/**/{old_package.name}"))


def test_doctor_watchdog_retains_tree_ownership_on_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import process_ownership

    closed: list[bool] = []

    class Process:
        def wait(self, timeout: float | None = None) -> int:
            assert timeout == 0.1
            raise subprocess.TimeoutExpired(["secret-canary"], timeout)

        def close_ownership(self) -> None:
            closed.append(True)

    def spawn(*args: object, **kwargs: object) -> Process:
        assert kwargs["require_native"] is True
        assert kwargs["stdout"] is None and kwargs["stderr"] is None
        return Process()

    monkeypatch.setattr(process_ownership, "spawn_owned_process", spawn)
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    with pytest.raises(bootstrap.BootstrapError) as error:
        bootstrap._run(["fixture"], timeout=0.1)
    assert closed == [True] and "secret-canary" not in str(error.value)


def test_cache_and_full_doctor_have_explicit_watchdogs(monkeypatch: pytest.MonkeyPatch) -> None:
    budgets: list[float | None] = []

    def run(command: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        budgets.append(timeout)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(bootstrap, "_run", run)
    monkeypatch.setattr(bootstrap, "imports_healthy", lambda: True)
    assert bootstrap._doctor_check("local-http", "default")
    bootstrap._full_doctor("local-http", "default")
    assert budgets == [30, 900]


def test_cache_probe_cleanup_failure_stops_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import process_ownership
    from core.errors import ToolError

    class Process:
        def wait(self, timeout: float | None = None) -> int:
            return 0

        def close_ownership(self) -> None:
            raise ToolError("process_cleanup_incomplete", "secret-canary")

    monkeypatch.setenv("MCP_START_MODE", "local-http")
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "_repair_dependencies", lambda: False)
    monkeypatch.setattr(bootstrap, "imports_healthy", lambda: True)
    monkeypatch.setattr(process_ownership, "spawn_owned_process", lambda *args, **kwargs: Process())
    monkeypatch.setattr(bootstrap, "_ensure_dependencies", lambda **kwargs: pytest.fail("continued after uncertain cleanup"))
    with pytest.raises(bootstrap.BootstrapError) as error:
        bootstrap.validate()
    assert "secret-canary" not in str(error.value)


def test_cache_probe_defers_missing_dependencies_to_installer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bootstrap, "imports_healthy", lambda: False)
    monkeypatch.setattr(bootstrap, "_run", lambda *args, **kwargs: pytest.fail("spawned without required imports"))
    assert not bootstrap._doctor_check("local-http", "default")


def test_requirements_fingerprint_includes_nested_files(tmp_path: Path) -> None:
    root = tmp_path / "requirements.txt"
    child = tmp_path / "child.txt"
    root.write_text("-r child.txt\n", encoding="utf-8")
    child.write_text("demo==1\n", encoding="utf-8")
    before = bootstrap.requirements_fingerprint(root)
    child.write_text("demo==2\n", encoding="utf-8")
    after = bootstrap.requirements_fingerprint(root)
    assert before != after


def test_document_runtime_imports_are_required() -> None:
    imports = set(bootstrap._required_imports())
    assert {"openpyxl", "docx", "pypdf", "reportlab"} <= imports


def test_supervisor_args_are_platform_neutral() -> None:
    assert bootstrap._supervisor_args("local-http", "default") == [
        "--mode",
        "local-http",
    ]
    assert bootstrap._supervisor_args("tunnel", "work")[-2:] == ["--profile", "work"]


def test_windows_launcher_suppresses_batch_interrupt_confirmation() -> None:
    launcher = (bootstrap.ROOT / "START_MCP.bat").read_text(encoding="utf-8")
    assert "setlocal EnableDelayedExpansion" in launcher
    assert '"%~dp0.venv\\Scripts\\python.exe" -m scripts.bootstrap --start' in launcher
    assert '& set "MCP_EXIT_CODE=!ERRORLEVEL!" & call;' in launcher


def test_mode_rejects_unknown_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_START_MODE", "mystery")
    with pytest.raises(bootstrap.BootstrapError) as error:
        bootstrap._mode()
    assert error.value.code == 16


def test_control_plane_key_loads_from_portable_secret_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONTROL_PLANE_API_KEY", raising=False)
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    secret = tmp_path / ".secrets" / "control_plane_api_key.txt"
    secret.parent.mkdir()
    secret.write_text("secret-value\n", encoding="utf-8")
    bootstrap._load_control_plane_key()
    assert os.environ["CONTROL_PLANE_API_KEY"] == "secret-value"


def test_control_plane_key_strips_utf8_bom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONTROL_PLANE_API_KEY", raising=False)
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    secret = tmp_path / ".secrets" / "control_plane_api_key.txt"
    secret.parent.mkdir()
    secret.write_bytes(b"\xef\xbb\xbfsecret-with-bom\r\n")
    bootstrap._load_control_plane_key()
    assert os.environ["CONTROL_PLANE_API_KEY"] == "secret-with-bom"


def test_control_plane_key_handles_utf16_and_quotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONTROL_PLANE_API_KEY", raising=False)
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    secret = tmp_path / ".secrets" / "control_plane_api_key.txt"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_bytes('"secret-with-utf16-quotes"'.encode("utf-16"))
    bootstrap._load_control_plane_key()
    assert os.environ["CONTROL_PLANE_API_KEY"] == "secret-with-utf16-quotes"
    assert secret.read_bytes() == b"secret-with-utf16-quotes"


def test_missing_key_interactive_creates_secret_and_reuses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from types import SimpleNamespace

    monkeypatch.delenv("CONTROL_PLANE_API_KEY", raising=False)
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True)))
    prompts: list[str] = []

    def hidden_prompt(prompt: str) -> str:
        prompts.append(prompt)
        return "  test-key-secret  "

    monkeypatch.setattr(bootstrap.getpass, "getpass", hidden_prompt)
    bootstrap._load_control_plane_key()
    secret = tmp_path / ".secrets" / "control_plane_api_key.txt"
    assert secret.read_text(encoding="utf-8") == "test-key-secret"
    assert os.environ["CONTROL_PLANE_API_KEY"] == "test-key-secret"
    assert "test-key-secret" not in capsys.readouterr().out

    monkeypatch.delenv("CONTROL_PLANE_API_KEY")
    bootstrap._load_control_plane_key()
    assert len(prompts) == 1
    assert secret.read_text(encoding="utf-8") == "test-key-secret"


def test_missing_key_noninteractive_fails_without_creating_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    monkeypatch.delenv("CONTROL_PLANE_API_KEY", raising=False)
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: False)))
    with pytest.raises(bootstrap.BootstrapError) as error:
        bootstrap._load_control_plane_key()
    assert error.value.code == 17
    assert not (tmp_path / ".secrets").exists()


def test_empty_secret_file_is_filled_after_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    monkeypatch.delenv("CONTROL_PLANE_API_KEY", raising=False)
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True)))
    secret = tmp_path / ".secrets" / "control_plane_api_key.txt"
    secret.parent.mkdir()
    secret.write_text(" \n", encoding="utf-8")
    monkeypatch.setattr(bootstrap.getpass, "getpass", lambda _: "new-test-key")
    bootstrap._load_control_plane_key()
    assert secret.read_text(encoding="utf-8") == "new-test-key"


def test_empty_secret_file_changed_during_prompt_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    monkeypatch.delenv("CONTROL_PLANE_API_KEY", raising=False)
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True)))
    secret = tmp_path / ".secrets" / "control_plane_api_key.txt"
    secret.parent.mkdir()
    secret.write_text(" \n", encoding="utf-8")

    def prompt(_: str) -> str:
        secret.write_text("another-process-key", encoding="utf-8")
        return "new-test-key"

    monkeypatch.setattr(bootstrap.getpass, "getpass", prompt)
    with pytest.raises(bootstrap.BootstrapError) as error:
        bootstrap._load_control_plane_key()
    assert error.value.code == 17
    assert secret.read_text(encoding="utf-8") == "another-process-key"
    assert "CONTROL_PLANE_API_KEY" not in os.environ


def test_cancelled_key_prompt_never_creates_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    monkeypatch.delenv("CONTROL_PLANE_API_KEY", raising=False)
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True)))

    def cancel(_: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(bootstrap.getpass, "getpass", cancel)
    with pytest.raises(bootstrap.BootstrapError) as error:
        bootstrap._load_control_plane_key()
    assert error.value.code == 17
    assert not (tmp_path / ".secrets").exists()


def test_existing_key_never_prompts_or_changes_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONTROL_PLANE_API_KEY", raising=False)
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    secret = tmp_path / ".secrets" / "control_plane_api_key.txt"
    secret.parent.mkdir()
    secret.write_text("already-set", encoding="utf-8")
    monkeypatch.setattr(bootstrap.getpass, "getpass", lambda _: pytest.fail("unexpected prompt"))
    bootstrap._load_control_plane_key()
    assert secret.read_bytes() == b"already-set"


@pytest.mark.parametrize(
    "cached,doctor_exit,expected_dependency_calls,expected_full_calls",
    [
        (True, 0, 0, 0),
        (False, 0, 1, 1),
        (False, 9, 1, 1),
    ],
)
def test_validate_fast_and_full_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cached: bool,
    doctor_exit: int,
    expected_dependency_calls: int,
    expected_full_calls: int,
) -> None:
    monkeypatch.setenv("MCP_START_MODE", "local-http")
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "_repair_dependencies", lambda: False)
    dependency_calls: list[bool] = []
    full_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(bootstrap, "_doctor_check", lambda mode, profile: cached)
    monkeypatch.setattr(bootstrap, "_ensure_dependencies", lambda **kwargs: dependency_calls.append(True))

    def full_doctor(mode: str, profile: str) -> None:
        full_calls.append((mode, profile))
        if doctor_exit:
            raise bootstrap.BootstrapError("doctor failed", 15)

    monkeypatch.setattr(bootstrap, "_full_doctor", full_doctor)

    if doctor_exit:
        with pytest.raises(bootstrap.BootstrapError) as error:
            bootstrap.validate()
        assert error.value.code == 15
    else:
        assert bootstrap.validate() == ("local-http", "default")

    assert len(dependency_calls) == expected_dependency_calls
    assert len(full_calls) == expected_full_calls
