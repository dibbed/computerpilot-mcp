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
