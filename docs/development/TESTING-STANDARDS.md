# Testing standards

These standards describe current repository test patterns and the required v0.9 release gates. Native and extension tests exercise the implemented boundaries with isolated local fixtures. The [agent contract](../../AGENTS.md), [native tool guide](MCP-TOOL-AUTHORING.md), and [release process](../release-process.md) own their respective contracts.

## Isolation and existing patterns

Use pytest `tmp_path` and `monkeypatch`, function-local fixtures, and `asyncio.run(scenario())` as in `tests/test_tool_profiles.py` and `tests/test_mcp_integration.py`. The repository has no global `tests/conftest.py` at the audited stable baseline. Inspect nearby tests before inventing fixture machinery. Do not add pytest-asyncio merely for style; the installed SDK/AnyIO support real async scenarios already.

For SDK-boundary behavior use `async with Client(create_server(), raise_exceptions=True)`, then check `structured_content` and observable state. For body helpers use direct tests where the SDK is irrelevant. Preserve the distinction between a direct internal call and a wire/client call; test both when guards or registration matter.

Keep configuration, journals, artifacts, marker files and owned subprocesses disposable. Native suites must keep extensions disabled and must not read/launch a developer's real extension config. Explicit fixture cases opt in with their own absolute paths and minimal environment. Avoid real credentials, public third-party MCP servers, network downloads or production `.agent_state` writes. Restore monkeypatched settings and close all SDK contexts in the same task that entered them.

`pyproject.toml` defaults pytest to `-q --basetemp=.pytest-tmp` with `.pytest_cache`, outside live `.agent_state`. Set a unique basetemp for independent runs. Always use the correct checkout as cwd and confirm `core.config.PROJECT_ROOT` when borrowing another checkout's interpreter. Never run parallel suites against shared local state.

## Behavioral evidence

- Input/authorization: invalid or denied calls prove the body/dispatch did not run and no effect marker exists.
- Registration/profiles: public catalog/schema, strict unknown arguments, unique names, expected platform gates and all eleven profiles. Windows/full has 151 native tools by default and 152 with Batch enabled; coding_core has 43 and read_batch has seven. POSIX counts differ.
- Mutations: duplicate, timeout, cancellation, storage failure, partial success and restart paths prove exact effect counts and durable outcomes. Lost response after effect stays uncertain; no replay/reroute.
- Persistence: legacy fixtures, repeated migration, rollback/crash, concurrent CAS/admission and compaction preserve data and unresolved operations.
- Process ownership: real child/grandchild trees, root exit, failed ownership before execution, forced stop, and no surviving reader threads/tasks/processes.
- Output/security: byte/depth/count boundaries before expensive parsing, no-network schema validation, literal canaries across stdout/stderr/errors/logs/health/panel/artifacts, and bounded memory under floods.

Synchronize using events, protocol acknowledgements or durable marker/control files. Avoid arbitrary sleeps and assumptions about interpreter startup timing. A timeout assertion must also check cleanup. Count provider-side durable effects independently of caller outcomes.

Use the implemented `tests/fixtures/fake_mcp_server.py` and `tests/extension_test_utils.py` for public SDK sessions and controlled malformed bytes, deterministic effect counters and finite shutdown. Extension suites cover no-network schema, generation/schema freeze, notifications, policy matrices, no-replay, authenticated panel and quota stress. Native Batch tests verify real SDK byte bounds, full/profile eligibility, Unicode windows, cancellation and resource/connection draining. Packaging regressions verify all five pinned-ref archives ignore dirty files and private untracked state.

## Commands and scope

From the actual repository root, using its validated Python environment:

```text
python -m pytest -o addopts= -q tests/test_tool_profiles.py tests/test_mcp_integration.py
python -m ruff check .
python -m mypy core tools scripts tests
python -m compileall -q core tools scripts tests
python main.py --check
python -m scripts.health_check --json
python -m pytest -o addopts= -q --basetemp=.pytest-tmp-v09
python -m scripts.doctor --mode local-http --browser
```

On Windows use `.venv/Scripts/python.exe`; POSIX uses `.venv/bin/python`. An absolute interpreter from a validated checkout is acceptable with verified cwd/import root. Docs-only units use UTF-8, Markdown fences/links/source references, truthful status and `git diff --check`; do not add tests that only mirror copy.

Start focused, then run the required checks for the affected boundary. Re-run after the last relevant source/config/dependency edit. Record the exact command, exit status, pass/skip counts, tested HEAD and working diff, platform/interpreter/dependency versions, and material missing evidence. Expected pre-implementation red tests demonstrate the missing behavior; acceptance is still pending. Never label code-only or skipped evidence VERIFIED.

## Platforms, stress and release

Portable Core needs actual Windows/Linux/macOS coverage; Windows UIA is deliberately gated elsewhere. Required-native external transport cannot activate before three-platform ownership/deadline/cleanup proof and independent review. Measurements separate cold `--check`, native readiness, all-provider readiness, Core/child RSS, tasks/threads/processes, and teardown latency. Record sample counts/warmup/host/config/SDK/SHA. An incomparable baseline is unknown, not no regression.

Release gates are the existing six CI entries, three native platform validation entries and one packaging entry on the exact final main SHA, plus the publication run. Five archive targets do not prove five native hosts. Rehash actual archive bytes and inspect manifests/inventory. Follow [release process](../release-process.md) and the active roadmap; no ad-hoc tag bypass.

Preserve logs/SHA/dependencies for failures. Diagnose deterministic failures before retrying; one evidence-based transient rerun is bounded. Isolate pre-existing failures and do not weaken invariants, add arbitrary sleeps or hide pending checks. The historical Windows lifecycle timing case remains a suspected runner race, not proof that a new failure is harmless. Repeated failure blocks its acceptance unit until reproduced and resolved.
