"""Opt-in acceptance of real, pinned LSP servers; no live MCP configuration changes.

Usage: .venv\\Scripts\\python.exe scripts/real_lsp_acceptance.py python
       --executable <absolute-node.exe> --entrypoint <absolute-pyright-langserver.js>
For TypeScript use node.exe plus cli.mjs; PHP also supports node.exe plus Intelephense's entrypoint
with --php-provider intelephense. Fixtures live under tests/.real_lsp_runs.
They are deleted after the run, including provider failure, and are not committed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, TypedDict

import psutil

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from core.errors import ToolError  # noqa: E402
from tools.language import sessions  # noqa: E402
from tools.language.lsp import position_to_offset, uri_to_path  # noqa: E402
from tools.language.providers import LspProvider, ProviderId, validate_provider  # noqa: E402
from tools.language.sessions import LspSessionManager  # noqa: E402


class RealLspCase(TypedDict):
    provider: ProviderId
    language: str
    source: tuple[str, str]
    use: tuple[str, str]
    line: int
    character: int
    change_from: str
    change_to: str


CASES: dict[str, RealLspCase] = {
    "python": {
        "provider": "pyright",
        "language": "python",
        "source": ("service.py", "def calculate(value: int) -> int:\n    return value + 1\n"),
        "use": ("main.py", "from service import calculate\nresult = calculate(2)\n"),
        "line": 1,
        "character": 11,
        "change_from": "result = calculate(2)",
        "change_to": "result = calculate('not-an-int')",
    },
    "typescript": {
        "provider": "typescript",
        "language": "typescript",
        "source": ("service.ts", "export function calculate(value: number): number { return value + 1; }\n"),
        "use": ("main.ts", "import { calculate } from './service';\nexport const result = calculate(2);\n"),
        "line": 1,
        "character": 23,
        "change_from": "calculate(2)",
        "change_to": "calculate('not-a-number')",
    },
    "php": {
        "provider": "phpactor",
        "language": "php",
        "source": ("Service.php", "<?php\ndeclare(strict_types=1);\nfunction calculate(int $value): int { return $value + 1; }\n"),
        "use": ("main.php", "<?php\ndeclare(strict_types=1);\nrequire_once __DIR__ . '/Service.php';\n$result = calculate(2);\n"),
        "line": 3,
        "character": 12,
        "change_from": "calculate(2)",
        "change_to": "calculate('not-an-int')",
    },
}


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        h = hashlib.sha256()
        for data in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def location_matches(value: Any, target: Path, *, position: dict[str, int] | None = None) -> bool:
    if isinstance(value, dict):
        uri = value.get("uri") or value.get("targetUri")
        span = value.get("targetSelectionRange") or value.get("range")
        if not isinstance(uri, str) or not isinstance(span, dict):
            return False
        try:
            if uri_to_path(uri, target.parent) != target.resolve():
                return False
            text = target.read_text(encoding="utf-8")
            start = position_to_offset(text, span["start"])
            end = position_to_offset(text, span["end"])
            if start >= end or "calculate" not in text[start:end]:
                return False
            return position is None or start <= position_to_offset(text, position) < end
        except (ToolError, OSError, KeyError, TypeError, ValueError):
            return False
    if isinstance(value, list):
        return any(location_matches(item, target, position=position) for item in value)
    return False


def diagnostic_items(value: Any) -> list[dict[str, Any]]:
    check(isinstance(value, dict) and value.get("kind") == "full", "Expected an actual full diagnostic report")
    items = value.get("items")
    check(isinstance(items, list) and all(isinstance(item, dict) for item in items), "Invalid diagnostic items")
    return items


def type_error_matches(items: list[dict[str, Any]], language: str, line: int) -> bool:
    types = {"python": ("int", "not-an-int"), "typescript": ("number", "string"), "php": ("int", "not-an-int")}[language]
    return any(
        item.get("severity") == 1
        and isinstance(item.get("message"), str)
        and all(token in item["message"].lower() for token in types)
        and isinstance(item.get("range"), dict)
        and isinstance(item["range"].get("start"), dict)
        and item["range"]["start"].get("line") == line
        for item in items
    )


async def execute(
    case_name: str, executable: Path, entrypoint: Path | None, workspace: Path, results: dict[str, Any],
    *, php_provider: ProviderId = "phpactor",
) -> None:
    c = CASES[case_name]
    definition_file = workspace / c["source"][0]
    usage_file = workspace / c["use"][0]
    definition_file.write_text(c["source"][1], encoding="utf-8")
    usage_file.write_text(c["use"][1], encoding="utf-8")
    if case_name == "typescript":
        # The trusted server bundle owns tsserver; never load workspace node_modules code.
        (workspace / "tsconfig.json").write_text(
            json.dumps(
                {
                    "compilerOptions": {"target": "ES2020", "module": "commonjs", "moduleResolution": "node", "strict": True},
                    "include": ["*.ts"],
                }
            ),
            encoding="utf-8",
        )
    # Do not trust arbitrary external roots; only this owned fixture directory.
    provider = LspProvider(
        provider_id=php_provider if case_name == "php" else c["provider"],
        languages=(c["language"],),
        executable=executable.resolve(strict=True),
        executable_sha256=digest(executable),
        entrypoint=entrypoint.resolve(strict=True) if entrypoint else None,
        entrypoint_sha256=digest(entrypoint) if entrypoint else None,
        declared_version="real-provider-local",
        trusted_roots=(workspace,),
    )
    validate_provider(workspace, c["language"], providers=(provider,))
    results["pin_verified"] = True
    results["lsp_provider_id"] = provider.provider_id
    results["executable_sha256"] = provider.executable_sha256
    results["entrypoint_sha256"] = provider.entrypoint_sha256
    uri = usage_file.resolve().as_uri()
    position = {"line": c["line"], "character": c["character"]}
    params = {"textDocument": {"uri": uri}, "position": position}
    managers: list[LspSessionManager] = []
    pids: dict[int, float] = {}

    def capture_processes(manager: LspSessionManager) -> None:
        with manager._lock:
            roots = [s.process.pid for s in manager._sessions.values() if s.process is not None]
        for pid in roots:
            try:
                process = psutil.Process(pid)
                for owned in [process, *process.children(recursive=True)]:
                    pids[owned.pid] = owned.create_time()
            except psutil.NoSuchProcess:
                pass

    def live_processes() -> list[int]:
        alive = []
        for pid, created in pids.items():
            try:
                if psutil.Process(pid).create_time() == created:
                    alive.append(pid)
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                alive.append(pid)
        return alive

    async def ask(manager: LspSessionManager, method: str, data: dict[str, Any], name: str, *, timeout_sec: float = 75) -> Any:
        try:
            reply = await manager.arequest_with_evidence(workspace, c["language"], method, data, timeout_sec=timeout_sec)
            results[name] = "REPLIED"
            results[name + "_capabilities"] = sorted(reply.capabilities)
            return reply
        except ToolError as exc:
            results[name] = "FAIL:" + exc.code
            raise
        finally:
            capture_processes(manager)

    async def diagnostics(manager: LspSessionManager, name: str, *, invalid: bool = False) -> None:
        deadline = time.monotonic() + 25
        items: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            reply = await ask(
                manager, "textDocument/diagnostic", {"textDocument": {"uri": uri}}, name,
                timeout_sec=max(0.1, deadline - time.monotonic()),
            )
            items = diagnostic_items(reply.value)
            if type_error_matches(items, case_name, c["line"]) if invalid else not items:
                results[name] = "PASS"
                results[name + "_items"] = len(items)
                if invalid:
                    results[name + "_evidence"] = [
                        {"code": item.get("code"), "message": item.get("message", "")[:250]}
                        for item in items[:5]
                    ]
                return
            await asyncio.sleep(0.1)
        raise AssertionError(f"Expected {'a typed argument error' if invalid else 'clean diagnostics'}; got {str(items)[:350]}")

    try:
        for turn in range(2):
            suffix = "_after_restart" if turn else ""
            usage_file.write_text(c["use"][1], encoding="utf-8")
            manager = LspSessionManager((provider,), generation=f"real-{case_name}-{turn}")
            managers.append(manager)
            # Open both files in the same session before cross-file navigation.
            await ask(manager, "textDocument/documentSymbol", {"textDocument": {"uri": definition_file.as_uri()}}, "source_index" + suffix)
            reply = await ask(manager, "textDocument/definition", params, "definition" + suffix)
            check(location_matches(reply.value, definition_file), f"Definition did not reach the source symbol: {str(reply.value)[:350]}")
            results["definition" + suffix] = results["cross_file_definition" + suffix] = "PASS"
            refs = await ask(manager, "textDocument/references", {**params, "context": {"includeDeclaration": True}}, "references" + suffix)
            check(isinstance(refs.value, list) and location_matches(refs.value, definition_file), "References omitted the declaration")
            check(location_matches(refs.value, usage_file, position=position), "References omitted the actual call")
            results["reference_count" + suffix] = len(refs.value)
            results["references" + suffix] = "PASS"
            await diagnostics(manager, "diagnostics" + suffix)
            text = usage_file.read_text(encoding="utf-8")
            check(c["change_from"] in text, "Fixture mutation precondition failed")
            usage_file.write_text(text.replace(c["change_from"], c["change_to"]), encoding="utf-8")
            updated = await ask(manager, "textDocument/definition", params, "definition_after_edit" + suffix)
            check(location_matches(updated.value, definition_file), "Definition did not reach source after the mutation")
            results["definition_after_edit" + suffix] = "PASS"
            documents = {u: (version, sha) for u, version, sha in updated.documents}
            check(uri in documents and documents[uri][0] >= 2, "didChange did not advance document version")
            check(documents[uri][1] == digest(usage_file), "Document hash not synchronized")
            results["file_edit_and_sync" + suffix] = "PASS"
            await diagnostics(manager, "diagnostics_after_edit" + suffix, invalid=True)
            usage_file.write_text(c["use"][1], encoding="utf-8")
            await diagnostics(manager, "diagnostics_after_restore" + suffix)
            with manager._lock:
                results["push_diagnostics_notifications"] = sum(
                    session.transport.notification_counts["textDocument/publishDiagnostics"]
                    for session in manager._sessions.values()
                    if session.transport is not None
                )
            await asyncio.to_thread(manager.close_workspace, workspace)
            check(manager.snapshot()["active"] == 0 and manager.snapshot()["quarantined"] == 0, "Session did not drain")
            results["first_cleanup" if turn == 0 else "restart_cleanup"] = "PASS"
            # A fresh owner generation is the supported restart path, with old process fully drained.
        results["restart"] = "PASS"
        results["owned_pids"] = len(pids)
    finally:
        for manager in managers:
            try:
                capture_processes(manager)
                await asyncio.to_thread(manager.close_all)
            except Exception as exc:
                results.setdefault("cleanup_error", type(exc).__name__)
        # Windows Job process-tree teardown can outlive the owner thread by a short interval.
        for _attempt in range(20):
            if not live_processes():
                break
            await asyncio.sleep(0.1)
        still_alive = live_processes()
        results["cleanup"] = (
            "PASS"
            if not still_alive and "cleanup_error" not in results
            and all(m.snapshot()["active"] == 0 and m.snapshot()["quarantined"] == 0 for m in managers)
            else "FAIL"
        )
        results["orphan_pids"] = still_alive


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("provider", choices=tuple(CASES))
    p.add_argument("--executable", type=Path, required=True)
    p.add_argument("--entrypoint", type=Path)
    p.add_argument("--php-provider", choices=("phpactor", "intelephense"), default="phpactor")
    p.add_argument("--keep-workspace", action="store_true")
    args = p.parse_args()
    root = REPO / "tests" / ".real_lsp_runs"
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / f"{args.provider}-{os.getpid()}-{time.monotonic_ns()}"
    workspace.mkdir()
    scratch = workspace / ".tmp"
    scratch.mkdir()
    results: dict[str, Any] = {"provider": args.provider, "workspace_kind": "repo-local-ephemeral"}
    previous_settings = sessions.SETTINGS
    sessions.SETTINGS = replace(previous_settings, state_dir=workspace / ".state")
    scratch_variables = ("TEMP", "TMP") if os.name == "nt" else ("TMPDIR",)
    previous_scratch_environment = {name: os.environ.get(name) for name in scratch_variables}
    os.environ.update(dict.fromkeys(scratch_variables, str(scratch)))
    try:
        asyncio.run(execute(args.provider, args.executable, args.entrypoint, workspace, results, php_provider=args.php_provider))
    except (Exception, KeyboardInterrupt) as exc:
        detail = ":" + exc.code if isinstance(exc, ToolError) else (":" + str(exc)[:420] if isinstance(exc, AssertionError) else "")
        results["uncaught"] = type(exc).__name__ + detail
    finally:
        sessions.SETTINGS = previous_settings
        for name, previous in previous_scratch_environment.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        target = workspace.resolve()
        contained = target.parent == root.resolve() and root.resolve().is_relative_to(REPO.resolve())
        if not args.keep_workspace and results.get("cleanup") == "PASS" and contained:
            for _attempt in range(4):
                if not workspace.exists():
                    break
                try:
                    shutil.rmtree(workspace)
                except OSError:
                    time.sleep(0.15)
            results["workspace_removed"] = not workspace.exists()
        else:
            results["workspace_removed"] = False
            results["workspace_retained"] = str(workspace)
    print(json.dumps(results, ensure_ascii=False, sort_keys=True))
    mandatory = (
        "pin_verified",
        "definition",
        "cross_file_definition",
        "references",
        "diagnostics",
        "definition_after_edit",
        "file_edit_and_sync",
        "diagnostics_after_edit",
        "diagnostics_after_restore",
        "first_cleanup",
        "definition_after_restart",
        "cross_file_definition_after_restart",
        "references_after_restart",
        "diagnostics_after_restart",
        "definition_after_edit_after_restart",
        "file_edit_and_sync_after_restart",
        "diagnostics_after_edit_after_restart",
        "diagnostics_after_restore_after_restart",
        "restart_cleanup",
        "restart",
        "cleanup",
        "workspace_removed",
    )
    return (
        0
        if all(
            results.get(key) is True if key in {"pin_verified", "workspace_removed"} else results.get(key) == "PASS" for key in mandatory
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
