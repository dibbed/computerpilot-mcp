from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer
from pydantic import ValidationError

from core.jobs import JobStore
from tools.jobs import registry as jobs_registry
from tools.testing import background, registry
from tools.testing.background import VerificationRequest, source_fingerprint, submit_verification


def test_request_rejects_unsafe_parallel_modes() -> None:
    with pytest.raises(ValueError, match="parallel"):
        VerificationRequest(repo=".", max_parallel_checks=2)
    with pytest.raises(ValueError, match="parallel"):
        VerificationRequest(repo=".", max_parallel_checks=2, fail_fast=False, ruff_fix=True)


def test_fingerprint_tracks_inputs_and_ignores_test_cache(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("x = 1\n", encoding="utf-8")
    before = source_fingerprint(tmp_path)
    cache = tmp_path / ".pytest_cache"
    cache.mkdir()
    (cache / "state").write_text("generated", encoding="utf-8")
    assert source_fingerprint(tmp_path) == before
    source.write_text("x = 2\n", encoding="utf-8")
    assert source_fingerprint(tmp_path) != before


def test_rejected_request_never_submits(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")
    repo = tmp_path / "repo"
    repo.mkdir()
    request = VerificationRequest(repo=str(repo), changed_paths=["../outside.py"])
    with pytest.raises(ValueError, match="inside"):
        submit_verification(store, request, "bad-path")
    assert store.list(0, 10)["total_count"] == 0


def test_start_returns_before_test_finishes_and_survives_client_reconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    started, release = tmp_path / "started", tmp_path / "release"
    (repo / "test_slow.py").write_text(
        "from pathlib import Path\nimport time\n"
        "def test_barrier():\n"
        f"    Path({str(started)!r}).write_text('started')\n"
        "    deadline = time.monotonic() + 20\n"
        f"    while not Path({str(release)!r}).exists():\n"
        "        assert time.monotonic() < deadline\n"
        "        time.sleep(0.02)\n",
        encoding="utf-8",
    )
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")
    monkeypatch.setattr(registry, "JobStore", lambda: store)
    monkeypatch.setattr(jobs_registry, "JobStore", lambda: store)

    def server() -> MCPServer:
        result = MCPServer("background-verification")
        registry.register(result, background_enabled=True)
        jobs_registry.register(result)

        @result.tool()
        def independent_read() -> dict[str, bool]:
            return {"ok": True}

        return result

    async def scenario() -> None:
        arguments = {
            "repo": str(repo), "changed_paths": ["test_slow.py"], "checks": ["pytest"],
            "idempotency_key": "barrier-test", "total_timeout_sec": 30,
        }
        async with Client(server(), raise_exceptions=True) as client:
            response = (await client.call_tool("start_verification", arguments)).structured_content
            assert response and response["accepted"] and not release.exists()
            job_id = response["job_id"]
            duplicate = (await client.call_tool("start_verification", arguments)).structured_content
            assert duplicate and duplicate["job_id"] == job_id and duplicate["deduplicated"]
            deadline = time.monotonic() + 20
            while not started.exists():
                assert time.monotonic() < deadline, store.get(job_id)
                await asyncio.sleep(0.03)
            assert (await client.call_tool("independent_read", {})).structured_content == {"ok": True}
            assert store.get(job_id)["status"] == "running"
        release.write_text("go", encoding="utf-8")
        async with Client(server(), raise_exceptions=True) as client:
            while True:
                state = (await client.call_tool("job_status", {"job_id": job_id})).structured_content
                assert state
                if state["status"] not in {"queued", "running"}:
                    break
                assert time.monotonic() < deadline
                await asyncio.sleep(0.05)
            output = (await client.call_tool("job_output", {"job_id": job_id})).structured_content
            assert output
            assert state["status"] == "succeeded", output
            rows = [json.loads(line) for line in output["stdout"]["text"].splitlines()]
            assert rows[0]["event"] == "started"
            assert rows[-1]["event"] == "result"
            assert rows[-1]["ok"] and rows[-1]["freshness"] == "current"
            assert all(row["schema_version"] == 1 for row in rows)

    try:
        asyncio.run(scenario())
    finally:
        release.write_text("go", encoding="utf-8")


def test_profiles_require_jobs_for_background_tool() -> None:
    from dataclasses import replace

    from core import registry as core_registry

    original = core_registry.SETTINGS

    async def names(profile: str) -> set[str]:
        core_registry.SETTINGS = replace(original, tool_profile=profile)
        return {tool.name for tool in await core_registry.create_server().list_tools()}

    try:
        assert "start_verification" in asyncio.run(names("coding"))
        assert "start_verification" not in asyncio.run(names("testing"))
        assert "start_verification" not in asyncio.run(names("git"))
    finally:
        core_registry.SETTINGS = original


def test_worker_rejects_stale_source_without_running_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import verification_worker

    source = tmp_path / "app.py"
    source.write_text("x=1\n", encoding="utf-8")
    request = VerificationRequest(repo=str(tmp_path), source_fingerprint=source_fingerprint(tmp_path))
    source.write_text("x=2\n", encoding="utf-8")
    monkeypatch.setattr(verification_worker, "verify_changed_repository", lambda *a, **kw: pytest.fail("dispatched stale input"))
    assert verification_worker.run_request(request.model_dump_json()) == 1
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[-1]["freshness"] == "stale" and rows[-1]["required_unknown"]


def test_worker_marks_changes_during_execution_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import verification_worker

    source = tmp_path / "app.py"
    source.write_text("x=1\n", encoding="utf-8")
    request = VerificationRequest(repo=str(tmp_path), source_fingerprint=source_fingerprint(tmp_path))

    def check(*args: object, **kwargs: object) -> dict[str, object]:
        source.write_text("x=2\n", encoding="utf-8")
        return {"ok": True, "required_unknown": False, "stages": [], "mode": "focused", "duration_ms": 1}

    monkeypatch.setattr(verification_worker, "verify_changed_repository", check)
    assert verification_worker.run_request(request.model_dump_json()) == 1
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["freshness"] == "stale" and not result["ok"]


def test_invalid_worker_request_does_not_echo_payload(capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.verification_worker import run_request

    assert run_request('{"credential_canary":"DO_NOT_ECHO_CANARY"}') == 1
    output = capsys.readouterr()
    assert "DO_NOT_ECHO_CANARY" not in output.out + output.err
    result = json.loads(output.out)
    assert not result["ok"] and result["required_unknown"]


def test_oversized_request_is_rejected_before_planning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.testing import background

    request = VerificationRequest(repo=str(tmp_path), changed_paths=[f"{n:04d}" + "a" * 100 for n in range(700)])
    monkeypatch.setattr(background, "build_verification_plan", lambda *a, **kw: pytest.fail("planned oversized request"))
    with pytest.raises(ValueError, match="size"):
        submit_verification(JobStore(tmp_path / ".agent_state" / "jobs.sqlite3"), request, "oversized")


def test_cancel_running_verification_reaps_test_process(tmp_path: Path) -> None:
    import psutil

    repo = tmp_path / "repo"
    repo.mkdir()
    marker = tmp_path / "test-pid"
    (repo / "test_cancel.py").write_text(
        "import os,time\nfrom pathlib import Path\ndef test_wait():\n"
        f"    Path({str(marker)!r}).write_text(str(os.getpid()))\n"
        "    time.sleep(60)\n", encoding="utf-8",
    )
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")
    request = VerificationRequest(repo=str(repo), changed_paths=["test_cancel.py"], checks=["pytest"], total_timeout_sec=30)
    job = submit_verification(store, request, "cancel-test")
    deadline = time.monotonic() + 20
    while not marker.exists():
        assert time.monotonic() < deadline, store.output(job["job_id"])
        time.sleep(0.03)
    process = psutil.Process(int(marker.read_text(encoding="utf-8")))
    created = process.create_time()
    store.cancel(job["job_id"])
    while store.get(job["job_id"])["status"] in {"queued", "running"}:
        assert time.monotonic() < deadline
        time.sleep(0.03)
    assert store.get(job["job_id"])["status"] == "cancelled"
    from core.jobs import same_process

    assert not same_process(process.pid, created)


def test_worker_bounds_diagnostics_but_preserves_stage_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import verification_worker

    request = VerificationRequest(repo=str(tmp_path), source_fingerprint=source_fingerprint(tmp_path))
    monkeypatch.setattr(verification_worker, "verify_changed_repository", lambda *a, **kw: {
        "ok": False, "required_unknown": False, "mode": "full", "duration_ms": 1,
        "stages": [{"name": "pytest_full", "status": "failed", "diagnostics": ["x" * 4000] * 200}],
    })
    assert verification_worker.run_request(request.model_dump_json()) == 1
    lines = capsys.readouterr().out.splitlines()
    assert all(len(line.encode("utf-8")) + 1 <= verification_worker.MAX_EVENT_BYTES for line in lines)
    result = json.loads(lines[-1])
    assert result["stages"][0]["status"] == "failed" and result["output_truncated"]


def test_fingerprint_fails_closed_on_input_quota(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.testing import background

    (tmp_path / "app.py").write_text("xx", encoding="utf-8")
    monkeypatch.setattr(background, "MAX_INPUT_BYTES", 1)
    with pytest.raises(ValueError, match="budget"):
        source_fingerprint(tmp_path)


def test_idempotency_conflict_when_key_reused_after_source_change(tmp_path: Path) -> None:
    from core.errors import ToolError

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")
    request1 = VerificationRequest(repo=str(repo), changed_paths=["app.py"])
    first = submit_verification(store, request1, "key-1")
    assert first["accepted"] and not first["deduplicated"]

    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    request2 = VerificationRequest(repo=str(repo), changed_paths=["app.py"])
    with pytest.raises(ToolError) as exc_info:
        submit_verification(store, request2, "key-1")
    assert exc_info.value.code == "idempotency_conflict"


def test_fingerprint_rejects_directory_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_text("ok", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: True if self.name == "sub" else original_is_symlink(self))
    with pytest.raises(ValueError, match="symlink"):
        source_fingerprint(tmp_path)


def test_fingerprint_rejects_directory_junction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_text("ok", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(Path, "is_junction", lambda self: True if self.name == "sub" else False, raising=False)
    with pytest.raises(ValueError, match="symlink"):
        source_fingerprint(tmp_path)


def test_request_rejects_base_flag_injection() -> None:
    with pytest.raises(ValueError, match="base git ref"):
        VerificationRequest(repo=".", base="-main")
    with pytest.raises(ValueError, match="pattern"):
        VerificationRequest(repo=".", base="main; rm -rf /")


def test_request_rejects_ruff_fix() -> None:
    with pytest.raises(ValueError, match="background verification cannot mutate"):
        VerificationRequest(repo=".", ruff_fix=True)


def test_submit_verification_revalidates_changed_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")
    request = VerificationRequest(repo=str(repo), changed_paths=["app.py"])

    # Simulate impact analysis finding over 1,000 files
    monkeypatch.setattr(
        background,
        "build_verification_plan",
        lambda *a, **kw: {
            "changed_paths": [f"file_{i}.py" for i in range(1005)],
            "affected": {"decision": "none", "tests": []},
            "full": False,
            "mode": "checks_only",
            "python_targets": [],
        },
    )
    with pytest.raises(ValidationError):
        submit_verification(store, request, "large-paths-key")


def test_worker_emits_result_when_stream_quota_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import verification_worker

    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    request = VerificationRequest(repo=str(tmp_path), source_fingerprint=source_fingerprint(tmp_path))

    def exceed_stream_callback(*args: object, **kwargs: object) -> dict[str, Any]:
        cb = kwargs.get("stage_callback")
        if callable(cb):
            for i in range(100):
                cb("stage_finished", {"name": f"stage_{i}", "data": "y" * 1000})
        return {"ok": True, "mode": "full", "stages": [], "duration_ms": 1, "required_unknown": False}

    monkeypatch.setattr(verification_worker, "verify_changed_repository", exceed_stream_callback)
    monkeypatch.setattr(verification_worker, "MAX_STREAM_BYTES", 500)
    exit_code = verification_worker.run_request(request.model_dump_json())
    assert exit_code == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) >= 1
    last = json.loads(lines[-1])
    assert last["event"] == "result"
    assert last["ok"] is False
    assert last["required_unknown"] is True
