from __future__ import annotations

from typing import Any

from core.platform import PlatformCapabilities
from scripts import platform_validation


def _caps() -> PlatformCapabilities:
    return PlatformCapabilities(
        system="linux",
        architecture="amd64",
        process_tree_ownership=True,
        desktop_screenshot=False,
        desktop_input=False,
        semantic_ui=False,
        system_services=True,
        installed_software=True,
        powershell=False,
        posix_shell=True,
        secure_tunnel=True,
        browser=False,
    )


def test_platform_validation_runs_launcher_doctor_benchmark_and_soak(monkeypatch) -> None:
    commands: list[tuple[str, list[str]]] = []

    def fake_run(
        label: str,
        command: list[str],
        *,
        timeout: int,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        assert timeout > 0
        assert env is not None
        assert env["MCP_START_MODE"] == "local-http"
        assert env["MCP_START_VALIDATE_ONLY"] == "1"
        commands.append((label, command))
        return {"label": label, "elapsed_seconds": 0.01, "stdout_tail": ""}

    monkeypatch.setattr(platform_validation, "detect_capabilities", _caps)
    monkeypatch.setattr(platform_validation, "_run_command", fake_run)
    monkeypatch.setattr(platform_validation.os, "name", "posix", raising=False)
    monkeypatch.setattr(platform_validation, "_launcher_command", lambda: ["./start_mcp.sh"])

    result = platform_validation.run_validation(workflows=20)

    assert result["ok"] is True
    assert result["platform"] == "linux-amd64"
    assert [label for label, _ in commands] == [
        "launcher syntax",
        "clean launcher validation",
        "full local doctor",
        "quick platform benchmark",
        "workflow restart soak",
    ]
    benchmark = commands[3][1]
    assert "--include-jobs" in benchmark
    assert benchmark.count("--suite") == 4
    soak = commands[4][1]
    assert soak[soak.index("--workflows") + 1] == "20"
    assert soak[soak.index("--restarts") + 1] == "2"
    assert soak[soak.index("--durable-jobs") + 1] == "3"
