"""Cross-platform release validation gate used locally and by GitHub Actions."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from core.platform import detect_capabilities

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / ".agent_state" / "platform-validation"


def _run_command(
    label: str,
    command: list[str],
    *,
    timeout: int,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"{label}: {type(exc).__name__}") from exc
    elapsed = round(time.monotonic() - started, 3)
    if result.returncode:
        output = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"{label}: exit {result.returncode}: {output[-4000:]}"
        )
    return {
        "label": label,
        "elapsed_seconds": elapsed,
        "stdout_tail": result.stdout.strip()[-1000:],
    }


def _launcher_command() -> list[str]:
    if os.name == "nt":
        return ["cmd.exe", "/d", "/s", "/c", "START_MCP.bat"]
    return [str(ROOT / "start_mcp.sh")]


def run_validation(*, workflows: int = 60) -> dict[str, Any]:
    capabilities = detect_capabilities()
    if capabilities.system not in {"windows", "linux", "macos"}:
        raise RuntimeError(f"Unsupported validation host: {capabilities.system}")
    if capabilities.architecture not in {"amd64", "arm64"}:
        raise RuntimeError(f"Unsupported validation architecture: {capabilities.architecture}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["MCP_START_MODE"] = "local-http"
    environment["MCP_START_VALIDATE_ONLY"] = "1"
    environment["PYTHON_BIN"] = sys.executable

    steps: list[dict[str, Any]] = []
    if os.name != "nt":
        steps.append(_run_command("launcher syntax", ["sh", "-n", "start_mcp.sh"], timeout=30, env=environment))
    steps.append(_run_command("clean launcher validation", _launcher_command(), timeout=420, env=environment))
    steps.append(
        _run_command(
            "full local doctor",
            [sys.executable, "-m", "scripts.doctor", "--mode", "local-http"],
            timeout=240,
            env=environment,
        )
    )

    benchmark_path = REPORT_DIR / f"benchmark-{capabilities.platform_key}.json"
    benchmark_command = [
        sys.executable,
        "-m",
        "scripts.perf_benchmark",
        "--profile",
        "quick",
        "--runs",
        "1",
        "--suite",
        "catalog",
        "--suite",
        "startup",
        "--suite",
        "resilience",
        "--suite",
        "jobs",
        "--include-jobs",
        "--output",
        str(benchmark_path),
    ]
    steps.append(_run_command("quick platform benchmark", benchmark_command, timeout=420, env=environment))

    failures = max(1, workflows // 10)
    uncertain = max(1, workflows // 20)
    soak_command = [
        sys.executable,
        "-m",
        "scripts.workflow_soak",
        "--workflows",
        str(workflows),
        "--failures",
        str(failures),
        "--uncertain",
        str(uncertain),
        "--restarts",
        "2",
        "--durable-jobs",
        "3",
        "--json",
    ]
    steps.append(_run_command("workflow restart soak", soak_command, timeout=420, env=environment))

    return {
        "ok": True,
        "platform": capabilities.platform_key,
        "python": platform.python_version(),
        "capabilities": capabilities.as_dict(),
        "benchmark_report": str(benchmark_path),
        "workflow_count": workflows,
        "steps": steps,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=int, default=60)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.workflows < 10:
        parser.error("--workflows must be at least 10")
    try:
        report = run_validation(workflows=args.workflows)
    except RuntimeError as exc:
        print(f"FAIL platform validation: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
