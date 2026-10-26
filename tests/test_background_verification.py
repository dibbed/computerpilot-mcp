from __future__ import annotations

import asyncio
import json
import subprocess
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


def _git_fixture(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def test_background_preserves_rename_import_owners_without_delayed_git_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import verification_worker
    from tools.testing import verification

    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "app" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "app" / "original.py").write_text("value=1\n", encoding="utf-8")
    (repo / "tests" / "test_consumer.py").write_text("from app.original import value\n", encoding="utf-8")
    _git_fixture(repo, "init", "-q")
    _git_fixture(repo, "-c", "user.name=Tests", "-c", "user.email=tests@example.invalid", "add", ".")
    _git_fixture(repo, "-c", "user.name=Tests", "-c", "user.email=tests@example.invalid", "commit", "-qm", "initial")
    _git_fixture(repo, "mv", "app/original.py", "app/renamed.py")
    direct = verification.build_verification_plan(repo)
    expected = {item["path"] for item in direct["affected"]["tests"]}
    assert direct["changed_paths"] == ["app/renamed.py"]
    assert "tests/test_consumer.py" in expected
    commands: list[list[str]] = []
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")

    def submit(command: list[str], *args: Any, **kwargs: Any) -> dict[str, str]:
        commands.append(command)
        return {"job_id": "rename"}

    monkeypatch.setattr(store, "submit", submit)
    submit_verification(store, VerificationRequest(repo=str(repo), checks=["syntax"]), "rename")
    monkeypatch.setattr(verification, "discover_changed_paths", lambda *a, **kw: pytest.fail("rediscovered delayed Git paths"))
    selected: set[str] = set()

    def verify(root: Path, **options: Any) -> dict[str, Any]:
        plan = verification.build_verification_plan(root, changed_paths=options["changed_paths"])
        selected.update(item["path"] for item in plan["affected"]["tests"])
        return {"ok": True, "required_unknown": False, "stages": [], "mode": plan["mode"], "duration_ms": 1}

    monkeypatch.setattr(verification_worker, "verify_changed_repository", verify)
    assert verification_worker.run_request(commands[0][-1]) == 0
    capsys.readouterr()
    assert selected == expected


def test_background_pins_resolved_base_before_delayed_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("docs", encoding="utf-8")
    _git_fixture(repo, "init", "-q")
    _git_fixture(repo, "add", ".")
    _git_fixture(repo, "-c", "user.name=Tests", "-c", "user.email=tests@example.invalid", "commit", "-qm", "initial")
    _git_fixture(repo, "branch", "baseline")
    expected = _git_fixture(repo, "rev-parse", "HEAD")
    commands: list[list[str]] = []
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")

    def submit(command: list[str], *args: Any, **kwargs: Any) -> dict[str, str]:
        commands.append(command)
        return {"job_id": "base"}

    monkeypatch.setattr(store, "submit", submit)
    submit_verification(store, VerificationRequest(repo=str(repo), changed_paths=["README.md"], base="baseline"), "base")
    _git_fixture(repo, "-c", "user.name=Tests", "-c", "user.email=tests@example.invalid", "commit", "--allow-empty", "-qm", "later")
    _git_fixture(repo, "branch", "-f", "baseline", "HEAD")
    assert _git_fixture(repo, "rev-parse", "baseline") != expected
    request = VerificationRequest.model_validate_json(commands[0][-1])
    assert request.base == expected


def test_old_request_defaults_to_python_and_keeps_pins_out_of_options() -> None:
    request = VerificationRequest.model_validate_json('{"repo":"."}')
    assert request.test_backend == "python"
    assert request.runner_specs == ()
    assert "runner_specs" not in request.options()
    assert "snapshot_input_paths" not in request.options()


def test_background_submission_uses_target_scope_and_isolated_trusted_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("x=1\n", encoding="utf-8")
    captured: dict[str, Any] = {}
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")

    def submit(command: list[str], cwd: Path, *args: Any, **kwargs: Any) -> dict[str, str]:
        captured.update(command=command, cwd=cwd)
        return {"job_id": "trusted"}

    monkeypatch.setattr(store, "submit", submit)
    submit_verification(store, VerificationRequest(repo=str(repo), changed_paths=["app.py"], checks=["syntax"]), "trusted")
    assert captured["cwd"] == repo.resolve()
    assert captured["command"][1:3] == ["-I", "-c"]
    request = VerificationRequest.model_validate_json(captured["command"][-1])
    assert request.changed_paths == ["app.py"]
    assert request.snapshot_input_paths == ["app.py"]


def test_worker_forwards_original_pins_and_uncertainty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import verification_worker

    request = VerificationRequest(repo=str(tmp_path), source_fingerprint=source_fingerprint(tmp_path))
    captured: dict[str, Any] = {}

    def check(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": False, "required_unknown": True, "execution_uncertain": True,
                "stages": [], "mode": "focused", "duration_ms": 1}

    monkeypatch.setattr(verification_worker, "verify_changed_repository", check)
    assert verification_worker.run_request(request.model_dump_json()) == 1
    assert captured["runner_specs"] == ()
    assert captured["test_backend"] == "python"
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["execution_uncertain"] is True


@pytest.mark.parametrize("executed_unknown", [False, True])
def test_worker_keeps_execution_uncertainty_when_final_snapshot_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], executed_unknown: bool,
) -> None:
    from scripts import verification_worker

    request = VerificationRequest(repo=str(tmp_path), source_fingerprint=source_fingerprint(tmp_path))
    snapshots = 0

    def snapshot(*args: Any, **kwargs: Any) -> str:
        nonlocal snapshots
        snapshots += 1
        if snapshots > 1:
            raise ValueError("snapshot failed")
        return request.source_fingerprint

    def verify(*args: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs["stage_callback"]("stage_finished", {"name": "pytest_full", "status": "passed"})
        return {"ok": not executed_unknown, "required_unknown": executed_unknown, "execution_uncertain": executed_unknown,
                "stages": [], "mode": "focused", "duration_ms": 1}

    monkeypatch.setattr(verification_worker, "source_fingerprint", snapshot)
    monkeypatch.setattr(verification_worker, "native_test_dispatched", lambda: True)
    monkeypatch.setattr(verification_worker, "verify_changed_repository", verify)
    assert verification_worker.run_request(request.model_dump_json()) == 1
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["freshness"] == "unknown"
    assert result.get("execution_uncertain", False) is executed_unknown


def test_worker_reports_uncertainty_if_dispatched_stage_loses_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from types import SimpleNamespace

    from scripts import verification_worker

    request = VerificationRequest(repo=str(tmp_path), source_fingerprint=source_fingerprint(tmp_path))

    def dumps(value: Any, **kwargs: Any) -> str:
        if value.get("event") == "stage_finished":
            raise ValueError("callback output failed")
        return json.dumps(value, **kwargs)

    def verify(*args: Any, **kwargs: Any) -> None:
        kwargs["stage_callback"]("stage_finished", {"name": "pytest_full", "status": "passed"})

    monkeypatch.setattr(verification_worker, "native_test_dispatched", lambda: True, raising=False)
    monkeypatch.setattr(verification_worker, "json", SimpleNamespace(dumps=dumps))
    monkeypatch.setattr(verification_worker, "verify_changed_repository", verify)
    assert verification_worker.run_request(request.model_dump_json()) == 1
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["execution_uncertain"] is True


def test_external_repository_worker_cannot_shadow_trusted_modules(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    marker = tmp_path / "untrusted-import"
    malicious = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nraise RuntimeError('UNTRUSTED_MODULE_CANARY')\n"
    (repo / "sitecustomize.py").write_text(malicious, encoding="utf-8")
    for package in ("scripts", "core"):
        (repo / package).mkdir()
        (repo / package / "__init__.py").write_text(malicious, encoding="utf-8")
    (repo / "app.py").write_text("x=1\n", encoding="utf-8")
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")
    job = submit_verification(store, VerificationRequest(
        repo=str(repo), changed_paths=["app.py"], checks=["syntax"], total_timeout_sec=20,
    ), "external-shadow")
    deadline = time.monotonic() + 30
    while store.get(job["job_id"])["status"] in {"queued", "running"}:
        assert time.monotonic() < deadline, store.output(job["job_id"])
        time.sleep(0.03)
    output = store.output(job["job_id"])
    assert store.get(job["job_id"])["status"] == "succeeded", output
    assert not marker.exists()
    assert "UNTRUSTED_MODULE_CANARY" not in json.dumps(output)


def test_snapshot_tracks_selected_installed_inputs_without_changing_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import verification_worker
    from tools.testing import verification
    from tools.testing.project_runners import RunnerRuntime, RunnerSpec

    repo = tmp_path / "repo"
    installed = repo / "node_modules" / "jest"
    installed.mkdir(parents=True)
    entrypoint = installed / "cli.js"
    entrypoint.write_text("// approved", encoding="utf-8")
    metadata = installed / "package.json"
    metadata.write_text('{"name":"jest"}', encoding="utf-8")
    runtime = tmp_path / "node.exe"
    runtime.write_bytes(b"pinned runtime")
    spec = RunnerSpec(
        "jest", repo, "f" * 64, ("jest.config.json",), entrypoint, "1",
        ("installed:node_modules/jest/package.json",), True, (), workspace_root=repo,
        workspace_id="approved-workspace", runtime=RunnerRuntime(runtime, "a" * 64, runtime), entrypoint_sha256="b" * 64,
    )
    monkeypatch.setattr(verification, "bind_verification_runners", lambda *args: (spec,))
    commands: list[list[str]] = []
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")

    def submit(command: list[str], *args: Any, **kwargs: Any) -> dict[str, str]:
        commands.append(command)
        return {"job_id": "stale"}

    monkeypatch.setattr(store, "submit", submit)
    submit_verification(store, VerificationRequest(
        repo=str(repo), changed_paths=["src/app.js"], checks=["pytest"], test_backend="auto",
    ), "stale")
    request = VerificationRequest.model_validate_json(commands[0][-1])
    assert request.changed_paths == ["src/app.js"]
    assert {"jest.config.json", "node_modules/jest/package.json", "node_modules/jest/cli.js"} <= set(request.snapshot_input_paths or [])
    assert request.runner_specs == (spec,)
    metadata.write_text('{"name":"changed"}', encoding="utf-8")
    monkeypatch.setattr(verification_worker, "verify_changed_repository", lambda *a, **kw: pytest.fail("stale runner was dispatched"))
    assert verification_worker.run_request(commands[0][-1]) == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["freshness"] == "stale"


@pytest.mark.parametrize("options", [{"stage_timeout_sec": 601}, {"total_timeout_sec": 1801}])
def test_auto_background_rejects_runner_budget_above_native_caps(options: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="auto verification"):
        VerificationRequest(repo=".", test_backend="auto", **options)


@pytest.mark.parametrize("backend", [None, "auto"])
@pytest.mark.parametrize("tool", ["verify_changes", "start_verification"])
def test_registered_verification_tools_forward_backend_and_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str | None, tool: str,
) -> None:
    from collections.abc import Iterator
    from contextlib import contextmanager

    captured: dict[str, Any] = {}
    admissions = 0
    admission_budgets: list[float] = []

    @contextmanager
    def claims(*args: Any, **kwargs: Any) -> Iterator[None]:
        nonlocal admissions
        admissions += 1
        admission_budgets.append(kwargs["timeout_sec"])
        time.sleep(0.02)
        yield

    def verify(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "ok": True, "mode": "focused", "stages": [], "duration_ms": 1,
            "required_unknown": False, "changed_paths": [], "affected": {},
            "execution_uncertain": False, "tests_executed": False, "claim_testing": False,
        }

    def submit(store: Any, request: VerificationRequest, *args: Any) -> dict[str, Any]:
        captured.update(request.options())
        return {"accepted": True}

    monkeypatch.setattr(registry, "managed_resource_claims", claims)
    monkeypatch.setattr(registry, "verify_changed_repository", verify)
    monkeypatch.setattr(registry, "submit_verification", submit)
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")
    server = MCPServer("verification-forwarding")
    registry.register(server, background_enabled=True, store_getter=lambda: store)

    async def scenario() -> None:
        args: dict[str, Any] = {"repo": str(tmp_path), "checks": ["syntax"], "total_timeout_sec": 1}
        if backend is not None:
            args["test_backend"] = backend
        if tool == "start_verification":
            args["idempotency_key"] = "forwarding"
        async with Client(server, raise_exceptions=True) as client:
            response = (await client.call_tool(tool, args)).structured_content
            assert response and (response.get("ok") or response.get("accepted"))

    asyncio.run(scenario())
    assert captured["test_backend"] == (backend or "python")
    if tool == "verify_changes":
        assert admissions == 1
        assert 0 < admission_budgets[0] <= 1
        assert 0 < captured["total_timeout_sec"] < admission_budgets[0]


@pytest.mark.parametrize("tool", ["verify_changes", "start_verification"])
def test_registered_auto_budget_is_rejected_before_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str,
) -> None:
    monkeypatch.setattr(registry, "managed_resource_claims", lambda *args, **kwargs: pytest.fail("admitted invalid budget"))
    monkeypatch.setattr(registry, "submit_verification", lambda *args, **kwargs: pytest.fail("queued invalid budget"))
    server = MCPServer("verification-budget")
    registry.register(server, background_enabled=True)

    async def scenario() -> None:
        args: dict[str, Any] = {"repo": str(tmp_path), "test_backend": "auto", "stage_timeout_sec": 601}
        if tool == "start_verification":
            args["idempotency_key"] = "bad-budget"
        async with Client(server, raise_exceptions=True) as client:
            response = (await client.call_tool(tool, args)).structured_content
            assert response and response["ok"] is False

    asyncio.run(scenario())


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


def test_fingerprint_frames_file_contents_unambiguously(tmp_path: Path) -> None:
    first = tmp_path / "a"
    first.write_bytes(b"first\0b\0second")
    before = source_fingerprint(tmp_path)
    first.write_bytes(b"first")
    (tmp_path / "b").write_bytes(b"second")
    assert source_fingerprint(tmp_path) != before


@pytest.mark.parametrize("directory", [".worktrees", ".playwright-cli", ".pytest-tmp-session", ".codebase-memory-test"])
@pytest.mark.parametrize("change_when", ["before", "during", "deleted", "created", "unchanged"])
def test_explicit_excluded_input_is_bound_to_submitted_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    directory: str, change_when: str,
) -> None:
    from scripts import verification_worker

    repo = tmp_path / "repo"
    source = repo / directory / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("x=1\n", encoding="utf-8")
    if change_when == "created":
        source.unlink()
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")
    commands: list[list[str]] = []

    def submit(command: list[str], *args: object, **kwargs: object) -> dict[str, str]:
        commands.append(command)
        return {"job_id": "snapshot-test"}

    monkeypatch.setattr(store, "submit", submit)
    submit_verification(store, VerificationRequest(
        repo=str(repo), changed_paths=[f"{directory}/app.py"], checks=["syntax"],
    ), "snapshot-test")
    payload = commands[0][-1]
    dispatched = False

    def check(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal dispatched
        dispatched = True
        if change_when == "during":
            source.write_text("x=2\n", encoding="utf-8")
        return {"ok": True, "required_unknown": False, "stages": [], "mode": "focused", "duration_ms": 1}

    monkeypatch.setattr(verification_worker, "verify_changed_repository", check)
    if change_when in {"before", "created"}:
        source.write_text("x=2\n", encoding="utf-8")
    elif change_when == "deleted":
        source.unlink()
    assert verification_worker.run_request(payload) == (0 if change_when == "unchanged" else 1)
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["freshness"] == ("current" if change_when == "unchanged" else "stale")
    assert result["ok"] is (change_when == "unchanged")
    assert dispatched is (change_when in {"during", "unchanged"})


@pytest.mark.parametrize("expire_after_last_directory", [False, True])
def test_fingerprint_enforces_deadline_when_only_directories_are_visited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expire_after_last_directory: bool,
) -> None:
    now = [0.0]

    def walk(*args: object, **kwargs: object) -> Any:
        yield str(tmp_path), [], []
        now[0] = 11.0
        if not expire_after_last_directory:
            yield str(tmp_path / "empty"), [], []

    monkeypatch.setattr(background.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(background.os, "walk", walk)
    with pytest.raises(ValueError, match="budget"):
        source_fingerprint(tmp_path)


@pytest.mark.parametrize("limit", ["MAX_INPUT_FILES", "MAX_INPUT_BYTES"])
def test_explicit_excluded_inputs_share_snapshot_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str,
) -> None:
    (tmp_path / "app.py").write_bytes(b"xx")
    extra = tmp_path / ".worktrees" / "extra.py"
    extra.parent.mkdir()
    extra.write_bytes(b"xx")
    monkeypatch.setattr(background, limit, 1 if limit == "MAX_INPUT_FILES" else 3)
    with pytest.raises(ValueError, match="budget"):
        source_fingerprint(tmp_path, changed_paths=[".worktrees/extra.py"])


def test_explicit_scanned_inputs_are_not_counted_twice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_bytes(b"xx")
    monkeypatch.setattr(background, "MAX_INPUT_FILES", 1)
    before = source_fingerprint(tmp_path)
    assert source_fingerprint(tmp_path, changed_paths=["app.py", "./app.py"]) == before


def test_explicit_excluded_input_stat_error_is_not_treated_as_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    extra = tmp_path / ".worktrees" / "extra.py"
    extra.parent.mkdir()
    extra.write_text("x=1\n", encoding="utf-8")
    original = Path.stat

    def denied(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == extra:
            raise PermissionError("unreadable explicit input")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(PermissionError):
        source_fingerprint(tmp_path, changed_paths=[".worktrees/extra.py"])


@pytest.mark.parametrize("link_method", ["is_symlink", "is_junction"])
def test_explicit_excluded_inputs_reject_linked_ancestors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, link_method: str,
) -> None:
    extra = tmp_path / ".worktrees" / "extra.py"
    extra.parent.mkdir()
    extra.write_text("x=1\n", encoding="utf-8")
    original = getattr(Path, link_method, lambda self: False)
    monkeypatch.setattr(Path, link_method, lambda self: self == extra.parent or original(self), raising=False)
    with pytest.raises(ValueError, match="symlink"):
        source_fingerprint(tmp_path, changed_paths=[".worktrees/extra.py"])


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
        f"    pending = Path({str(marker.with_suffix('.pending'))!r})\n"
        "    pending.write_text(str(os.getpid()))\n"
        f"    pending.replace(Path({str(marker)!r}))\n"
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
    from core.workflow_store import WorkflowStore

    capacity = WorkflowStore(store.path.parent / "workflows.sqlite3", recover=False)
    assert capacity.health_summary()["active_analyzer_capacity_weight"] == 0


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
