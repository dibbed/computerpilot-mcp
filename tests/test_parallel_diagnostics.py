from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from core.registry import create_server
from core.resource_claims import coordination_claim, current_resource_capability, managed_resource_claims, path_claim
from core.workflow_store import WorkflowStore
from tools.testing import registry as testing_registry


@pytest.fixture(autouse=True)
def isolate_diagnostic_admission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(testing_registry, "SETTINGS", replace(testing_registry.SETTINGS, state_dir=tmp_path / "state"))


def _structured(result: Any) -> dict[str, Any]:
    payload = getattr(result, "structured_content", None)
    assert isinstance(payload, dict)
    return payload


def test_parallel_diagnostics_reuse_parent_resource_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(testing_registry.SETTINGS, state_dir=tmp_path / "state")
    monkeypatch.setattr(testing_registry, "SETTINGS", settings)
    resources = WorkflowStore(settings.workflow_db, recover=False)
    inherited: list[str | None] = []

    def runner(*args: object, **kwargs: object) -> dict[str, object]:
        inherited.append(current_resource_capability())
        return {"ok": True, "exit_code": 0, "diagnostics": []}

    monkeypatch.setattr(testing_registry, "run_ruff_summary", runner)
    monkeypatch.setattr(testing_registry, "run_mypy_summary", runner)

    async def scenario() -> dict[str, Any]:
        async with Client(create_server(), raise_exceptions=True) as client:
            return _structured(await client.call_tool("collect_diagnostics", {
                "cwd": str(tmp_path), "backends": ["ruff", "mypy"],
                "max_parallel_checks": 2, "total_timeout_sec": 0.5,
            }))

    with managed_resource_claims(
        (coordination_claim(), path_claim(tmp_path, "write", scope="subtree")),
        category="parent", store=resources,
    ) as capability:
        result = asyncio.run(scenario())
    assert result["ok"] is True
    assert inherited == [capability] * 2


def test_collect_diagnostics_parallelism_is_bounded_and_stage_order_is_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active = 0
    peak = 0
    calls = 0

    def runner(source: str) -> Callable[..., dict[str, Any]]:
        def run(*args: object, **kwargs: object) -> dict[str, Any]:
            nonlocal active, peak, calls
            with lock:
                active += 1
                peak = max(peak, active)
                calls += 1
                should_wait = calls <= 2
            try:
                if should_wait:
                    barrier.wait(timeout=5)
                return {
                    "ok": True,
                    "exit_code": 0,
                    "diagnostics": [
                        {
                            "file": f"{source}.py",
                            "line": 1,
                            "column": 1,
                            "severity": "warning",
                            "code": source.upper(),
                            "message": source,
                            "source": source,
                        }
                    ],
                }
            finally:
                with lock:
                    active -= 1

        return run

    monkeypatch.setattr(testing_registry, "run_ruff_summary", runner("ruff"))
    monkeypatch.setattr(testing_registry, "run_mypy_summary", runner("mypy"))
    monkeypatch.setattr(testing_registry, "run_pytest_summary", runner("pytest"))

    async def scenario() -> dict[str, Any]:
        async with Client(create_server(), raise_exceptions=True) as client:
            return _structured(
                await client.call_tool(
                    "collect_diagnostics",
                    {
                        "cwd": str(tmp_path),
                        "backends": ["ruff", "mypy", "pytest"],
                        "max_parallel_checks": 2,
                    },
                )
            )

    result = asyncio.run(scenario())
    assert result["ok"] is True
    assert peak == 2
    assert [stage["backend"] for stage in result["stages"]] == ["ruff", "mypy", "pytest"]
    assert {item["source"] for item in result["diagnostics"]} == {"ruff", "mypy", "pytest"}


def test_collect_diagnostics_defaults_to_serial_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = threading.Lock()
    active = 0
    peak = 0

    def run(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.01)
            return {"ok": True, "exit_code": 0, "diagnostics": []}
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(testing_registry, "run_ruff_summary", run)
    monkeypatch.setattr(testing_registry, "run_mypy_summary", run)
    monkeypatch.setattr(testing_registry, "run_pytest_summary", run)

    async def scenario() -> dict[str, Any]:
        async with Client(create_server(), raise_exceptions=True) as client:
            return _structured(await client.call_tool("collect_diagnostics", {"cwd": str(tmp_path)}))

    result = asyncio.run(scenario())
    assert result["ok"] is True
    assert peak == 1


def test_collect_diagnostics_preserves_siblings_when_one_backend_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> dict[str, Any]:
        raise RuntimeError("simulated analyzer crash")

    monkeypatch.setattr(testing_registry, "run_ruff_summary", boom)
    monkeypatch.setattr(
        testing_registry,
        "run_mypy_summary",
        lambda *args, **kwargs: {"ok": True, "exit_code": 0, "diagnostics": []},
    )

    async def scenario() -> dict[str, Any]:
        async with Client(create_server(), raise_exceptions=True) as client:
            return _structured(
                await client.call_tool(
                    "collect_diagnostics",
                    {
                        "cwd": str(tmp_path),
                        "backends": ["ruff", "mypy"],
                        "max_parallel_checks": 2,
                    },
                )
            )

    result = asyncio.run(scenario())
    assert result["ok"] is False
    assert result["required_unknown"] is True
    assert result["stages"][0]["backend"] == "ruff"
    assert result["stages"][0]["unavailable"] is True
    assert result["stages"][1]["backend"] == "mypy"
    assert result["stages"][1]["ok"] is True


def test_collect_diagnostics_total_deadline_prevents_late_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def slow_ruff(*args: object, **kwargs: object) -> dict[str, Any]:
        calls.append("ruff")
        time.sleep(0.05)
        return {"ok": True, "exit_code": 0, "diagnostics": []}

    def mypy(*args: object, **kwargs: object) -> dict[str, Any]:
        calls.append("mypy")
        return {"ok": True, "exit_code": 0, "diagnostics": []}

    monkeypatch.setattr(testing_registry, "run_ruff_summary", slow_ruff)
    monkeypatch.setattr(testing_registry, "run_mypy_summary", mypy)

    async def scenario() -> dict[str, Any]:
        async with Client(create_server(), raise_exceptions=True) as client:
            return _structured(
                await client.call_tool(
                    "collect_diagnostics",
                    {
                        "cwd": str(tmp_path),
                        "backends": ["ruff", "mypy"],
                        "max_parallel_checks": 1,
                        "total_timeout_sec": 0.02,
                    },
                )
            )

    result = asyncio.run(scenario())
    # Slow hosts may expire the deadline during resource admission, before Ruff starts.
    # In either valid case mypy must never be dispatched after the total deadline.
    assert calls in ([], ["ruff"])
    assert result["required_unknown"] is True
    if not calls:
        assert result["stages"][0]["timed_out"] is True
    assert result["stages"][1]["timed_out"] is True
