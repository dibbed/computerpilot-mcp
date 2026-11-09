# Windows Runtime Recovery and Regression Report

- Date: 2026-11-09
- Host: Windows 10 (`10.0.19045`), amd64
- Runtime: ComputerPilot MCP `0.9.0`, `full` tool profile
- Base revision: `0d1d1a2690723ec2ede36fb69f3802bd1b2b97b8` (`origin/main`)
- Working branch: `fix/windows-runtime-cleanup-recovery`
- Pull request: [#94](https://github.com/dibbed/computerpilot-mcp/pull/94)
- Implementation head verified: `cece44b04b97a902717514b6b7069656616180f0`.
- Status labels: **VERIFIED** (observed), **FIXED AND VERIFIED** (changed and proven), **UNVERIFIED** (not proven), **NOT AUTHORIZED** (requires separate approval).

## Executive summary

The live runtime became `degraded` for three independent reasons, and repairing the
first exposed a fourth:

1. Windows Job Object cleanup did not own the command tree started through the
   `pwsh.exe` app-execution alias. `TerminateJobObject` stopped only the alias root,
   the worker observed surviving descendants, and the job was marked failed.
2. That cancelled durable job injected its internal resource capability into the
   pytest process. `tests/test_job_worker.py` then reserved an opaque child
   (`unknown-child` / `unknown-owner`) against the **live** workflow store. Opaque
   capacity has no PID identity, so death-proof reclamation could never release it
   and the parent request stayed quarantined permanently.
3. Owned-process cleanup imported `core.executor`, so a bootstrap interpreter that
   installed pywin32 after startup could not settle escaped processes at all.
4. Verifying cleanup strictly pushed LSP workspace teardown past a 2-second drain
   budget. A real pinned-Pyright acceptance run failed with
   `AssertionError: Session did not drain` on this branch while `origin/main`
   passed, proving a regression that the source fix had introduced.

The branch now reaps probed descendants by PID plus creation time, verifies Job
Object termination and quiescence, keeps cleanup independent of runtime imports,
settles opaque permits only through a guarded and audited API, isolates tests from
ambient runtime authority, bounds LSP teardown to the owned-process contract, and
uses a Windows Toolhelp snapshot to avoid psutil's slow cold process-table walk.
The live quarantine was settled through that API after a consistent backup. The
latest full suite and all hosted gates pass on `cece44b`; current databases remain
consistent with no quarantined requests or unresolved capacity.

## 1. Initial runtime state

**VERIFIED** from the live workflow store (read-only), the job records, and the
runtime health panel:

| Item | Value |
| --- | --- |
| Health | `degraded` — `resource_ownership_unresolved` |
| Workflow schema | `12` |
| SQLite `quick_check` | `ok` |
| Quarantined resource requests | `1` |
| Request category / reason | `native.job` / `managed_effect_or_cleanup_unresolved` |
| Owner token | `native-owner-20316-1794203233.543988-a0b559981371481a9b92529886e0fced` |
| Blocking capacity | `unknown-child`, category `analyzer`, owner `unknown-owner`, state `active` |
| Active / pending requests | `0` / `0` |
| Running / queued workflows and jobs | `0` / `0` |
| Failed job | `f71c93115cec42a3b20c6d13352ec7e8`, main PID `6156`, worker PID `20316` |
| Job error | `Process-tree cleanup did not stop every child.` |

Timeline (UTC):

- `05:47:14.301` — native resource request created.
- `05:50:27.610` — cancellation requested for the durable job.
- `05:50:27.893` — termination targeted PID `6156`.
- `05:50:31.499` — `unknown-child` capacity created, four seconds **after** cancellation.
- `05:52:42.491` — PIDs `20180` and `2248` killed manually outside the product path.

## 2. Root causes

### 2.1 Windows app-alias escape — **VERIFIED**

A probe launched the same command through `spawn_owned_process`:

- Direct `sys.executable` spawn: contained by the Job Object, no survivors.
- `pwsh.exe` app-execution alias spawn: the alias root was in the Job Object, the real
  Python tree was not. Pre-fix output showed surviving `alive_after_terminate` PIDs.

The product treated process-table descendants outside Job Object authority as
unkillable and reported `process_cleanup_incomplete`, leaving the request
quarantined even after the surviving PIDs disappeared.

### 2.2 Test-capability contamination and opaque capacity — **VERIFIED**

`scripts/job_worker.py` injects `COMPUTERPILOT_INTERNAL_RESOURCE_CAPABILITY` into the
durable job process. When pytest ran inside that job, the intentional unresolved
child fixture wrote `unknown-child` into the live `.agent_state/workflows.sqlite3`.
The test environment was isolated by state path but not by resource authority.

`release_dead_resource_capacity` only parses `pid-<pid>-<created>-...` owners. Rows
with opaque tokens had no release path, so the parent could never be resolved.

### 2.3 Cleanup coupled to runtime imports — **FIXED AND VERIFIED**

`OwnedProcess._reap_identities` imported `core.executor` to terminate an identity
tree. A bootstrap interpreter that installed pywin32 after it started could not
import the MCP runtime, so cleanup failed exactly when escaped descendants needed
settling.

### 2.4 Teardown budget and cold enumeration regression — **FIXED AND VERIFIED**

Discovered during this session by the real pinned-Pyright acceptance script and by
`tests/test_job_admission.py`, both of which failed on this branch while
`origin/main` passed:

- `LspSessionManager.close_workspace` joined the session owner for **2 s**, while the
  delegated Job Object teardown contract allows up to **5 s** (quiescence plus
  identity-checked reaping). Normal cleanup crossed the join deadline, the session
  became `QUARANTINED`, and the acceptance run reported `Session did not drain`
  even though `close_all` later drained it with zero orphan PIDs.
- The escaped-descendant PPID scan used `psutil.process_iter`, whose first full
  enumeration measured **1.64–2.23 s** on this host (warm calls: `0.016 s`). Every
  fresh job-worker process therefore paid that cost inside its first
  `close_ownership`, so a `0.35 s` job took ~`2.3 s` to settle. With two concurrent
  workers, `test_real_workers_respect_max_running_jobs` stalled: 6 jobs succeeded
  and 2 were still `running` after its 20 s deadline, versus a `7.75 s` pass on
  `origin/main`.

An intermediate attempt gated the scan on "root no longer provably alive". That was
reverted after it left escaped descendants unreaped and the same admission test
still stalled; coverage must not be traded for speed. The final fix keeps the full
scan and uses `CreateToolhelp32Snapshot` to collect PID/parent pairs, querying
creation times only for candidates. `psutil.process_iter` remains the fallback if
the Windows snapshot cannot be read.

## 3. Code changes

### 3.1 `core/windows_job.py` — **FIXED AND VERIFIED**

- Captures PID plus creation time for the root and visible descendants at ownership
  construction and again on `wait`/`poll`.
- Persists captured identities so descendants remain identifiable after the root and
  intermediate launchers exit.
- After `TerminateJobObject`, verifies Job Object quiescence and reaps only processes
  whose PID **and** creation time still match a captured identity.
- Treats an exited process with a retained handle as terminated.
- Never signals a bare process-table PID; identity mismatch remains fenced.
- Keeps the escaped-descendant PPID scan and reads PID/parent pairs from a bounded
  Toolhelp snapshot (`CreateToolhelp32Snapshot` plus `Process32EntryW`), fetching
  `create_time` only for candidates that match a known parent. psutil's enumeration
  remains a fallback when the snapshot is unavailable.
- Terminates identity trees with psutil only, removing the runtime import from the
  cleanup path.

### 3.2 `core/workflow_store.py` — **FIXED AND VERIFIED**

`release_unidentified_resource_capacity()` requires the parent request to exist,
match the submitted owner token, and be `quarantined`; requires explicit admission
IDs all linked to that request; rejects every PID-identifiable row with
`capacity_identity_requires_death_proof`; settles only `active`/`pending`/
`quarantined` rows; is idempotent; records bounded operator evidence; commits
atomically.

### 3.3 `tests/conftest.py` — **FIXED AND VERIFIED**

Removes ambient `MCP_*` and `COMPUTERPILOT_*` variables before application
configuration is imported, and clears the resource capability environment and
ContextVar around every test. Tests can still exercise the capability explicitly;
they cannot inherit a live job's authority.

### 3.4 `tools/language/sessions.py` — **FIXED AND VERIFIED**

`close_workspace` now drains for up to 5 s (the owned-process teardown bound) instead
of 2 s, and still quarantines when a session genuinely does not finish.

### 3.5 Documentation

`docs/architecture.md`, `docs/tooling.md`, and `CHANGELOG.md` describe the
identity-checked Windows cleanup contract and the guarded opaque settlement path.

## 4. Verification

### 4.1 Windows cleanup probe — **VERIFIED**

```text
direct_python terminate_tree {'terminated_pids': [1944, 5836, 6156, 7556, 17236], 'alive_pids': [], 'job_terminated': True, 'job_quiescent': True}
direct_python alive_after_terminate []
pwsh_alias terminate_tree {'terminated_pids': [8968, 9036, 15068, 20940, 23916, 24556], 'alive_pids': [], 'job_terminated': True, 'job_quiescent': True}
pwsh_alias alive_after_terminate []
```

### 4.2 Automated suite

- `f579351` (before the LSP budget fix), isolated clean worktree, ambient `MCP_*`
  stripped, disposable `TEMP` and state:
  `2517 passed, 35 skipped, 0 failed` in `690.08 s`; JUnit
  `tests=2552 failures=0 errors=0 skipped=35`.
- `cece44b04b97a902717514b6b7069656616180f0` (current implementation head),
  detached verification worktree with disposable state:
  `2518 passed, 35 skipped, 0 failed` in `652.18 s`; JUnit
  `tests=2553 failures=0 errors=0 skipped=35`; pytest exit `0`. The retained
  `full.log` and `junit.xml` were written at `2026-11-09T08:05:09Z` and
  `2026-11-09T08:05:08Z`. No pytest process remained after completion.

### 4.3 Static, startup, and health checks on `cece44b` — **VERIFIED**

| Command | Result |
| --- | --- |
| Latest hosted CI matrix (compile, Ruff, Mypy, startup, health, pytest) | `6/6` jobs succeeded on `cece44b` |
| `python main.py --check` in a detached worktree | `ok`, `tool_count=152`, unique names; exit `0` |
| `python -m scripts.health_check --json` in a detached worktree | `ok`; import, MCP startup, tool registration, filesystem, terminal all passed; `tool_count=152` |
| Explicit `mypy --platform win32` | Passed on `5758e74`; latest native Windows CI Mypy step passed on `cece44b` |

The startup and health commands used the isolated worktree's temporary state, not
the live `.agent_state`. The health check's 152-tool catalog is its default
disposable profile; it does not replace the live snapshot below, which had the
configured extension enabled.

### 4.4 Platform release gate on Windows — **VERIFIED**

`python -m scripts.platform_validation --json` passed on `cece44b` in a fresh
detached Windows worktree, with ambient `MCP_*` / `COMPUTERPILOT_*` variables
removed and disposable `TEMP`, `TMP`, and `.agent_state`:

| Step | Elapsed |
| --- | --- |
| clean launcher validation (`START_MCP.bat`) | `64.750 s` |
| full local doctor | `8.093 s` |
| quick platform benchmark | `35.454 s` |
| workflow restart soak (60 workflows, 2 restarts, 3 durable jobs) | `13.718 s` |

Soak outcome: `leaked_leases=0`, `orphan_operations=0`, `orphan_events=0`,
`protected_unresolved_rows=0`, `cleanup_errors=0`,
`durable_jobs submitted=3 succeeded=3 active=0`, `rss_before=69.363 MB`,
`rss_after=71.938 MB`, growth `2.574 MB`.

The latest hosted Platform Release Validation also passed `3/3` jobs on
`cece44b`, including Windows, Linux, and macOS.

### 4.5 Real LSP acceptance (pinned Pyright) — **FIXED AND VERIFIED**

Same script, same provider, same host:

| Tree | Exit | `first_cleanup` | `restart_cleanup` | `cleanup` | Orphan PIDs |
| --- | --- | --- | --- | --- | --- |
| `origin/main` `0d1d1a2` | `0` | PASS | PASS | PASS | `[]` |
| branch before fix | `1` | missing (`Session did not drain`) | missing | PASS | `[]` |
| final branch `5758e74` | `0` | PASS | PASS | PASS | `[]` |

Coverage: pin verification, definition, cross-file definition, references,
diagnostics, edit plus document sync, diagnostics after edit and restore, restart,
and cleanup. Measured workspace close after the fix: `1.953 s`, snapshot
`active=0 quarantined=0`. The earlier `lsp_provider_unavailable` in the isolated
clone was missing state configuration: the live install pins `pyright` (and
TypeScript/Intelephense) in `.agent_state/config/lsp-providers.json` with hashes and
the repository as trusted root. The later `cece44b` change is limited to Windows
process enumeration and does not alter the LSP session code; latest full-suite, CI,
and platform gates passed on that head.

### 4.6 Live recovery

**VERIFIED** consistent backup (SQLite backup API, WAL included):

- `C:\Users\Meliodas\Downloads\computerpilot-backups\pre-runtime-recovery-20261109-095455`
- `workflows.sqlite3` `10203136` bytes, SHA-256
  `a9eea0d0bf03a50d5b415abc71706e3276582662ac5806c58b6f4314111d17ee`
- `jobs.sqlite3` `245760` bytes, SHA-256
  `bc88a958ebb3b60025abb2ec466e5f56e3bc9bf609289e70e33adf86a82d59e1`
- both `PRAGMA quick_check = ok`

The exact opaque row was settled through the guarded API, not SQL, after confirming
job PID `6156` and worker PID `20316` were dead by PID plus creation time, zero
active/pending requests, zero queued/running workflows and jobs, and exactly one
linked child matching the recorded owner.

Comparison against the backup immediately after the guarded settlement:

| Measurement | Backup | Immediately after settlement |
| --- | --- | --- |
| `quick_check` / schema | `ok` / `12` | `ok` / `12` |
| workflows | `36` | `36` |
| workflow operations | `102` | `102` |
| workflow events | `184` | `184` |
| resource requests | `7697` | `7697` |
| resource claims | `15831` | `15831` |
| runtime capacity rows | `41` | `41` |
| nonterminal requests | `1` | `0` |
| nonterminal capacity | `1` | `0` |

No history was deleted; only the two fenced rows changed state.

Fresh read-only check on `2026-11-09`:

| Store | Size | SHA-256 | Integrity |
| --- | ---: | --- | --- |
| Current `workflows.sqlite3` | `10211328` bytes | `49eff4783c124fd6fd446c5bfe4239e54612b9be1f137be98e3619f99f163a9d` | `PRAGMA integrity_check = ok` |
| Current `jobs.sqlite3` | `245760` bytes | `e2c9b3d9c15635cbd3d3d8125385bdbaba3bf7586f408f28d237b8712739216c` | `PRAGMA integrity_check = ok` |

Current `workflow_resource_requests` contains `7700` rows, all `released`; current
runtime capacity contains `41` rows, all `released`; quarantined requests,
active/pending requests, unresolved capacity, and workflow leases are all `0`.
The workflow table has `4 completed` and `32 failed` records, with no queued or
running workflows. The job store has `5 cancelled`, `44 failed`, and `66 succeeded`
jobs, with no queued or running jobs. The store reports `10 created`, `32 failed`,
and `60 succeeded` workflow operations; according to
`WorkflowStore.health_summary`, unresolved operations are the `uncertain`,
`reconciling`, or `unresolvable` states, all of which are `0`.

The full suite, startup check, and platform gate ran from detached worktrees whose
state defaulted under each worktree. The original workflow and job database file
timestamps predate those runs; the live databases were opened read-only for these
checks.

### 4.7 Latest live runtime health snapshot — **HEALTHY; REQUERY UNAVAILABLE**

The last directly captured live `server_health` snapshot after recovery was healthy
at runtime generation `d86fa44b02e74a4d93e0631b7b706215`. In this continuation the
current tool session had no ComputerPilot MCP connector to query the already-running
stdio server again. The live `local_pc_mcp.py` process was still present (PID
`21880`, started `2026-11-09 10:03` local time); it was not signalled or restarted.
The fresh startup/health check above used disposable state and proves the current
source starts cleanly, but does not identify the code already loaded in that live
process. Treat the live server's latest direct health result below as the last
observed snapshot, not a new RPC from this continuation.

Runtime generation `d86fa44b02e74a4d93e0631b7b706215`:

```json
{
  "health_status": "healthy",
  "ok": true,
  "degraded_reasons": [],
  "tool_count": 153,
  "tool_profile": "full",
  "version": "0.9.0",
  "quarantined_resource_request_count": 0,
  "active_resource_request_count": 0,
  "pending_resource_request_count": 0,
  "runtime_capacity_counts": {"analyzer:released": 41},
  "active_jobs": 0,
  "running_jobs": 0,
  "queued_jobs": 0,
  "unresolved_workflow_operations": 0,
  "active_workflow_leases": 0,
  "lsp": {"available": true, "enabled": true, "quarantined": 0},
  "extensions": {"enabled": true},
  "orphaned_jobs": 0,
  "tracked_background_processes": 0,
  "resource_pressure": "normal"
}
```

Capabilities remained enabled: browser, desktop input, desktop screenshot, semantic
UI, secure tunnel, PowerShell, process-tree ownership, installed software, system
services, Windows.

### 4.8 GitHub Actions — `cece44b`

| Workflow | Result | URL |
| --- | --- | --- |
| CI (`6` matrix jobs) | success | https://github.com/dibbed/computerpilot-mcp/actions/runs/42582217418 |
| Platform Release Validation (`3` OS jobs) | success | https://github.com/dibbed/computerpilot-mcp/actions/runs/42582217415 |
| Release Packaging Smoke (`1` job) | success | https://github.com/dibbed/computerpilot-mcp/actions/runs/42582217411 |

`731ce42` produced a transient Windows `Platform Release Validation` failure
(`clean launcher validation: exit 15: ERROR: Startup Doctor: ToolError`). The same
code path passed on `f579351`, `5758e74`, and `cece44b`; the gate was not bypassed
or weakened.

## 5. Regression coverage added

- `tests/test_windows_job_backend.py`: outside-job descendant reaping; escaped
  descendant after root exit; real `pwsh` app-alias tree probe.
- `tests/test_windows_job_unit.py`: identity-checked reaping without importing the
  runtime layer, PID-reuse rejection, and PPID scan using fast snapshot pairs.
- `tests/test_unidentified_capacity_recovery.py`: owner/state guards, exact-link
  atomicity, idempotence, PID-identity rejection, parent resolution.
- `tests/conftest.py`: ambient capability isolation for every test.
- Real Pyright acceptance is the regression proof for the LSP budget change; the
  real admission test is the regression proof for the scan performance change.

## 6. Status matrix

| Area | Status | Evidence |
| --- | --- | --- |
| Live quarantine identification | **VERIFIED** | Read-only schema-12 inspection |
| `pwsh` app-alias escape reproduction | **VERIFIED** | Pre-fix survivors; post-fix zero |
| Identity-checked Windows cleanup | **FIXED AND VERIFIED** | Probe, focused and full suites |
| Import-free cleanup | **FIXED AND VERIFIED** | Unit tests inject missing runtime import |
| Opaque capacity settlement API | **FIXED AND VERIFIED** | Unit/atomicity/idempotence tests; live settlement |
| Test capability isolation | **FIXED AND VERIFIED** | `tests/conftest.py`; full suite |
| LSP workspace close budget | **FIXED AND VERIFIED** | main vs branch acceptance comparison |
| Live backup and database integrity | **VERIFIED** | Backup hashes rechecked; current SQLite integrity checks pass |
| Live quarantine and capacity | **VERIFIED** | No quarantined, active, or pending requests; capacity rows all released |
| Latest captured live health snapshot | **HEALTHY; REQUERY UNAVAILABLE** | Healthy snapshot, 153 tools; fresh RPC unavailable in this session |
| Current-code startup and health | **VERIFIED** | Disposable-state health check and catalog check passed on `cece44b` |
| Hosted Linux/macOS/Windows CI | **VERIFIED** | `6/6` jobs succeeded on `cece44b` |
| Hosted platform validation | **VERIFIED** | `3/3` OS jobs succeeded; local Windows 60-workflow soak passed |
| Hosted packaging smoke | **VERIFIED** | `1/1` job succeeded on `cece44b` |
| PR merge / release | **NOT AUTHORIZED** | PR remains open; user requested no merge or release |

## 7. Residual risk and unverified areas

- **UNVERIFIED:** A broker that escapes before any identity snapshot exists can
  still leave an unattributable descendant. The product deliberately does not kill a
  bare PID; all reproduced paths had a live launcher lineage at capture time.
- **UNVERIFIED:** Test isolation depends on the repository-level `tests/conftest.py`.
  `--noconftest` or copying tests outside the repository can still inherit authority.
- **UNVERIFIED:** Interactive browser acceptance (fill/select/click/extract, two
  sessions) was verified before this change set and is covered by the browser
  lifecycle/semantics/transfer suites; it was not re-driven manually in this session.
- **UNVERIFIED:** UIA was exercised by the desktop suite on this host, not by a live
  window-targeting acceptance run.
- **UNVERIFIED:** The reliability evidence is a bounded platform gate (60-workflow
  soak, 2 restarts, 3 durable jobs, benchmark), not a multi-minute live MCP soak.
- **UNVERIFIED:** No external end-to-end tunnel reachability check was run in this
  continuation. An existing tunnel-runtime process was observed; it was not probed
  or changed.
- **UNVERIFIED:** A fresh `server_health` RPC and the loaded source revision of the
  already-running local MCP process could not be verified from this task's tool
  session. The latest direct live snapshot is healthy, and current source passes
  disposable startup/health checks; these do not prove the live process loaded the
  latest commit.
- The Windows Job Object contract still cannot make an external broker's process
  tree inherit job membership; the fix detects and reaps provable escaped
  descendants rather than changing broker behavior.

## 8. Git and verification commands

- Branch: `fix/windows-runtime-cleanup-recovery`
- Commits: `731ce42` (cleanup and settlement), `f579351` (import-free cleanup),
  `5758e74` (LSP teardown budget), `cece44b` (Toolhelp snapshot process scan)
- PR: https://github.com/dibbed/computerpilot-mcp/pull/94

```powershell
python main.py --check
python -m scripts.health_check --json
python -m scripts.platform_validation --json
```

The latest full-suite logs are
`C:\Users\Meliodas\AppData\Local\Temp\cp-verify-final-logs\full.log` and
`C:\Users\Meliodas\AppData\Local\Temp\cp-verify-final-logs\junit.xml`; that run used detached worktree
`C:\Users\Meliodas\AppData\Local\Temp\cp-verify-final` at `cece44b`.
The latest platform gate used detached worktree
`C:\Users\Meliodas\AppData\Local\Temp\cp-verify-windows-runtime-20261109`.
Both isolated runs removed ambient `MCP_*` / `COMPUTERPILOT_*` variables and used
disposable state. The Pyright acceptance above used the live install's pinned
provider configuration; its machine-specific executable paths are not repeated
here.
