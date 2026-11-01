# Cross-Platform Runtime Hardening — Final Report

Branch: `fix/cross-platform-runtime-hardening`
Integration base: `1847705` (merge of `fix/linux-reliability-hardening` at `b301309` onto `b88a2e1`)
Repository: `https://github.com/dibbed/computerpilot-mcp`
Environment: Ubuntu Linux x86-64, Python 3.12.3, MCP SDK 2.0.1, pytest 9.2.0
Windows execution environment: **not available to this run** (see §15).

Status legend: **VERIFIED**, **FIXED AND VERIFIED**, **PARTIAL**, **BLOCKED**, **UNVERIFIED**, **NOT APPLICABLE**.

## 1. Repository and branch identity

| Item | Value |
| --- | --- |
| Branch | `fix/cross-platform-runtime-hardening` |
| Original `main` baseline | `b88a2e17040036181ed99be3a0a0122c4dcb5e88` |
| Linux hardening branch | `fix/linux-reliability-hardening` @ `b301309962c00cb929f8241d4711c8c97f62eee2` |
| Integration merge | `1847705069fe1703cab3737d4afd2392a4cc38c5` (`--no-ff`, Linux history preserved) |
| `main` at verification | Unchanged at `b88a2e1`, verified with `git ls-remote` |

The merge commit's tree is byte-identical to the previously verified Linux tree (`b301309`), because
`main` is an ancestor of that branch. This report records verification performed on the tree after
the two new production fixes, not on the merge commit alone.

## 2. Original Linux integration baseline

The Linux hardening branch passed compileall, Ruff, mypy (314 files), startup/catalogue checks
(138 POSIX tools), health, platform validation, and the full suite: **2,494 passed, 24 skipped, 0
failed in 497 s**. Those results remain the baseline for the integrated Linux history. Its report is
[LINUX-RELIABILITY-HARDENING-REPORT.md](LINUX-RELIABILITY-HARDENING-REPORT.md), whose read-concurrency
claims are explicitly superseded by this work.

## 3. Previously incomplete issues

| ID | Issue | Status at start | Final status |
| --- | --- | --- | --- |
| G1 | Caller-declared `access="read"` can bypass exclusive coordination | Unresolved | **FIXED AND VERIFIED** |
| G2 | Quarantined stale claims can require another restart | Unresolved | **FIXED AND VERIFIED** |
| G3 | Windows native behavior not executed | Unverified | **BLOCKED** (no host) |
| G4 | Windows Job Object / POSIX lifecycle parity | Not verified | **UNVERIFIED** (platform-independent unit + static only) |
| G5 | Real browser and LSP acceptance | Not completed | **BLOCKED** (no browser/LSP install) |
| G6 | Post-integration stress and soak | Not completed | **PARTIAL** (bounded benchmark only) |
| G7 | Cross-platform migration verification | Incomplete | **VERIFIED** on POSIX fixtures |
| G8 | Final security review | Incomplete | **VERIFIED** for changed boundaries |
| G9 | Cross-platform report | Missing | **FIXED** (this document) |
| G10 | Final suite and CI evidence | Missing | **PARTIAL**: local suite **VERIFIED**, hosted CI **UNVERIFIED** |

## 4. Reproduction of G1

Reproduced with real MCP stdio calls over the integrated tree:

- Three concurrent `run_shell` calls with `command_text="sleep 0.5"` and `access="read"` completed in
  about 0.5 s, i.e. all three held read claims and overlapped.
- Two callers could label arbitrary writers as readers and both be admitted under read coordination.
- An exclusive caller was blocked only by the write claim on the shared subtree; a read-labeled
  writer to an alias or outside the claimed subtree was not represented as a writer at all.

This is a **coordination bypass, not an authorization bypass**: `access="read"` never granted OS
permissions, but it did remove the writer fence that the exclusive path promises.

## 5. Root causes

- `_managed_command_claims()` trusted the caller's `access` literal to choose
  `coordination_claim(exclusive=False)` and a read path claim.
- The shared-command gate only bounded how many read-labeled commands ran; it did not verify that
  the command was read-only.
- No executable/argument contract can prove an arbitrary process side-effect free: arguments,
  environment, configuration, plugins, scripts and descendants can write. A name allowlist is
  insufficient.
- For G2, native reconciliation was reachable only from `WorkflowStore.recover_interrupted()`;
  native command admission uses `WorkflowStore(..., recover=False)`, so an orphan that exited after
  the previous recovery stayed quarantined until a new store/recovery cycle.

## 6. Production fixes

### G1 — fail-closed terminal classification

- Every terminal command (`run_process`, `run_shell`, `run_cmd`, `run_powershell`) now always takes
  `coordination_claim(exclusive=True)` plus a write path claim on its cwd subtree.
- The public `access` field remains for wire compatibility but is a **hint only**; it cannot weaken
  coordination.
- When a caller requests `read`, the result includes `effective_access: "exclusive"` and
  `access_reason: "terminal_command_read_untrusted"`, so the downgrade is observable rather than
  silent.
- Native typed read tools keep their own read claims and safe concurrency. No host-wide lock was
  added to native readers, and no filesystem or execution capability was removed.
- Inherited-claim audit: `scripts/job_worker.py` already takes exclusive coordination plus a write
  cwd subtree for durable jobs; the workflow `run_durable_job` action does the same, and
  `verify_changes` is deliberately repository-write coarse. The remaining read-footprint workflow
  actions (`check_file`, `affected_tests`, `check_http`, `git_status`) do not dispatch arbitrary
  terminal commands. No second caller-trusted read path remains.
- The obsolete `core/command_concurrency.py` gate and the
  `MCP_COMMAND_SHARED_CONCURRENCY`/`command_shared_concurrency` setting were removed; they only
  governed an unsafe classification. `MCP_COMMAND_SHARED_CONCURRENCY` is now ignored because the
  setting no longer exists.

### G2 — bounded runtime reconciliation

`WorkflowStore.reserve_resource_claims()` now performs a lazy, bounded reconciliation **when a
conflicting claim is encountered**:

- It scans at most 64 native (`workflow_id IS NULL`, `native-owner-*`) rows that are pending,
  active, or quarantined **only** for `stale_native_owner_command_alive`; it excludes the incoming
  request id and inspects at most 8 conflicting claims.
- It acts only when the owner identity is already dead (`native_resource_owner_alive`), never on a
  live current server or process.
- A never-dispatched `pending` row is released as `stale_native_owner_never_dispatched`.
- An `active` row is released only when the durable dispatch marker exists and PID + creation time
  prove the child dead and no non-released child capacity remains.
- A live child, missing marker, or unresolved capacity is re-quarantined with the existing precise
  reasons. Every other quarantine reason, including unresolved effects and child-capacity cleanup,
  is never auto-released; no side effect is replayed and no process is killed.
- The scan is throttled to one per store per second, runs inside the admission transaction, and
  refreshes the conflict set once before deciding.
- Counters are exposed as `workflow_health.native_runtime_reconciliation`
  (`released`, `quarantined`, `examined`) alongside the existing startup counters.

## 7. Files modified

| File | Change |
| --- | --- |
| `tools/terminal/registry.py` | Exclusive claims for all terminal tools; effective-access reporting. |
| `core/config.py` | Removed dead shared-command setting and budget entry. |
| `core/command_concurrency.py` | Deleted; it existed only for unsafe caller-declared read scheduling. |
| `core/workflow_store.py` | Admission-time native claim reconciliation + counters. |
| `core/health_snapshot.py` | Publishes runtime reconciliation counters. |
| `tests/test_command_access_classification.py` | New real-MCP regression suite. |
| `tests/test_stale_claim_recovery.py` | New runtime reconciliation tests with real processes. |
| `tests/test_command_concurrency.py` | Deleted with the removed feature. |
| `docs/LINUX-RELIABILITY-HARDENING-REPORT.md` | Marks the old read-concurrency contract superseded. |
| `docs/CROSS-PLATFORM-RUNTIME-HARDENING-REPORT.md` | This report. |

## 8. Regression tests added

- Three concurrent read-labeled `run_shell` calls must serialize and report
  `effective_access="exclusive"`.
- A read-labeled writer cannot pass an active exclusive writer; measured wait ≥ 0.7 s.
- Two read-labeled `run_process` calls serialize.
- Independent native `read_file` calls remain fast/concurrent (no terminal-style serialization).
- Runtime reconciliation releases an orphan that exited after fencing, without reconstructing the
  store.
- A live orphan stays quarantined until it exits, then the same runtime releases it on a later
  admission attempt.
- Markerless ownership is preserved as `stale_native_owner_dispatch_unknown`.
- A live owner is never released or reclassified.
- A never-dispatched pending native claim is released during runtime.
- A claim quarantined for unresolved effects stays quarantined even after its dead child is gone.
- The health snapshot publishes both startup and runtime reconciliation counters.
- Startup recovery tests (exited child, live orphan, missing marker, live owner, SIGKILL) still pass.

## 9. Final execution-coordination contract

1. Native typed readers can hold read claims and overlap compatible readers.
2. Every arbitrary terminal command is a writer for coordination purposes, regardless of `access`.
3. Terminal commands conflict with native writers, durable jobs, workflows and other terminal
   commands through the host coordination claim and the cwd subtree write claim.
4. `access="read"` requests are admitted conservatively and report the effective classification.
5. Durable uncertainty is retained: unknown ownership is quarantined, not released.
6. Admission-time reconciliation only converts provably dead native ownership to `released`.

## 10. Full-permission behavior

No capability was removed. The `full` profile still registers 138 tools on POSIX (152 on Windows
per the static catalogue), including arbitrary process/shell execution, writes, Git, jobs,
workflows, documents, browser and platform-gated desktop tools. Absolute and project-relative paths
resolve exactly as before (`core/config.resolve_path`), and no repository-only sandbox was added.
The only change is which resource claims a terminal command takes.

## 11. Effective read/write classification

| Caller request | Native typed read | Terminal command |
| --- | --- | --- |
| default/exclusive | n/a | exclusive |
| `access="read"` | read claims where implemented | **exclusive**, reported in result |
| unknown/ambiguous | n/a | exclusive |

Native read claims were not weakened. The `access` input-schema text is frozen by the Windows
metadata golden guard; enforcement and the result fields are authoritative, and the stale sentence
"read commands may overlap" no longer describes runtime behavior.

## 12. Cancellation semantics per transport

Verified by reading the implementation and running the existing regression suites:

| Transport | Request cancellation | Disconnect | Explicit `cancel_operation` |
| --- | --- | --- | --- |
| stdio | SDK cancels handler; worker signals and reaps owned tree | session end cancels task | supported |
| streamable HTTP (stateless) | not observed | not observed; durable command continues | supported, real HTTP client test |
| durable jobs | `cancel_job` only | never cancels | separate identity/contract |

`run_cancellable_operation` uses `anyio.to_thread.run_sync(..., abandon_on_cancel=True)`; the worker
owns the process tree and the resource claim context. On task cancellation the worker is signalled
and continues cleanup, so ownership is not released while the process could still act. If cleanup
cannot be confirmed, the existing quarantine path (`process_cleanup_incomplete`) fences ownership.
Measured on this tree: explicit cancel returned in 9.1 ms, the owned process was reaped in 47 ms,
and a follow-up command was admitted in 16 ms.
`cancel_operation` ids are process-local bearer capabilities, not authentication; cross-client
cancellation is possible for anyone who knows the id, and restart reports `operation_not_found`.
No durable job is cancelled by HTTP disconnection.

## 13. Runtime stale-claim reconciliation

Before: a claim quarantined as `stale_native_owner_command_alive` was rechecked only by
`recover_interrupted()` at store startup. After: a conflicting admission triggers the same proof
during the current runtime and releases only proven-dead children or never-dispatched pending
claims. Live orphans remain fenced and require a retry after they exit; no restart is required.
Claims quarantined for unresolved effects or child-capacity cleanup are never reconciled by this
path. No polling task, scheduler or new recovery authority was introduced.

## 14. SQLite migration evidence

Schema version is 12. `tests/test_workflow_migrations.py`, crash-recovery, reconciliation and
recovery-journal suites pass on disposable databases. Migration tests cover future-schema rejection
without writes, v5/v9/v10/v11 upgrades, partial-upgrade rollback, retry after failed backfill,
concurrent old-schema open, WAL-lock retry, idempotent reopen, route-column preservation and
active/pending claim quarantine/release semantics. A real v0.9.0 Windows `.agent_state` copy was
**not** opened or modified, and no Windows-migrated database was available for byte-level
comparison.

## 15. Windows compatibility results

**BLOCKED.** This run has no Windows host, no authorized Windows CI runner, and did not touch the
original Windows workspace. `scripts.platform_validation` reports `linux-amd64` with
`powershell=false`, `desktop_screenshot=false`, `semantic_ui=false`, `browser=false`. Windows-only
Job Object paths are covered by existing unit tests and static review only. No claim of native
Windows parity is made.

## 16. Real browser and LSP results

**BLOCKED.** No Playwright/Chromium installation and no language server are available in this
environment. Browser/LSP code paths were not executed; this report makes no browser, semantic-UIA,
or LSP acceptance claim.

## 17. Real MCP end-to-end results

- stdio: real `mcp.Client` sessions exercised tool discovery, `run_shell`, `run_process`,
  `read_file`, concurrency admission, cancellation and restart/recovery suites.
- streamable HTTP: existing real endpoint tests for explicit cancellation and disconnect behavior
  pass (82-test focused batch).
- Full suite includes the broader integration matrix; see §20 for the exact final totals.
- Mocked-only results are not presented as live MCP evidence.

## 18. Stress and performance measurements

Measured on this host after the fix:

| Scenario | Previous Linux report | Current |
| --- | --- | --- |
| 4 × `sleep 1`, `access="read"` | 1.13 s | **4.23 s** (now sequential by design) |
| 4 × `sleep 1`, exclusive | 4.24 s | **4.12 s** |
| 20 concurrent exclusive `echo` commands | not reported | **0.96 s** |
| 60 mixed terminal requests (20 echoes, 20 read-labeled echoes, 20 read-labeled 0.1 s sleeps) | 2.54 s (unsafe read overlap) | **15.19 s, 0 errors** (fully serialized) |
| Explicit cancel call / process reap / post-cancel follow-up | ~0.10 s / not separated / 1.03 s | **9.1 ms / 47 ms / 16 ms** |
| Focused concurrency/access suite | n/a | 102 passed, 1 skipped in 30.5 s |

Tradeoff: removing untrusted read concurrency costs about 3.1 s for four concurrent one-second
read-labeled commands, and the 60-request mixed batch rises from 2.54 s to 15.19 s because every
terminal command now serializes. That is intentional: those commands could write. Native read
concurrency is unchanged, and no artificial speedup was introduced by weakening synchronization.
The 60-request run reported 0 errors. A multi-minute soak, RSS/fd/handle sampling and
browser-session stress were **not** performed.

## 19. Resource leak findings

No new persistent threads, schedulers, subprocesses or unbounded stores were introduced. The
reconciliation scan is limited to 64 rows/8 conflicts per admission and runs inside the existing
transaction. Existing cancellation tests verify reaping and claim release. Quantitative RSS,
file-descriptor and handle measurements were **not** collected; no leak claim is made beyond the
passing cleanup/cancellation tests.

## 20. Final pytest, Ruff, mypy results

Executed on the final functional tree (POSIX):

```text
python -m compileall -q core tools scripts tests     PASS
python -m ruff check .                                PASS (All checks passed!)
python -m mypy core tools scripts tests               PASS
python main.py --check                                PASS (138 tools)
python -m scripts.health_check --json                 PASS (ok=true)
python -m scripts.platform_validation --json          PASS (linux-amd64)
```

Full pytest: **2,501 passed, 24 skipped, 0 failed in 594.53 s** on a fresh basetemp over the final
functional tree. The count increased from 2,494 because of the new regression tests and the removed
obsolete gate test. An earlier run reported one setup error because pytest could not clear a stale
`.pytest-tmp` directory; the affected test passed in isolation, and the clean-basetemp rerun above
is the recorded result.

## 21. GitHub CI evidence

**UNVERIFIED.** `ci.yml`, `platform-validation.yml` and `release-packaging.yml` trigger only on
pushes to `main` or pull requests targeting `main`; `release.yml` runs after platform validation.
No PR was opened and no `main` push occurred, so this branch push created no hosted run. Local gates
above are the only executed evidence. Windows/macOS CI remains required before claiming
cross-platform parity.

## 22. Remaining risks

1. Terminal commands are fully serialized per host; throughput for formerly read-labeled commands is
   intentionally reduced.
2. The `access` parameter description in the frozen input schema still mentions overlap; the
   implementation ignores it and reports the effective classification. Refreshing that metadata
   requires a Windows baseline refresh.
3. Reconciliation is admission-triggered with a one-second per-store throttle; a caller blocked by a
   live orphan must retry after it exits. There is no background poller by design.
4. Operation ids are bearer capabilities, not secrets; anyone who learns an id can cancel that
   in-flight operation for the server lifetime.
5. The runtime remains fully privileged by design; there is no OS sandbox.
6. Windows Job Object/UIA and macOS behavior remain unexecuted here.
7. POSIX process-group liveness is conservative; a PID-reused group can fence longer than necessary
   (fail-closed, never unsafe release).

## 23. Untested capabilities

- Native Windows execution, Job Objects, CMD/PowerShell, Windows UIA/desktop.
- macOS execution.
- Real Chromium/browser sessions, downloads/uploads, semantic snapshots.
- Real LSP providers and persistent sessions.
- Live third-party MCP extensions and authenticated tunnel connectivity.
- Large multi-client soak, memory/fd leak quantification.
- Migration from a real v0.9.0 Windows database.

## 24. Commit history

- `1847705` merge: integrate Linux reliability hardening into cross-platform branch (nine Linux
  commits preserved: `9e27d1f`, `e983fcc`, `b48bda8`, `b0a6716`, `36d3c28`, `635844c`, `d5be09f`,
  `fb56b32`, `b301309`).
- This work adds focused commits for terminal fail-closed classification, runtime reconciliation,
  and this report. Exact final SHA is reported in the delivery message and can be verified with
  `git log --oneline origin/fix/cross-platform-runtime-hardening`.

## 25. Final acceptance checklist

| Criterion | Status |
| --- | --- |
| `full` profile capabilities preserved; arbitrary commands executable; no repo-only sandbox | **VERIFIED** (138 POSIX tools, no capability removal) |
| Caller-declared read cannot bypass exclusive terminal coordination | **FIXED AND VERIFIED** |
| Native reads retain safe concurrency | **VERIFIED** (focused test; no host lock added) |
| Conflicting writes synchronized | **VERIFIED** |
| Cancellation during admission and execution safe | **VERIFIED** by existing suites |
| stdio + HTTP explicit cancellation | **VERIFIED** |
| Process trees cleaned; no premature release | **VERIFIED** by cancellation/ownership tests |
| Stale claims reconciled during runtime when conclusive | **FIXED AND VERIFIED** |
| Live/unknown ownership fenced | **VERIFIED** |
| No uncertain side effect replayed | **VERIFIED** |
| Schema migration | **VERIFIED** on POSIX fixtures; Windows DB unverified |
| Windows compatibility | **BLOCKED** (no host/CI) |
| Linux functionality | **VERIFIED** |
| Browser/LSP | **BLOCKED** |
| Full test suite / Ruff / mypy | **VERIFIED** (see §20) |
| Real MCP end-to-end | **VERIFIED** for stdio/HTTP paths listed |
| Stress testing / leak measurement | **PARTIAL** (bounded benchmark only) |
| Documentation matches implementation | **VERIFIED** |
| Commit/push hygiene, `main` untouched | **VERIFIED** after delivery |
