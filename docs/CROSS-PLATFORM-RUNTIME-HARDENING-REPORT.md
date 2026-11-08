# Cross-Platform Runtime Hardening — Final Report

Branch: `fix/cross-platform-runtime-hardening`
Repository: `https://github.com/dibbed/computerpilot-mcp`
Final functional tree: `c3496172773654534eb0307f41981852f15a8bba`
`main` baseline: `b88a2e17040036181ed99be3a0a0122c4dcb5e88` (unchanged)

Status vocabulary: **FIXED AND VERIFIED**, **VERIFIED**, **UNVERIFIED**, **BLOCKED**, **NOT APPLICABLE**.

## 1. Outcome

The branch preserves the full ComputerPilot capability set: arbitrary executables and shell
commands, absolute and project-relative paths, writes, Git, jobs, workflows, documents, browser,
LSP and Windows-only domains. No filesystem sandbox or read-only mode was introduced. The `full`
profile remains the default.

The cross-platform candidate now has real Linux, macOS and Windows execution evidence. Windows
Platform Validation passed, including the release gate and workflow restart soak, and Windows CI
runs the full pytest suite. The Windows-only P08 guard was corrected rather than relaxed: the
frozen v0.9.0 metadata predates both terminal `access` and `operation_id`, so both fields are
removed before hashing and the historical `dd943…` digest is retained. The catalogue-byte ceiling
is the measured 32,575 bytes, exactly 291 above the historical 32,284-byte limit for the
post-baseline catalogue delta (including `cancel_operation`). Final hosted status is recorded in §18.

Readiness is **cross-platform candidate**, not an unconditional production-ready declaration.
Authenticated tunnel, real Windows UIA/desktop, macOS UIA (not applicable), and a real v0.9.0 Windows
database migration remain unverified. Real local Chromium form interaction is now verified; public-site
browser behavior is not claimed.

## 2. Final identity

| Item | Value |
| --- | --- |
| Branch | `fix/cross-platform-runtime-hardening` |
| Functional HEAD | `c3496172773654534eb0307f41981852f15a8bba` |
| Integration base | `1847705` (Linux hardening `b301309` merged onto `b88a2e1`) |
| `main` | `b88a2e17040036181ed99be3a0a0122c4dcb5e88` |
| Local environment | Ubuntu Linux x86-64, Python 3.12.3, pytest 9.2.0, MCP SDK 2.0.1 |
| Tool counts | POSIX 138; Windows default 152; Windows Batch 153; coding 97; coding_core 43 |
| SQLite schema | v12 |

Documentation-only commits after `c349617` do not change the functional tree. Any code change after
a hosted run requires the affected checks to run again on the new SHA.

## 3. Original gaps and final status

| Gap | Final status | Evidence |
| --- | --- | --- |
| Windows native execution | **FIXED AND VERIFIED** for CI/platform scope | Windows Platform Validation success; Windows CI full suite; Windows Job Object/ownership tests |
| Windows UIA/desktop | **UNVERIFIED** | GitHub-hosted runners have no interactive desktop |
| macOS execution | **VERIFIED** for CI scope | macOS 3.10/3.12 full suite and Platform Validation |
| Stale `access` schema text | **FIXED AND VERIFIED** | `_ACCESS_DESCRIPTION`; terminal schema regression test; precise catalogue hash |
| Cancellation identity/authorization | **FIXED AND VERIFIED** for the bearer model | `SECURITY.md`; keyed audit correlation; registry and HTTP/stdio edge tests |
| Real browser acceptance | **VERIFIED** on a disposable local fixture | real stdio-MCP Chromium session: navigation, semantic snapshot/fill/select/click, extract, isolated second session and clean close; `artifacts/real-browser-acceptance.json` |
| Real LSP acceptance | **VERIFIED** for Pyright, including the pip wrapper and direct Node paths | provider SHA, definitions/references/diagnostics/restart/cleanup evidence; `artifacts/real-lsp-python.json`; final pip-launcher recheck in section 23 |
| Cross-platform migration | **VERIFIED** on synthetic fixtures; real v0.9.0 Windows DB **UNVERIFIED** | schema v5/v9/v10/v11/v12 tests |
| Soak/resource analysis | **VERIFIED** | 181 s live MCP soak plus 60-workflow platform soak and 40-workflow restart soak; no leaked claims/orphans |
| Extension process/transport | **VERIFIED** for the local subprocess fixture | 62 extension/tunnel-offline tests pass; real bounded stdio subprocess lifecycle |
| Authenticated tunnel/extension live acceptance | **UNVERIFIED** | no authorized credentials; offline tunnel and local extension tests only |

## 4. Confirmed defects and fixes

### 4.1 Terminal `access` metadata contradicted runtime behavior

**Root cause.** Every arbitrary terminal command takes exclusive host coordination and a cwd write
claim, but the advertised `access` description still said read commands might overlap. A caller
could believe a read-labelled command was concurrent when the runtime serialized it.

**Fix.** `tools/terminal/registry.py` uses `_ACCESS_DESCRIPTION` on `run_process`,
`run_powershell`, `run_cmd` and `run_shell`: the value is a compatibility hint only, commands are
always exclusive because arbitrary commands cannot be proven read-only, and results report
`effective_access`. Native typed readers keep their read concurrency. Regression coverage in
`tests/test_terminal_platform.py` checks the actual discovered schema and rejects “may overlap”.

### 4.2 Windows typing referenced POSIX-only process-group calls

**Root cause.** A shared recovery path directly referenced `os.killpg`; mypy on Windows and runtime
typing could fail even though the path was POSIX-owned.

**Fix.** `core/workflow_store.py` guards the group probe with `os.name != "nt"` and
`getattr(os, "killpg", None)`. A missing group probe keeps the claim fenced rather than releasing
it. `mypy --platform win32` passes.

### 4.3 Windows tool counts omitted `cancel_operation`

**Root cause.** The Windows registry now includes `cancel_operation`, but CI and soak gates still
expected 151 tools.

**Fix.** All Windows count contracts use 152, with 153 when native read batching is activated:
`scripts/workflow_soak.py`, `tests/test_workflow_soak.py`, `tests/test_tool_profiles.py`,
`scripts/activate_native_read_batch.ps1`, `docs/tooling.md`, `docs/troubleshooting.md` and
`docs/development/TESTING-STANDARDS.md`.

### 4.4 Native command cancellation used an opaque, over-broad identity model

**Root cause.** A process-local operation id is the only cancellation selector. Stateless HTTP has
no authenticated client/session identity, so the id is a bearer capability, not authorization.

**Fix.** The bearer model is explicit in `SECURITY.md` and `core/operation_control.py`: ids are
high-entropy, process-local, non-persistent, bounded-retention, reject unknown/malformed ids, and
never cancel durable jobs or workflows. Cancellation is tested for self, unknown, malformed,
duplicate, repeated, completed, restart, concurrent, registry-capacity and HTTP-disconnect cases.
No claim of cross-client authentication is made.

### 4.5 Windows catalogue replay and platform-test assumptions

The frozen v0.9.0 catalogue predates both terminal `access` and `operation_id`; earlier
reconstructions removed only one of them, so the historical hash could not match. The guard now
removes both post-release fields, restores the P14 descriptions and excludes `cancel_operation`
from the historical hash; the new descriptor is vetted separately. The original `dd943…` digest is
retained. The full-catalogue byte ceiling is 32,575, exactly 291 bytes above the historical 32,284
ceiling for the post-baseline catalogue delta (including `cancel_operation`); no baseline was
refreshed.

The recovery probe now accepts the conservative `stale_native_owner_dispatch_unknown` outcome:
a missing dispatch marker must remain quarantined, never be released on a guess. The Windows
snapshot probe waits for the maintenance task instead of assuming a 10 ms scheduler window on every
platform.

### 4.6 macOS cancellation token probe lost the token

**Root cause.** `/bin/sh` may `exec` the final command. The test’s token was in a shell comment, so
the process that survived did not contain the token and the cleanup probe reported “never started”.

**Fix.** `tests/test_terminal_cancellation.py` embeds the token inside the Python `-c` program. The
real cancellation test then exercises the actual owned process group on macOS.

### 4.7 Cancellation audit records retained the bearer id

**Root cause.** `cancel_operation` wrote the raw client-supplied operation id into durable audit
targets. That id is a bearer cancellation capability, so copied audit records could cancel an
in-flight operation.

**Fix.** `core/operation_control.py` now emits a process-local, keyed HMAC correlation of the form
`op-correlation:<hex>`; `tools/terminal/registry.py` writes that value. The legitimate API response
still returns the requested id. `tests/test_operation_control.py` covers repeated cancellation,
absence of the raw id and stable correlation, and `SECURITY.md` documents the guarantee.

## 5. Terminal access contract

1. Native typed readers may overlap compatible readers.
2. Every terminal command takes `coordination_claim(exclusive=True)` and a cwd subtree write claim.
3. `access="read"` remains accepted for wire compatibility but never weakens coordination.
4. A read-labelled terminal result reports `effective_access="exclusive"` and
   `access_reason="terminal_command_read_untrusted"`.
5. Unknown, ambiguous or malformed access values are rejected by the typed schema; the runtime does
   not infer permissions from a label.
6. No tool was removed; `full` keeps arbitrary execution and absolute paths.

## 6. Cancellation security model

- Cancellation is process-local and **not authentication**. Any caller that can reach the same
  server and learns an id can cancel that operation.
- Ids are 32 random hex bytes with a bounded 300 s terminal retention and a 256-entry registry cap.
- Unknown, malformed, duplicate-in-flight, completed and post-restart ids have explicit outcomes.
- `cancel_job` remains the durable-job contract; `cancel_operation` cannot cancel jobs or workflows.
- HTTP disconnects do not silently destroy durable work. stdio cancellation reaps the owned tree.
- Resource ownership is released only after the worker has reaped the tree or has quarantined an
  uncertain identity. A `process_cleanup_incomplete` result keeps the fence.
- `SECURITY.md` documents that ids must not be logged or exposed across trust boundaries.
- Durable audit targets use a keyed correlation, not the raw bearer id; the correlation is not a
  cancellation credential.

## 7. Windows validation

Executed on genuine GitHub-hosted Windows runners.

| Workflow | Runner | Python | Result |
| --- | --- | --- | --- |
| CI (`ci.yml`) | `windows-latest` | 3.10 and 3.12 | Full compile, Ruff, mypy, startup, health, durable ownership, pytest path |
| Platform Release Validation | `windows-latest` | 3.12 | **PASS** (`Release Gate (Windows, Python 3.12)`) |

The Windows platform gate verifies launcher syntax, clean launcher validation, full doctor,
startup/catalogue, quick benchmark and the workflow restart soak. The restart soak registers 152
tools, performs two MCP restarts, submits three durable jobs, and reports no leaked leases, orphan
operations or orphan events. Exact hosted stdout is not publicly downloadable without sign-in;
the job conclusion and annotations are the available evidence.

Windows-specific coverage includes `run_process`, `run_cmd`, `run_powershell`, CMD quoting, Job
Object creation/assignment, owned child creation, forced tree termination, restart/reconciliation,
atomic replacement and sharing-violation retries. UIA/desktop remains **UNVERIFIED**: a hosted
runner has no interactive desktop. The real v0.9.0 Windows `.agent_state` was not copied, opened or
modified.

## 8. Linux validation

Final local gates on the functional tree:

```text
python -m compileall -q core tools scripts tests     PASS
python -m ruff check .                                PASS
python -m mypy core tools scripts tests               PASS (313 files)
python main.py --check                                PASS (138 tools, unique names)
python -m scripts.health_check --json                 PASS
python -m scripts.platform_validation --json          PASS (linux-amd64)
git diff --check                                      PASS
```

Local platform validation (60 workflows, 3 durable jobs): 0 leaked leases, 0 orphan operations, 0
orphan events, RSS 75.055 → 77.160 MB (+2.105 MB), workflow restart soak 11.384 s. The full suite
was run in four bounded groups to fit the runner's five-minute ceiling: **2,505 passed, 24 skipped,
0 failed** (2,529 collected; group times 154 s, 202 s, 131 s and 94 s). The final P08-focused test
also passed after the 32,575-byte ceiling correction.

## 9. macOS validation

The hosted matrix ran both Python 3.10 and 3.12 on `macos-latest`, and both jobs passed at the
final functional SHA. Platform Validation also passed its macOS release gate. The earlier macOS
pytest failures were in the cancellation probe’s token construction, not in production cleanup; the
corrected token survived shell `exec` and the full macOS suite passed.

## 10. Browser acceptance

**VERIFIED** for a disposable local HTTP fixture. Public-site behavior and every browser tool are
not claimed by this result.

The earlier failure was correctly classified as a host-runtime problem, not a ComputerPilot defect.
Playwright could download Chromium, but the binary first failed on missing system libraries
(`libnspr4.so`). After supplying those libraries locally, the renderer aborted with
`FATAL:third_party/skia/src/ports/SkFontMgr_FontConfigInterface.cpp:163 Not implemented` (`SIGTRAP`)
because the container had neither fontconfig configuration nor fonts. A local fontconfig file plus
DejaVu fonts removed the crash. No security boundary was disabled; Chromium still used its normal
headless launch flags.

A real stdio MCP server (138 tools, Chromium 156.0.8078.4) then completed:

1. `browser_open_page` against the local fixture: HTTP 200 and title `Acceptance Fixture`.
2. `browser_snapshot`: four semantic nodes.
3. `browser_fill_semantic`: filled `Name` with `Alice`.
4. `browser_select`: selected `Color` value `blue`.
5. `browser_click_semantic`: activated `Submit`.
6. `browser_extract`: observed `Hello Alice blue`.
7. A second session filled `Bob`, selected the default `red`, and observed `Hello Bob red`; the
   first session still observed `Hello Alice blue`, proving context isolation.
8. Both sessions closed, the MCP process exited, and no Chromium process remained.

Evidence is retained at `artifacts/real-browser-acceptance.json`. Live crash recovery, idle
eviction and the complete download/upload matrix were not repeated against real Chromium; those
remain covered by deterministic lifecycle tests. No public website, account or production data was
used.

## 11. LSP acceptance

Real Pyright acceptance was executed against a disposable Python project:

| Check | Result |
| --- | --- |
| Provider | `pyright` |
| Node executable SHA-256 | `0adde40d1b7dfa57c8bc83cbc319758c3ec6ebba21363bb2fa752ea6b267ad1b` |
| Pyright entrypoint SHA-256 | `467344c79457ec74f7f7b6cb0f52c3c5ebc7ea77bf53edeb6d2dc6217687b507` |
| Definition / references / diagnostics | PASS |
| References after restart | 3; definition and diagnostics use real protocol traffic |
| Owned provider PIDs / orphans | 2 owned; 0 orphan PIDs |
| Edit diagnostics | 1 (`reportArgumentType`) |
| Diagnostics after restart | 0 (clean) |
| Restart / cleanup | PASS; workspace removed |

The acceptance used a repo-local ephemeral workspace and the real provider process. The complete
JSON evidence is retained at `artifacts/real-lsp-python.json` in the validation
workspace. Other providers are **UNVERIFIED**; mocked provider tests are not presented as real
acceptance.

### 11.1 Pip launcher resolution

The earlier native-session investigation recorded a supported-path defect: the pip
`pyright-langserver` console launcher resolves its Node runtime through `PATH`, while the LSP
transport deliberately launches providers with a minimal environment containing only the pinned
provider directory. Without a Node directory the launcher attempted a network-dependent
`nodeenv` install and the session failed before document symbols.

The fix adds a bounded launcher-runtime lookup: when the provider executable name starts with
`pyright-langserver`, the transport resolves `node` with `shutil.which` and adds only that
directory to the child `PATH`. `PYTHONPATH`, `PYTHONHOME`, `NODE_OPTIONS`, `NODE_PATH`, shell
loader variables and credentials remain excluded. Three regression tests cover the lookup,
environment construction, provider launch and the one-shot command path. A final real acceptance
against the ordinary pip launcher passed; see section 23.

## 12. SQLite and migration

Schema v12 adds dispatch identity columns and native claim recovery. Tests cover fresh
initialization, v5/v9/v10/v11 upgrades, v12 reopen, interrupted upgrade rollback, WAL behavior,
concurrent readers/writers, pending/active/quarantined claims, durable jobs, incomplete dispatch
markers, startup reconciliation and unsupported downgrade behavior. The Windows CI pytest job
runs the same synthetic migration/recovery tests; the Platform release gate itself does not run
pytest.

A real v0.9.0 Windows `.agent_state` copy was **not** opened, copied or modified. Byte-level
migration from the user’s private production database remains **UNVERIFIED**; this is intentional
data-safety scope, not a skipped test.

## 13. Recovery and crash behavior

- Unknown ownership is preserved and fenced; no uncertain side effect is replayed.
- A dead owner with a recorded child that has exited releases ownership as
  `stale_native_owner_command_exited`.
- A live orphan stays quarantined as `stale_native_owner_command_alive`.
- A missing dispatch marker stays quarantined as `stale_native_owner_dispatch_unknown`; this is the
  safe Windows outcome observed by the recovery probe.
- Pending never-dispatched claims release as `stale_native_owner_never_dispatched`.
- A live owner is never touched. The reconciliation scan is admission-triggered, bounded to 64 rows
  and 8 conflicts, and never creates a second recovery authority.

## 14. Soak and resource measurements

A genuine multi-minute soak ran for **181.218 s** through a real stdio MCP client against a
disposable checkout and fixture:

| Measurement | Result |
| --- | --- |
| MCP tool calls / success / failure | 10,083 / 10,083 / 0 |
| Throughput | 55.64 calls/s |
| Latency p50 / p95 / max / mean | 18.787 / 158.986 / 863.873 / 59.738 ms |
| Terminal process + shell calls | 1,265 + 1,230 |
| File write + read calls | 1,231 + 1,232 |
| Durable jobs submitted / succeeded | 59 / 59 |
| Cancellations requested / observed / follow-up command | 17 / 17 / 17 |
| Browser open/action/close cycles | 2 |
| SQLite contention errors | 0 |
| Resource requests / outstanding / quarantined | 3,787 released / 0 / 0 |
| Server CPU | 154.47 s (85.24% of one core) |
| Server RSS baseline → peak | 117.699 → 134.258 MB (+16.559 MB) |
| Sum-of-process-tree RSS peak | 632.906 MB (includes transient Chromium/job children; shared pages are counted more than once) |
| Server file descriptors baseline → peak | 8 → 25 |
| Server threads / child processes peak | 15 / 8 |
| Orphan processes after shutdown | 0 |
| Server and Chromium after shutdown | absent |
| SQLite files after run | 3,407,872 bytes |
| Final health check | PASS |

The machine-readable measurement is `artifacts/soak-acceptance.json`. The workload mixed four bounded
workers (read/write, `run_process`, `run_shell`, health) with a durable-job loop, a cancellation loop,
and browser session cycles. State used a disposable checkout's
`.agent_state`; no user or Windows runtime state was read. The 59 jobs and all 3,787 resource requests
finished released, with zero orphan claim rows, so the retained claim rows are history rather than
active leases. No unbounded growth or leaked process was observed. These are bounded observations,
not a multi-hour leak proof.

Bounded restart soak (40 workflows, 2 runtime restarts, 2 uncertain cases): 37 completed, 3 failed,
2 uncertain reconciled, 2 durable jobs succeeded, 0 leaked leases, 0 orphan operations/events, RSS
75.004 → 77.152 MB (+2.148 MB). A second 40-workflow run with no uncertain cases measured RSS
75.023 → 76.723 MB (+1.699 MB). The local platform gate additionally ran 60 workflows with 3 jobs
and measured RSS 75.055 → 77.160 MB (+2.105 MB), with no leaks. The 60-workflow platform soak
expanded the SQLite database, then checkpointed WAL and reduced the total database footprint after
retention cleanup. No process, lease or event leaked.

## 15. Extension and tunnel

**PARTIALLY VERIFIED.** The real local extension transport/lifecycle tests, bounded stdio transport
tests and tunnel offline tests passed together: **62 passed in 21.71 s**
(`tests/test_extension_live_lifecycle.py`, `tests/test_extension_transport.py`,
`tests/test_tunnel_runtime.py`). They exercise subprocess startup, discovery, registration, bounded
request/response, timeout/cancellation, forced reaping, environment filtering, secret redaction and
zero-extension native behavior. Tunnel tests cover platform asset selection, digest/checksum
verification, safe extraction, update locking, explicit profile selection and offline-cache
fallback.

A live authenticated Secure Tunnel connection and a real third-party extension still require
authorized credentials that were not available; those remain **UNVERIFIED**. No token was requested,
printed or committed, and no unauthenticated tunnel connection was attempted.

## 16. Security review

The review covered full-capability preservation, caller-controlled access labels, cancellation
bearer exposure, process-tree cleanup, Windows Job Object ownership, POSIX process-group ownership,
stale-claim release, unknown effects, SQLite migration, path aliasing/symlinks, external MCP output,
schema truthfulness, network boundaries, logging confidentiality and admission fairness.

Confirmed findings were fixed: terminal access truthfulness, cancellation identity documentation and
edge behavior, cancellation-id persistence in audit records, POSIX-only typing in a shared path,
and Windows catalogue/count gates. No unsafe read concurrency remains for arbitrary commands. No
secret canary or credential was committed.

## 17. Test evidence summary

| Gate | Result |
| --- | --- |
| Full pytest (hosted CI at `c33004e`, Linux/macOS) | **2,516 passed, 24 skipped, 0 failed** |
| Full pytest (hosted CI at `c33004e`, Windows) | **2,526 passed, 14 skipped, 0 failed** |
| Full pytest (local Linux, four bounded groups, earlier tree) | 2,505 passed, 24 skipped, 0 failed |
| Full pytest (local Windows, grouped, earlier tree) | 2,505 passed, 35 skipped, 0 failed |
| Terminal schema / access | PASS |
| Cancellation (stdio, HTTP, restart, capacity) | PASS |
| Windows Job Object / ownership | PASS in CI |
| Recovery / stale claims / no replay | PASS |
| SQLite v12 migration and rollback | PASS |
| Real stdio MCP | PASS |
| Real HTTP MCP cancellation | PASS |
| Real LSP (Pyright) | PASS |
| Real browser (local fixture, stdio MCP) | PASS |
| Extension/transport/tunnel-offline tests | PASS (62) |
| Multi-minute live MCP soak | PASS (10,083 calls, 181 s) |
| Workflow restart soak | PASS, no leaks |

## 18. CI and hosted evidence

Temporary branch push filters are present in `.github/workflows/ci.yml` and
`.github/workflows/platform-validation.yml` so the existing workflows validate this branch. No PR,
merge, release or `main` push was created.

All final hosted jobs ran at `c3496172773654534eb0307f41981852f15a8bba`.

| Workflow | Job | Runner | Result | Link |
| --- | --- | --- | --- | --- |
| CI | Test & Validate (Linux, Python 3.10) | `ubuntu-latest` | PASS | https://github.com/dibbed/computerpilot-mcp/actions/runs/42118454560 |
| CI | Test & Validate (Linux, Python 3.12) | `ubuntu-latest` | PASS | https://github.com/dibbed/computerpilot-mcp/actions/runs/42118454560 |
| CI | Test & Validate (macOS, Python 3.10) | `macos-latest` | PASS | https://github.com/dibbed/computerpilot-mcp/actions/runs/42118454560 |
| CI | Test & Validate (macOS, Python 3.12) | `macos-latest` | PASS | https://github.com/dibbed/computerpilot-mcp/actions/runs/42118454560 |
| CI | Test & Validate (Windows, Python 3.10) | `windows-latest` | PASS | https://github.com/dibbed/computerpilot-mcp/actions/runs/42118454560 |
| CI | Test & Validate (Windows, Python 3.12) | `windows-latest` | PASS | https://github.com/dibbed/computerpilot-mcp/actions/runs/42118454560 |
| Platform Validation | Linux release gate | `ubuntu-latest` | PASS | https://github.com/dibbed/computerpilot-mcp/actions/runs/42118454569 |
| Platform Validation | macOS release gate | `macos-latest` | PASS | https://github.com/dibbed/computerpilot-mcp/actions/runs/42118454569 |
| Platform Validation | Windows release gate | `windows-latest` | PASS | https://github.com/dibbed/computerpilot-mcp/actions/runs/42118454569 |

All six CI matrix jobs and all three Platform Validation jobs passed. This closes the earlier
Windows P08 failure at `e530068`: the corrected normalization retained the frozen `dd943…` digest,
the measured catalogue byte budget is 32,575, and the temporary digest probe is absent from the
final tree. Hosted logs require sign-in; public job conclusions and annotations are the available
evidence.

### Earlier hosted evidence

- CI at `7c67999`: Windows/macOS tests exposed the catalogue hash, snapshot scheduling and
  cancellation-token assumptions; Linux passed.
- Platform at `7c67999`: Windows, Linux and macOS release gates passed.
- CI at `e530068`: Linux and macOS green; the Windows job failed only P08.
- CI at `42115846245`: the Windows byte-bound failure was isolated to the 32,575-byte assertion;
  corrected at `c349617`.

## 19. Final suite and gates

| Gate | Status |
| --- | --- |
| compileall | PASS |
| Ruff | PASS |
| mypy (313 files; native, `--platform win32`, `--platform linux`) | PASS |
| mypy `--platform win32` | PASS |
| MCP startup (138 POSIX tools) | PASS |
| health | PASS |
| platform validation (linux-amd64) | PASS |
| full pytest (local Linux groups, earlier tree) | 2,505 passed, 24 skipped |
| full pytest (hosted CI at `c33004e`) | Linux/macOS **2,516 passed, 24 skipped**; Windows **2,526 passed, 14 skipped** |
| multi-minute live MCP soak | PASS (181.218 s, 10,083 calls, 0 failures) |
| real browser acceptance (local fixture) | PASS |
| extension/transport/tunnel-offline tests | PASS (62) |
| diff/whitespace | PASS |

## 20. Commit sequence

The 20 commits after `c494591` are:

- `c4f64b9` `fix(terminal): align access metadata with exclusive runtime`
- `13a6782` `fix(cancellation): document bearer identity and cover HTTP edges`
- `0954097` `ci: run hosted matrix for cross-platform hardening branch`
- `0a1246b` `fix(recovery): guard POSIX group liveness for Windows typing`
- `709ba09` `test(catalog): reconstruct Windows metadata before terminal fields`
- `833e85d` `fix(platform): annotate failed validation sub-step`
- `7c67999` `fix(runtime): include cancel_operation in Windows tool-count gates`
- `45581e4` `ci: annotate pytest failure node ids`
- `b36afaa` `fix(catalog): preserve access field in frozen Windows metadata`
- `abe05e3` `test(snapshots): wait for maintenance scheduling on Windows`
- `bfd012e` `ci: annotate pytest failure messages`
- `4c2f606` `test(catalog): normalize historical Windows descriptions`
- `bb0c4a4` `test(recovery): accept conservative dispatch-unknown quarantine`
- `e530068` `test(cancellation): retain token through POSIX shell exec`
- `6173b49` `fix(terminal): prevent cancellation capability exposure in audit`
- `f2d9b10` `chore(ci): add temporary Windows catalogue digest probe`
- `27ebee4` `fix(ci): make temporary Windows digest probe importable`
- `58bbb8d` `test(catalog): restore frozen Windows P08 normalization`
- `edda804` `chore(ci): remove temporary Windows digest probe`
- `c349617` `test(catalog): budget post-baseline cancellation descriptor`

The probe commits were temporary diagnostics and are removed from the final tree. A documentation-only
follow-up commit records this report; it does not change the functional tree. No force-push, merge,
tag, release or `main` mutation occurred.

## 21. Remaining risks and unverified scope

1. Windows UIA/desktop interaction is **UNVERIFIED**; hosted runners have no interactive desktop.
2. Real browser acceptance is **VERIFIED** only on a disposable local fixture. Public sites,
   downloads/uploads, real crash recovery and idle eviction were not exhaustively exercised live.
3. Authenticated tunnel and live third-party extension acceptance are **UNVERIFIED** without
   credentials.
4. A real v0.9.0 Windows `.agent_state` migration was not performed; synthetic v12 migration is
   verified.
5. Cancellation ids are bearer capabilities, not authentication. Anyone who can reach the server
   and learn an id can cancel that in-flight operation.
6. Arbitrary terminal commands are intentionally serialized per host; formerly read-labelled
   commands lose concurrency. Native typed readers retain theirs.
7. The runtime is fully privileged by design and is not an OS sandbox.
8. Windows Job Object cleanup and macOS POSIX group cleanup have platform coverage, but exotic
   process trees and kernel-level races cannot be exhaustively proven.
9. Hosted logs require authentication; public evidence is limited to job conclusions and annotations.
   Authenticated CLI logs were used to read exact suite counts, and those counts can change as tests
   are added.
10. The 181-second soak is a bounded observation, not a multi-hour leak proof. Its RSS tree peak
    includes shared Chromium/job pages and therefore overstates unique memory.

## 22. Readiness assessment

**Cross-platform candidate — conditionally ready for controlled use, not an unconditional
production release.**

The full Linux verification baseline is retained; all six Windows/Linux/macOS CI jobs and all three
Platform Validation jobs are green; terminal schema truthfulness is fixed; cancellation is documented
and tested as a bearer capability; migration/recovery is fail-closed; the 181-second live soak and
real local browser acceptance show no leak or orphan; and the extension/tunnel-offline suite passes.
The remaining blockers are explicitly scoped: Windows UIA/desktop, authenticated tunnel/live extension
acceptance, public-site browser coverage and a real private Windows database migration. Those must not
be represented as verified.

## 23. Final integration — pip Pyright launcher and merge readiness

A supported-path defect remained after the acceptance recorded in section 11: the pip
`pyright-langserver` console launcher could not resolve `node` from the deliberately minimal
provider `PATH`. It attempted a network-dependent `nodeenv` fallback and failed before
`source_index`. The direct-Node provider path was unaffected, which is why earlier acceptance
passed.

The fix in `tools/language/transport.py` adds only the discovered Node directory to the child
`PATH` for launcher executables beginning with `pyright-langserver`; all other parent variables
remain excluded. Three regression tests cover the lookup, environment construction, provider
launch and one-shot command path. A real acceptance run against the ordinary pip launcher exited
0 with `source_index: REPLIED`, PASS for definitions, references, diagnostics, edit
synchronization, restart and cleanup, eight owned provider processes, no orphan PIDs and the
ephemeral workspace removed.

Final Windows integration gates on the complete tree:

| Gate | Result |
| --- | --- |
| `compileall` | PASS |
| Ruff | PASS |
| mypy (313 files; native, `--platform win32`, `--platform linux`) | PASS |
| `main.py --check` | PASS, 152 unique tools |
| Health | PASS, 5/5 checks |
| Windows platform validation | PASS |
| Full pytest (ungrouped, serial) | **2,508 passed, 35 skipped, 0 failed** (621.24 s) |
| Real Pyright pip-launcher acceptance | PASS |
| `git diff --check` | PASS |

Hosted CI and Platform Validation for the final integration commit are a separate gate. This
section does not publish a release, create or move tags, publish packages, bump versions or deploy.
