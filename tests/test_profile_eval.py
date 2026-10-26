"""Safety and reproducibility gates for the offline model-profile evaluation manifest."""
from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path

import pytest

from scripts.profile_eval import (
    MODEL_PROFILES,
    PROFILE_TASKS,
    build_schedule,
    classify_result,
    prepare_fixture_roots,
    summarize_results,
    validate_fixture_roots,
)


def test_manifest_is_balanced_and_repeatable() -> None:
    left = build_schedule()
    right = build_schedule()
    assert left == right
    assert len(left) == 180
    assert len(PROFILE_TASKS) == 20
    assert len({task.task_id for task in PROFILE_TASKS}) == 20
    assert set(MODEL_PROFILES) == {"full", "coding", "coding_core"}
    assert Counter((entry.task_id, entry.profile) for entry in left) == {
        (task.task_id, profile): 3
        for task in PROFILE_TASKS
        for profile in MODEL_PROFILES
    }
    for task in PROFILE_TASKS:
        assert {entry.repetition for entry in left if entry.task_id == task.task_id} == {1, 2, 3}
    for task in PROFILE_TASKS:
        for repetition in (1, 2, 3):
            matches = [row for row in left if row.task_id == task.task_id and row.repetition == repetition]
            assert len(matches) == 3
            assert {row.order_index for row in matches} == {0, 1, 2}


def test_manifest_profiles_rotate_first_position() -> None:
    schedule = build_schedule()
    first_profiles = [
        next(row.profile for row in schedule if row.task_id == task.task_id and row.repetition == repetition and row.order_index == 0)
        for task in PROFILE_TASKS
        for repetition in (1, 2, 3)
    ]
    assert Counter(first_profiles) == {profile: 20 for profile in MODEL_PROFILES}


def test_scoring_requires_verified_expected_mcp_call() -> None:
    assert classify_result({
        "terminal_status": "SUCCESS",
        "exit_code": 0,
        "observed_tool_names": ["project_summary"],
        "expected_tool": "project_summary",
        "response_is_correct": True,
        "permission_denied": False,
    }) == "PASS"
    for change in (
        {"terminal_status": "TIMEOUT"},
        {"exit_code": 1},
        {"observed_tool_names": ["read_file"]},
        {"observed_tool_names": ["call_mcp_tool"]},
        {"response_is_correct": False},
        {"response_is_correct": None},
        {"permission_denied": True},
    ):
        evidence = {
            "terminal_status": "SUCCESS", "exit_code": 0,
            "observed_tool_names": ["project_summary"],
            "expected_tool": "project_summary",
            "response_is_correct": True, "permission_denied": False,
            **change,
        }
        assert classify_result(evidence) != "PASS"


def test_aggregate_rejects_partial_and_regressed_runs() -> None:
    rows = []
    for entry in build_schedule():
        rows.append({
            "profile": entry.profile,
            "task_id": entry.task_id,
            "repetition": entry.repetition,
            "terminal_status": "SUCCESS", "exit_code": 0,
            "observed_tool_names": [entry.expected_tool],
            "expected_tool": entry.expected_tool,
            "response_is_correct": True, "permission_denied": False,
            "elapsed_seconds": 1.0,
            "input_tokens": 200,
        })
    assert summarize_results(rows)["decision"] == "PILOT_ACCEPTABLE_NOT_PRODUCTION_GO"
    assert summarize_results(rows[:-1])["decision"] == "INCOMPLETE"
    for row in rows:
        if row["profile"] == "coding_core":
            row["elapsed_seconds"] = 1.1
    summary = summarize_results(rows)
    assert summary["decision"] == "NO_GO"
    assert summary["reasons"]


def test_malformed_or_duplicate_result_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        summarize_results([{
            "profile": "full", "task_id": "summary_languages", "repetition": 1,
        }] * 2)
    with pytest.raises(ValueError, match="unknown"):
        summarize_results([{"profile": "full", "task_id": "typo", "repetition": 1}])




def test_prepare_workspaces_uses_only_committed_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("print('example')\n", encoding="utf8")
    (repo / "tracked.txt").write_text("safe", encoding="utf8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "main.py", "tracked.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=test@example.invalid",
         "-c", "user.name=Unit Test", "commit", "-qm", "fixture"],
        check=True, capture_output=True,
    )
    (repo / "tracked.txt").write_text("uncommitted secret must not copy", encoding="utf8")
    (repo / "untracked.txt").write_text("untracked secret must not copy", encoding="utf8")
    monkeypatch.chdir(tmp_path)
    roots = prepare_fixture_roots(repo, Path("snapshots"))
    assert len(roots) == 3
    for profile in MODEL_PROFILES:
        ws = Path(roots[profile])
        assert (ws / "tracked.txt").read_text(encoding="utf8") == "safe"
        assert not (ws / "untracked.txt").exists()
        assert not (ws / ".git").exists()
        config = __import__("json").loads((ws / ".agents/mcp_config.json").read_text(encoding="utf8"))
        data = config["mcpServers"]["computerpilot"]
        assert data["env"]["MCP_TOOL_PROFILE"] == profile
        assert Path(data["args"][0]) == ws / "main.py"
        assert data["env"]["MCP_EXTENSIONS_ENABLED"] == "false"
        assert data["env"]["MCP_WORKFLOW_DAG_ENABLED"] == "false"
    with pytest.raises(ValueError, match="must_not_exist"):
        prepare_fixture_roots(repo, Path("snapshots"))


def test_fixture_validation_is_read_only_and_identical(tmp_path: Path) -> None:
    base = tmp_path / "pilot"
    for profile in MODEL_PROFILES:
        workspace = base / profile
        (workspace / ".agents").mkdir(parents=True)
        (workspace / ".agents" / "mcp_config.json").write_text("{}", encoding="utf8")
        (workspace / "fixture_app.py").write_text("def value(): return 1\n", encoding="utf8")
    roots = validate_fixture_roots(base)
    assert set(roots) == set(MODEL_PROFILES)
    (base / "full" / "fixture_app.py").write_text("changed", encoding="utf8")
    with pytest.raises(ValueError, match="mismatch"):
        validate_fixture_roots(base)
