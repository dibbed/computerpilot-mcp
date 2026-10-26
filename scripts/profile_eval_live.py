"""Opt-in, strictly read-only AGY model pilot for profile_eval snapshots.

Never run on the original repository or unattended without explicit --execute.
Permissions are granted only for seven read-only ComputerPilot tools, with
the original settings restored on exit. Usage budgets are observational (not
a provider-enforced billing cap); the model provider may bill each call.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from scripts.profile_eval import build_schedule, validate_fixture_roots

READ_ONLY_TOOLS = frozenset({
    "project_summary", "read_file", "get_file_info", "list_directory",
    "search_files", "code_context", "affected_tests",
})
PERMISSION_RULES = tuple(f"mcp(computerpilot/{tool})" for tool in sorted(READ_ONLY_TOOLS))
MODEL = "gemini-3.8-flash-low"


def atomic_write(path: Path, content: bytes) -> None:
    temp = path.with_name(path.name + ".cp-eval-" + str(os.getpid()))
    with temp.open("xb") as out:
        out.write(content)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)


@contextmanager
def scoped_read_only_permissions(settings: Path) -> Iterator[None]:
    original = settings.read_bytes()
    current = json.loads(original)
    if not isinstance(current, dict):
        raise ValueError("invalid_settings")
    permissions = current.get("permissions") or {}
    if not isinstance(permissions, dict):
        raise ValueError("invalid_permission_policy")
    allowed = permissions.get("allow") or []
    if not isinstance(allowed, list) or not all(isinstance(x, str) for x in allowed):
        raise ValueError("invalid_permission_list")
    additions = [rule for rule in PERMISSION_RULES if rule not in allowed]
    candidate = json.dumps(
        {**current, "permissions": {**permissions, "allow": [*allowed, *additions]}},
        ensure_ascii=False, indent=2,
    ).encode("utf8") + b"\n"
    if settings.read_bytes() != original:
        raise RuntimeError("settings_changed_before_grant")
    if additions:
        atomic_write(settings, candidate)
    try:
        yield
    finally:
        latest_bytes = settings.read_bytes()
        if latest_bytes == candidate:
            atomic_write(settings, original)
        elif latest_bytes == original and not additions:
            pass
        else:
            latest = json.loads(latest_bytes)
            latest_perms = latest.get("permissions") or {}
            if not isinstance(latest_perms, dict) or not isinstance(latest_perms.get("allow"), list):
                raise RuntimeError("settings_changed_unrecoverably_review_needed")
            latest_perms["allow"] = [v for v in latest_perms["allow"] if v not in additions]
            if not latest_perms["allow"]:
                latest_perms.pop("allow")
            if not latest_perms:
                latest.pop("permissions", None)
            atomic_write(settings, json.dumps(
                latest, ensure_ascii=False, indent=2,
            ).encode("utf8") + b"\n")
        checked = json.loads(settings.read_bytes())
        remaining = (checked.get("permissions") or {}).get("allow") or []
        if any(rule in remaining for rule in additions):
            raise RuntimeError("temporary_permission_not_revoked")


def validate_isolated_mcp(root: Path, profile: str) -> None:
    config = json.loads((root / ".agents" / "mcp_config.json").read_text(encoding="utf8"))
    servers = config.get("mcpServers", {})
    if not isinstance(servers, dict) or set(servers) != {"computerpilot"}:
        raise ValueError("unexpected_mcp_servers")
    server = servers["computerpilot"]
    if server.get("command") != str(Path(sys.executable).resolve()):
        raise ValueError("untrusted_mcp_command")
    if server.get("args") != [str(root / "main.py"), "--transport", "stdio"]:
        raise ValueError("untrusted_mcp_arguments")
    environ = server.get("env") or {}
    required = {
        "MCP_TOOL_PROFILE": profile,
        "MCP_EXTENSIONS_ENABLED": "false",
        "MCP_LSP_PERSISTENT_ENABLED": "false",
        "MCP_WORKFLOW_DAG_ENABLED": "false",
        "MCP_OUTPUT_DEFAULT": "legacy-inline",
    }
    if environ != required:
        raise ValueError("unsafe_mcp_environment")


def parse_model_events(stdout: str) -> dict[str, Any]:
    terminal: dict[str, Any] = {}
    explicit_tools: list[str] = []
    ambiguous_count = 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("event") == "result" and isinstance(event.get("result"), dict):
            terminal = event["result"]
        step = event.get("step_update")
        if not isinstance(step, dict):
            continue
        raw = step.get("tool_name")
        if not isinstance(raw, str) or "duration_seconds" not in step:
            continue
        info = step.get("tool_info")
        info = info if isinstance(info, dict) else {}
        # AGY also exposes built-in read_file; bare names must NEVER be
        # interpreted as an MCP ComputerPilot call.
        server = info.get("server_name") or info.get("server")
        tool = info.get("tool_name") or info.get("name")
        if server == "computerpilot" and tool in READ_ONLY_TOOLS:
            explicit_tools.append(tool)
        else:
            ambiguous_count += 1
    usage = terminal.get("usage") or {}
    return {
        "terminal_status": terminal.get("status"),
        "observed_tool_names": explicit_tools,
        "ambiguous_tool_calls": ambiguous_count,
        "input_tokens": usage.get("input_tokens") if isinstance(usage, dict) else None,
        "output_tokens": usage.get("output_tokens") if isinstance(usage, dict) else None,
        "cached_read_tokens": usage.get("cache_read_tokens") if isinstance(usage, dict) else None,
        "result_contains_response": bool(terminal.get("response")),
    }


def runtime_trace_offset(root: Path) -> int:
    trace = root / ".agent_state" / "timings.jsonl"
    if trace.is_symlink():
        raise ValueError("linked_runtime_trace")
    return trace.stat().st_size if trace.is_file() else 0


def read_runtime_tool_events(root: Path, since_offset: int) -> list[str]:
    """Identify real MCP tools via isolated SERVER dispatch, not AGY built-ins."""
    trace = root / ".agent_state" / "timings.jsonl"
    if trace.is_symlink():
        raise ValueError("linked_runtime_trace")
    if not trace.is_file():
        return []
    size = trace.stat().st_size
    if since_offset < 0 or size < since_offset or size - since_offset > 2 * 1024 * 1024:
        raise ValueError("runtime_trace_not_bounded_or_rotated")
    with trace.open("rb") as source:
        source.seek(since_offset)
        raw = source.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("runtime_trace_overflow")
    tools: list[str] = []
    for rawline in raw.splitlines():
        try:
            event = json.loads(rawline)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        if event.get("phase") == "request_pipeline" and event.get("method") == "tools/call":
            name = event.get("tool")
            if not isinstance(name, str) or len(name) > 128:
                tools.append("<invalid_tool_identity>")
            else:
                tools.append(name)
            if len(tools) > 100:
                raise ValueError("too_many_runtime_tool_events")
    return tools


def run(
    base: Path, *, model: str, max_calls: int, max_total_input_tokens: int,
    max_wall_seconds: int, agy: Path, settings: Path, results_path: Path,
    start_index: int = 0,
) -> dict[str, Any]:
    if not 1 <= max_calls <= 180 or max_total_input_tokens < 1 or max_wall_seconds < 1 or not 0 <= start_index < 180:
        raise ValueError("invalid_budgets")
    if not agy.is_file() or not settings.is_file():
        raise ValueError("missing_authenticated_cli_or_settings")
    roots = validate_fixture_roots(base)
    for name, path in roots.items():
        validate_isolated_mcp(Path(path), name)
    if results_path.exists():
        raise ValueError("refusing_to_overwrite_results")
    if results_path.parent.resolve() != base.resolve():
        raise ValueError("results_must_be_within_isolated_base")
    budget_start = time.monotonic()
    spent_input = 0
    trials: list[dict[str, Any]] = []
    schedule = build_schedule()[start_index:]
    with scoped_read_only_permissions(settings):
        for entry in schedule:
            if len(trials) >= max_calls or spent_input >= max_total_input_tokens:
                break
            remaining = max_wall_seconds - (time.monotonic() - budget_start)
            if remaining < 10:
                break
            root = Path(roots[entry.profile])
            server_trace_before = runtime_trace_offset(root)
            prompt = (
                "This is a temporary isolated evaluation snapshot. "
                "Read-only tools only; no edits, terminal commands, browser, or network. "
                "You must call the actual ComputerPilot MCP tool specified here. "
                "Do not infer an MCP result. Use only the project root: "
                + str(root) + ". " + entry.instruction
                + " Cite the tool name in your short final response."
            )
            argv = [
                str(agy), "-p", prompt, "--model", model, "--output-format",
                "stream-json", "--print-timeout", "55s", "--disable-slash-commands",
            ]
            safe_env = os.environ.copy()
            safe_env.pop("COMPUTERPILOT_INTERNAL_RESOURCE_CAPABILITY", None)
            started = time.monotonic()
            try:
                response = subprocess.run(
                    argv, cwd=root, capture_output=True, text=True, encoding="utf8",
                    errors="replace", timeout=min(70, max(10, int(remaining))), env=safe_env,
                )
                parsed = parse_model_events(response.stdout)
                code = response.returncode
                permission_denied = "permission" in response.stderr.casefold() and "denied" in response.stderr.casefold()
            except subprocess.TimeoutExpired as error:
                output = error.stdout or b""
                partial_text = output.decode("utf8", errors="replace") if isinstance(output, bytes) else output
                parsed = {**parse_model_events(partial_text), "terminal_status": "TIMEOUT"}
                code = 9
                permission_denied = False
            client_tool_names = parsed.get("observed_tool_names", [])
            server_tool_names = read_runtime_tool_events(root, server_trace_before)
            parsed["client_observed_tool_names"] = client_tool_names
            parsed["observed_tool_names"] = server_tool_names
            parsed["tool_identity_source"] = (
                "isolated_mcp_server_request_pipeline" if server_tool_names else "unverified"
            )
            used = parsed.get("input_tokens")
            if isinstance(used, int) and used >= 0:
                spent_input += used
            else:
                # Fail closed: cannot enforce even a soft cumulative usage threshold.
                spent_input = max_total_input_tokens
            row = {
                **parsed, "schema_version": 1,
                "profile": entry.profile, "task_id": entry.task_id,
                "repetition": entry.repetition, "expected_tool": entry.expected_tool,
                "exit_code": code, "permission_denied": permission_denied,
                "response_is_correct": None,  # independent review is required
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "model": model, "verified_fixture_sha256": hashlib.sha256(
                    (root / "fixture_app.py").read_bytes(),
                ).hexdigest(),
            }
            trials.append(row)
            with results_path.open("a", encoding="utf8") as out:
                out.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "calls_completed_or_timed_out": len(trials), "requested_call_limit": max_calls,
        "reported_input_tokens": spent_input, "settings_restored": True,
        "results_path": str(results_path),
        "all_results_require_independent_correctness_review": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--max-calls", type=int, default=3)
    parser.add_argument("--max-total-input-tokens", type=int, default=120_000)
    parser.add_argument("--max-wall-seconds", type=int, default=225)
    parser.add_argument("--agy", type=Path, default=Path.home() / "AppData/Local/agy/bin/agy.exe")
    parser.add_argument("--settings", type=Path, default=Path.home() / ".gemini/antigravity-cli/settings.json")
    parser.add_argument("--results", type=Path)
    parser.add_argument("--start-index", type=int, default=0, help="Manifest offset for a bounded follow-up")
    parser.add_argument("--execute", action="store_true", help="Authorize real model usage and transient local settings grant")
    args = parser.parse_args()
    if not args.execute:
        print("REFUSED: explicit --execute is required for real model usage.", file=sys.stderr)
        return 2
    base = args.base.resolve(strict=True)
    path = args.results or base / "live-results.jsonl"
    result = run(base, model=args.model, max_calls=args.max_calls,
                 max_total_input_tokens=args.max_total_input_tokens,
                 max_wall_seconds=args.max_wall_seconds,
                 agy=args.agy, settings=args.settings, results_path=path,
                 start_index=args.start_index)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
