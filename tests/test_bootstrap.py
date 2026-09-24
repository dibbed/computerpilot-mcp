from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from scripts import bootstrap


def test_requirements_fingerprint_includes_nested_files(tmp_path: Path) -> None:
    root = tmp_path / "requirements.txt"
    child = tmp_path / "child.txt"
    root.write_text("-r child.txt\n", encoding="utf-8")
    child.write_text("demo==1\n", encoding="utf-8")
    before = bootstrap.requirements_fingerprint(root)
    child.write_text("demo==2\n", encoding="utf-8")
    after = bootstrap.requirements_fingerprint(root)
    assert before != after


def test_supervisor_command_is_platform_neutral() -> None:
    assert bootstrap._supervisor_command("local-http", "default") == [
        sys.executable,
        "-m",
        "scripts.supervisor",
        "--mode",
        "local-http",
    ]
    assert bootstrap._supervisor_command("tunnel", "work")[-2:] == ["--profile", "work"]


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


@pytest.mark.parametrize(
    "cached,doctor_exit,expected_dependency_calls,expected_full_calls",
    [
        (True, 0, 0, 0),
        (False, 0, 1, 1),
        (False, 9, 1, 1),
    ],
)
def test_validate_fast_and_full_paths(
    monkeypatch: pytest.MonkeyPatch,
    cached: bool,
    doctor_exit: int,
    expected_dependency_calls: int,
    expected_full_calls: int,
) -> None:
    monkeypatch.setenv("MCP_START_MODE", "local-http")
    dependency_calls: list[bool] = []
    full_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(bootstrap, "_doctor_check", lambda mode, profile: cached)
    monkeypatch.setattr(bootstrap, "_ensure_dependencies", lambda: dependency_calls.append(True))

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
