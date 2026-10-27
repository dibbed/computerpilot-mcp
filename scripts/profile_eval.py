"""Reproducible, fail-closed evaluation design for three ComputerPilot tool profiles.

The plan/analysis commands are offline and never call a model or change settings.
A live-model dataset must come from an independently authorized, isolated runner;
synthetic, incomplete or identity-ambiguous results are not P05-02 acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import statistics
import subprocess
import sys
import tarfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

MODEL_PROFILES = ("full", "coding", "coding_core")
PROFILE_TASKS: tuple[ProfileTask, ...]


@dataclass(frozen=True)
class ProfileTask:
    task_id: str
    expected_tool: str
    instruction: str


@dataclass(frozen=True)
class ScheduledTask:
    task_id: str
    expected_tool: str
    instruction: str
    repetition: int
    profile: str
    order_index: int


# Each prompt is read-only and targets the same immutable fixture path.
# They exercise tool selection, rather than only the model's claimed completion.
PROFILE_TASKS = (
    ProfileTask("summary_languages", "project_summary", "Identify the project's languages using project_summary."),
    ProfileTask("summary_python", "project_summary", "Use project_summary to determine whether the project contains Python."),
    ProfileTask("summary_powershell", "project_summary", "Use project_summary to check whether PowerShell files exist."),
    ProfileTask("summary_counts", "project_summary", "Use project_summary to report its language breakdown."),
    ProfileTask("read_function", "read_file", "Read fixture_app.py and report its Python function name."),
    ProfileTask("read_firstline", "read_file", "Read fixture_app.py using read_file and quote its first line."),
    ProfileTask("read_signature", "read_file", "Read fixture_app.py and report the function signature."),
    ProfileTask("info_fixture", "get_file_info", "Get file metadata for fixture_app.py using get_file_info."),
    ProfileTask("info_main", "get_file_info", "Get file metadata for main.py using get_file_info."),
    ProfileTask("info_readme", "get_file_info", "Get file metadata for README.md using get_file_info."),
    ProfileTask("list_root", "list_directory", "Use list_directory to list files in the project root."),
    ProfileTask("list_tools", "list_directory", "Use list_directory on the tools folder and name an item."),
    ProfileTask("list_core", "list_directory", "Use list_directory on the core folder and name an item."),
    ProfileTask("search_fixture", "search_files", "Use search_files to locate the filename fixture_app.py."),
    ProfileTask("search_readme", "search_files", "Use search_files to locate README.md by name."),
    ProfileTask("search_main", "search_files", "Use search_files to locate main.py by name."),
    ProfileTask("context_function", "code_context", "Use code_context to find the definition of the function calculate in fixture_app.py."),
    ProfileTask("context_symbol", "code_context", "Use code_context for the symbol calculate and report its defining path."),
    ProfileTask("impact_fixture", "affected_tests", "Use affected_tests to assess tests impacted if fixture_app.py changed."),
    ProfileTask("impact_main", "affected_tests", "Use affected_tests to assess tests impacted if main.py changed."),
)


def build_schedule() -> list[ScheduledTask]:
    """Latin-square profile ordering for each task across its three repeats."""
    scheduled: list[ScheduledTask] = []
    for task_index, task in enumerate(PROFILE_TASKS):
        for repetition in (1, 2, 3):
            order = (task_index + repetition - 1) % len(MODEL_PROFILES)
            for position in range(len(MODEL_PROFILES)):
                scheduled.append(
                    ScheduledTask(
                        task_id=task.task_id,
                        expected_tool=task.expected_tool,
                        instruction=task.instruction,
                        repetition=repetition,
                        profile=MODEL_PROFILES[(order + position) % len(MODEL_PROFILES)],
                        order_index=position,
                    )
                )
    return scheduled


def prepare_fixture_roots(repo: Path, output: Path) -> dict[str, str]:
    """Stage only committed HEAD into three new, isolated profile roots.

    No live runtime, settings or existing workspace is modified. Output must not
    exist. Reject links, oversized archives and paths escaping the destination.
    """
    source = repo.resolve(strict=True)
    output = output.resolve()
    if not (source / ".git").exists():
        raise ValueError("source_not_git_checkout")
    if output.exists() or output.is_symlink():
        raise ValueError("output_must_not_exist")
    main = source / "main.py"
    if not main.is_file():
        raise ValueError("missing_entrypoint")
    archive = subprocess.run(
        ["git", "-C", str(source), "archive", "--format=tar", "HEAD"],
        check=True, capture_output=True, timeout=40,
    ).stdout
    if len(archive) > 48 * 1024 * 1024:
        raise ValueError("archive_too_large")
    files: list[tuple[Path, bytes]] = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tape:
        for member in tape:
            if member.isdir():
                continue
            relative = Path(member.name)
            if (not member.isfile() or relative.is_absolute()
                    or ".." in relative.parts or not relative.parts
                    or len(files) >= 2000 or member.size > 4 * 1024 * 1024):
                raise ValueError("unsafe_archive_entry")
            stream = tape.extractfile(member)
            if stream is None:
                raise ValueError("unreadable_archive_entry")
            files.append((relative, stream.read()))
    if not files or not any(path == Path("main.py") for path, _ in files):
        raise ValueError("archive_missing_entrypoint")
    output.mkdir(parents=True, exist_ok=False)
    for profile in MODEL_PROFILES:
        workspace = output / profile
        workspace.mkdir()
        for relative, data in files:
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as f:
                f.write(data)
        with (workspace / "fixture_app.py").open("x", encoding="utf8") as f:
            f.write("def calculate(value: int) -> int:\n    return value + 1\n")
        config_dir = workspace / ".agents"
        config_dir.mkdir(exist_ok=True)
        config = {
            "mcpServers": {
                "computerpilot": {
                    "command": str(Path(sys.executable).resolve()),
                    "args": [str(workspace / "main.py"), "--transport", "stdio"],
                    "env": {
                        "MCP_TOOL_PROFILE": profile,
                        "MCP_EXTENSIONS_ENABLED": "false",
                        "MCP_LSP_PERSISTENT_ENABLED": "false",
                        "MCP_WORKFLOW_DAG_ENABLED": "false",
                        "MCP_OUTPUT_DEFAULT": "legacy-inline",
                    },
                }
            }
        }
        (config_dir / "mcp_config.json").write_text(
            json.dumps(config, indent=2) + "\n", encoding="utf8",
        )
    sha = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    (output / "snapshot.json").write_text(
        json.dumps({"schema_version": 1, "commit": sha,
                    "tracked_file_count": len(files),
                    "source": str(source),
                    "profile_count": len(MODEL_PROFILES)}, indent=2) + "\n",
        encoding="utf8",
    )
    return validate_fixture_roots(output)


def validate_fixture_roots(base: Path) -> dict[str, str]:
    """Reject missing/mismatched old pilot workspaces, without modifying them."""
    roots: dict[str, str] = {}
    fixture_hashes: set[str] = set()
    resolved = base.resolve(strict=True)
    for profile in MODEL_PROFILES:
        candidate = (resolved / profile).resolve(strict=True)
        if candidate.parent != resolved:
            raise ValueError("workspace_root_escape")
        if not (candidate / ".agents" / "mcp_config.json").is_file():
            raise ValueError(f"missing_mcp_config: {profile}")
        fixture = candidate / "fixture_app.py"
        if not fixture.is_file() or fixture.is_symlink():
            raise ValueError(f"missing_or_linked_fixture: {profile}")
        digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
        fixture_hashes.add(digest)
        roots[profile] = str(candidate)
    if len(fixture_hashes) != 1:
        raise ValueError("fixture_mismatch_across_profiles")
    return roots


def classify_result(row: dict[str, Any]) -> Literal["PASS", "FAIL", "UNVERIFIED"]:
    """Do not accept a model's own success claim as proof that MCP ran."""
    expected = row.get("expected_tool")
    tools = row.get("observed_tool_names")
    if (
        not isinstance(tools, list)
        or expected not in tools
        or any(not isinstance(name, str) for name in tools)
        or row.get("response_is_correct") is not True
    ):
        return "UNVERIFIED"
    if row.get("terminal_status") != "SUCCESS" or row.get("exit_code") != 0:
        return "FAIL"
    if row.get("permission_denied") is not False:
        return "FAIL"
    if any(name not in {expected} for name in tools):
        return "FAIL"
    return "PASS"


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("empty_percentile")
    return sorted(values)[math.ceil(len(values) * fraction) - 1]


def summarize_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    expected_pairs = {
        (entry.profile, entry.task_id, entry.repetition): entry
        for entry in build_schedule()
    }
    observed: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in rows:
        key = (row.get("profile"), row.get("task_id"), row.get("repetition"))
        if key not in expected_pairs:
            raise ValueError(f"unknown_profile_task_or_repetition: {key!r}")
        if key in observed:
            raise ValueError(f"duplicate_profile_task_repetition: {key!r}")
        if row.get("expected_tool") not in (None, expected_pairs[key].expected_tool):
            raise ValueError(f"expected_tool_mismatch: {key!r}")
        observed[key] = row
    summary: dict[str, Any] = {
        "expected": len(expected_pairs),
        "recorded": len(observed),
        "missing": len(expected_pairs) - len(observed),
        "decision": "INCOMPLETE",
        "reasons": [],
        "profiles": {},
        "caveat": "This report is not proof of independent grading, accurate pricing, or production performance.",
    }
    for profile in MODEL_PROFILES:
        chosen = [row for key, row in observed.items() if key[0] == profile]
        statuses = Counter(classify_result(row) for row in chosen)
        timings = [
            float(row["elapsed_seconds"])
            for row in chosen
            if isinstance(row.get("elapsed_seconds"), (int, float))
            and math.isfinite(float(row["elapsed_seconds"]))
            and 0 <= float(row["elapsed_seconds"]) <= 3600
        ]
        summary["profiles"][profile] = {
            "count": len(chosen),
            "pass": statuses["PASS"],
            "fail": statuses["FAIL"],
            "unverified": statuses["UNVERIFIED"],
            "p50_sec": statistics.median(timings) if len(timings) == 60 else None,
            "p95_sec": _percentile(timings, 0.95) if len(timings) == 60 else None,
        }
    if summary["missing"]:
        summary["reasons"].append("180_actual_traces_required")
        return summary
    if any(summary["profiles"][profile]["unverified"] for profile in MODEL_PROFILES):
        summary["decision"] = "NO_GO"
        summary["reasons"].append("tool_identity_or_response_not_independently_verified")
    coding = summary["profiles"]["coding"]
    core = summary["profiles"]["coding_core"]
    if core["pass"] < coding["pass"] or core["fail"] > coding["fail"]:
        summary["decision"] = "NO_GO"
        summary["reasons"].append("coding_core_success_or_wrong_tool_regression")
    if coding["p95_sec"] is None or core["p95_sec"] is None:
        summary["decision"] = "NO_GO"
        summary["reasons"].append("missing_latency_data")
    elif core["p95_sec"] > coding["p95_sec"] * 1.05:
        summary["decision"] = "NO_GO"
        summary["reasons"].append("coding_core_p95_regression_above_5_percent")
    if summary["decision"] != "NO_GO":
        summary["decision"] = "PILOT_ACCEPTABLE_NOT_PRODUCTION_GO"
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, help="Validate three comparable, local read-only workspaces")
    parser.add_argument("--output", type=Path, help="Write complete offline schedule; never execute a model")
    parser.add_argument("--results", type=Path, help="Evaluate newline-delimited live result records, without running them")
    parser.add_argument("--prepare-source", type=Path, help="Stage committed HEAD into three read-only evaluation snapshots")
    parser.add_argument("--prepare-output", type=Path, help="Brand-new directory for three isolated evaluation snapshots")
    args = parser.parse_args()
    if args.prepare_source or args.prepare_output:
        if not args.prepare_source or not args.prepare_output:
            parser.error("Both --prepare-source and --prepare-output are required")
        print(json.dumps(prepare_fixture_roots(args.prepare_source, args.prepare_output), indent=2))
    if args.fixtures:
        validate_fixture_roots(args.fixtures)
    if args.results:
        rows = [json.loads(line) for line in args.results.read_text(encoding="utf8").splitlines() if line.strip()]
        result = summarize_results(rows)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if result["decision"] == "PILOT_ACCEPTABLE_NOT_PRODUCTION_GO" else 2
    if args.output:
        if args.output.exists():
            parser.error("Refusing to overwrite an existing plan")
        payload = {
            "schema_version": 1,
            "type": "OFFLINE_PLAN_NOT_REAL_MODEL_EVIDENCE",
            "manifest": [asdict(entry) for entry in build_schedule()],
        }
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    else:
        print(json.dumps({"offline_plan_count": len(build_schedule()), "no_live_model_called": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
