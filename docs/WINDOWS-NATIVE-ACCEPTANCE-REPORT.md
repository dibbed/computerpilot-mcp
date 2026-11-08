# Windows-Native Acceptance Report

Branch: `fix/cross-platform-runtime-hardening`
Starting source SHA: `d2d396fcc0ff985e63e01de7e8f488da1d0690de`
Host: physical Windows 10 workstation with an unlocked interactive desktop
Evidence date: 2026-11-08

Labels used below: **VERIFIED**, **FIXED AND VERIFIED**, **PARTIALLY VERIFIED**, **UNVERIFIED**, **BLOCKED**, **FAILED**.

## 1. Windows environment

| Item | Value | Status |
| --- | --- | --- |
| OS | Windows 10 Enterprise, build 19045 | VERIFIED |
| Architecture | AMD64 (8 logical CPUs, 15.8 GB RAM) | VERIFIED |
| Session | Interactive Console session with an accessible desktop | VERIFIED |
| Python | CPython 3.12.8 in an isolated `.venv` (3.11 also installed) | VERIFIED |
| PowerShell | 5.1.19041.6456 | VERIFIED |
| Git | 2.56.0.windows.2 | VERIFIED |
| GitHub CLI | 2.74.2, existing keyring authentication, no re-login | VERIFIED |
| Node.js | v22.18.0 | VERIFIED |
| Pyright | 1.1.403 (global pip launcher and an isolated `pyright[nodejs]` install) | VERIFIED |
| UI Automation | Operational; 8 top-level windows enumerated | VERIFIED |
| Existing ComputerPilot process | None running before acceptance | VERIFIED |

The protected original workspace was not used for development, was not re-branched, and its `.agent_state`, `.secrets`, `uv.lock`, logs and user data were not modified.

## 2. Isolated checkout

- Checkout: `G:\sample` (dedicated clone; separate from the protected workspace).
- Branch: `fix/cross-platform-runtime-hardening`; remote `https://github.com/dibbed/computerpilot-mcp.git`.
- Starting HEAD matched the remote branch at `d2d396fcc0ff985e63e01de7e8f488da1d0690de`.
- Dependencies installed only in the isolated `.venv` (`requirements-dev.txt`, `requirements-browser.txt`, Playwright Chromium headless shell).

## 3. MCP startup and Windows full profile

- `python main.py --check` -> `{"ok":true,"server":"ali_windows_agent_mcp","version":"0.9.0","tool_count":152,"unique_tool_names":true}` — VERIFIED.
- `python -m scripts.health_check --json` -> `ok:true`, 152 tools, `mcp_startup`, `tool_registration`, `filesystem`, `terminal` all true, workflow health clean — VERIFIED.
- Full-capability design preserved: no read-only conversion, no repository-root restriction, absolute paths, mutation, terminal, UIA, browser, workflow and durable-job tools remain registered.
- The tool count is 152 in this configuration; it is reported as an observed value, not a hard requirement. Batch-enabled configurations can add tools.

## 4. Native terminal, process ownership and cancellation

Real MCP stdio requests against the running server produced 25 PASS results and one harness-expectation mismatch (not a product failure):

| Area | Evidence | Status |
| --- | --- | --- |
| `run_process` unicode + working directory | Unicode payload and spaces-in-path cwd returned correctly | VERIFIED |
| Exit codes | Non-zero exit propagated | VERIFIED |
| stdout/stderr capture | Separate streams preserved | VERIFIED |
| Output truncation | `stdout_limit` enforced with truncation metadata | VERIFIED |
| Environment handling | Explicit per-call environment value reached child process | VERIFIED |
| Spawn failure | Structured `ok:false` failure, no crash | VERIFIED |
| Timeout | Timed out, `timed_out:true`, process reaped | VERIFIED |
| Windows Job Object tree | Parent/child/grandchild tree killed; no survivors | VERIFIED |
| `run_cmd` | Quoting, exit code, unicode | VERIFIED |
| `run_powershell` | Exit code, unicode stdout, stderr | VERIFIED |
| `access="read"` on terminal command | Effective access remained exclusive | VERIFIED |
| Concurrent writers | Serialized by exclusive coordination | VERIFIED |
| Native read-only readers | Concurrent readers completed | VERIFIED |
| Cancellation in flight | `cancelled:true`, tree reaped | VERIFIED |
| Cancellation after completion | `operation_finished`, no side effect | VERIFIED |
| Unknown operation id | Structured error | VERIFIED |
| Malformed operation id | Structured `invalid_operation_id` envelope (harness expected a raised error; server behavior is correct) | VERIFIED |
| Background process kill | Target PID tree terminated, `alive_pids: []` | VERIFIED |
| Audit redaction | 8 correlation records, raw bearer id absent | VERIFIED |

Ownership backend reported `windows_job` for owned processes. Exclusive-claim metadata reported `effective_access="exclusive"` with reason `terminal_command_read_untrusted`.

## 5. Desktop and UI Automation

A disposable WinForms fixture plus a temporary Notepad document were used; no user applications or personal documents were touched.

- Desktop availability and window enumeration: VERIFIED.
- Window targeting and foreground switch to the fixture window: VERIFIED.
- UIA element tree retrieval, button lookup by automation id, `ui_invoke` button activation: VERIFIED.
- `ui_set_value` into the fixture text field and read-back: VERIFIED.
- Keyboard input into Notepad (`typed-by-computerpilot ✓` plus a second line): VERIFIED.
- Mouse click on the fixture button: VERIFIED.
- Screenshot of a test-only region (460x20 PNG): VERIFIED (kept outside Git).
- Closed-window error: `uia_window_not_found` — VERIFIED.
- Fixture processes terminated and confirmed not alive: VERIFIED.
- UIA recovery journal: the file exists but contained 0 bytes after the mutations; whether that is expected for this server configuration was not established — PARTIALLY VERIFIED.

Two harness expectations were refined during review: the window tree legitimately contains title-bar buttons alongside `FixtureButton`, and the recovery journal assertion needs a documented expected-content contract.

## 6. Browser

Real Chromium (Playwright headless shell) against a local HTTP fixture:

- Open page, semantic snapshot, fill, select, click, content extraction, session isolation, tabs, screenshot, close: VERIFIED.
- Multiple isolated sessions and close/release: VERIFIED.
- No Chromium processes remained after session close: VERIFIED.
- Upload: the page received the uploaded content (`uploaded-bytes=188`); the tool result did not expose a `bytes` field, so the harness assertion failed — PARTIALLY VERIFIED.
- Download: the page served the download, but the tool reported path `.` with no byte count; the saved artifact location/size was not confirmed — PARTIALLY VERIFIED.

Browser security was not weakened and no user browser profile or cookies were used.

## 7. Language server (LSP)

- Raw provider handshake: a direct stdio `initialize` to the pip `pyright-langserver` succeeds only with a full parent environment on this host — VERIFIED.
- Root cause of the native-session failure: the provider environment narrowed by ComputerPilot omitted the Windows user-profile variables. The pyright launcher crashed at import with `RuntimeError: Could not determine home directory`. Fixed and covered by a regression test — FIXED AND VERIFIED.
- End-to-end `scripts/real_lsp_acceptance.py` still reports `source_index: FAIL:lsp_session_failed`, both with the global pip launcher and with an isolated `pyright[nodejs]` install. The provider process starts (RSS ~86 MB) but the session never reaches document symbols. The remaining failure is not the home-directory crash and was not root-caused within this acceptance window — PARTIALLY VERIFIED / UNVERIFIED.
- Cleanup after the failed session: `cleanup: PASS`, no orphan PIDs — VERIFIED.

## 8. Extensions and Secure Tunnel

- Repository extension lifecycle/transport tests pass in isolation on this host (see section 10).
- A live third-party extension and an authenticated Secure Tunnel session were not exercised: no authorized fixtures or credentials were available, and starting an externally exposed tunnel was not authorized — UNVERIFIED / BLOCKED.
- Tunnel binary discovery and offline failure handling are covered by repository tests, not by a live tunnel.

## 9. SQLite schema v12

- Migration and workflow-store tests were run from the repository suite on Windows (focused results in section 10).
- Fresh creation, upgrade, WAL mode, concurrent connections and future-schema rejection are exercised by those tests.
- The private production database in the protected workspace was intentionally not opened, copied or migrated.

## 10. Verification gates

Static gates, after the source changes:

- `python -m compileall -q core tools scripts tests` -> exit 0 — VERIFIED.
- `python -m ruff check .` -> `All checks passed!` after the local `.validation` harnesses were moved outside the tree — VERIFIED.
- `python -m mypy core tools scripts tests` -> `Success: no issues found in 313 source files` — VERIFIED.
- Focused tests: LSP environment filtering, the new Windows home-directory test, the benchmark timeout test and platform-validation launcher test pass — VERIFIED.

Full-suite execution:

| Group | Files | Result |
| --- | --- | --- |
| 1 | 40 | 571 passed, 8 skipped (147 s) |
| 2 | 40 | 756 passed, 4 skipped, 2 failed (see below) |
| 3 | 40 | 722 passed, 19 skipped (203 s) |
| 4 | 40 | 446 passed, 4 skipped (96 s) |
| Combined | 160 | **2495 passed, 35 skipped, 2 failed** in the grouped run |

The four groups are deterministic, non-overlapping file ranges. The only failures were two `tests/test_launcher.py` cases that invoked `cmd.exe /c START_MCP.bat` by bare relative name; this host sets `NoDefaultCurrentDirectoryInExePath=1`, so cmd.exe refused to resolve the batch file. After switching the test invocation to the absolute path (no assertion weakened), `tests/test_launcher.py` passes 4/4. Effective post-fix total: **2497 passed, 35 skipped, 0 failed**.

Known issue observed during full-suite execution: an early ungrouped run stalled at 82%, and a grouped run stalled at 87% inside `tests/test_extension_fixture.py::test_fixture_root_exit_retains_owned_child_and_grandchild_cleanup` with the fake MCP server tree still alive. That test and its transport sibling pass in isolation (2 passed, 9.11 s), and the four groups completed under a 300 s per-test timeout without reproducing the stall. The interaction remains an intermittent cross-suite risk rather than a reproduced defect.

## 11. Stress and resource monitoring

The platform-validation workflow soak ran 60 workflows with 2 restarts and 3 durable jobs on Windows:

- Workflows completed: 60; durable jobs submitted 3, succeeded 3, active 0.
- Leaked leases 0; orphan operations 0; orphan events 0.
- SQLite WAL delta 0; workflow database remained consistent through restart and retention cleanup.
- RSS before 69.9 MB, after 72.0 MB (growth 2.1 MB).
- The terminal/cancellation matrix left server RSS at ~115 MB with all owned processes reaped.

This is a bounded soak, not multi-hour reliability evidence. A longer run with explicit handle/thread sampling remains UNVERIFIED.

## 12. Defects fixed

1. **Windows provider environment omitted the user profile.**
   - Root cause: `build_environment` forwarded only `SystemRoot`, `WINDIR`, `TEMP` and `TMP`; Python/Node launchers resolve `Path.home()` and caches during import.
   - Fix: forward fixed, non-credential Windows startup variables (`USERPROFILE`, `HOMEDRIVE`, `HOMEPATH`, `APPDATA`, `LOCALAPPDATA`, `COMSPEC`, `PATHEXT`, drive/processor variables) while still excluding `PYTHONPATH`, `PYTHONHOME`, `NODE_OPTIONS`, `NODE_PATH` and credentials.
   - Files: `tools/language/transport.py`, `tests/test_lsp_sessions.py`.
   - Regression tests: updated environment-filter test and a new Windows-only home-directory/no-injection test.

2. **Isolated exec-cost benchmark had a fixed 150 s budget and an opaque failure.**
   - Root cause: the worker runs `(5 + repeats) x cases` rounds; the old ceiling ignored warmups and case count, so slower Windows hosts timed out and reported only `isolated_benchmark_failed`.
   - Fix: per-case budget scaled by warmups and case count (capped at 3600 s) and a diagnostic error envelope (`isolated_benchmark_timeout`, `timed_out`, `exit_code`, `timeout_sec`).
   - Files: `scripts/perf_benchmark.py`, `tests/test_perf_benchmark.py`.

3. **Platform validation and the startup benchmark could not launch `START_MCP.bat` in a hardened Windows shell.**
   - Root cause: `cmd.exe /c START_MCP.bat` relied on current-directory executable search, which is disabled when `NoDefaultCurrentDirectoryInExePath` is set. Both `scripts/platform_validation.py` and `scripts/perf_benchmark.py` used the relative form.
   - Fix: pass the absolute batch path in both gates.
   - Files: `scripts/platform_validation.py`, `scripts/perf_benchmark.py`, `tests/test_platform_validation.py`, `tests/test_perf_benchmark.py`, `tests/test_launcher.py`.
   - Verification: `startup_launcher_validation` is now `ok`, the full platform validation gate passes, and `tests/test_launcher.py` passes 4/4 on this host.

## 13. GitHub CI

- Baseline runs `42118454560` and `42118454569` succeeded at `c349617` — VERIFIED historical evidence.
- Branch HEAD `d2d396f` failed Windows Python 3.12 in `tests/test_perf_benchmark.py::test_exec_cost_real_sdk_paths_are_equivalent_and_isolated` (`1 failed, 2514 passed, 14 skipped`). That failure is addressed by fix 2.
- New runs for the commits produced by this acceptance are reported in the final summary; a passing historical run is not treated as validation of new code.

## 14. Remaining risks

- An intermittent extension-fixture stall was seen twice but not reproduced after the per-test-timeout runs; it needs a traceback from a stalled process before it can be fixed.
- End-to-end LSP `source_index` still fails with `lsp_session_failed`; the home-directory crash is fixed, but the session-level failure remains open.
- Browser upload/download result metadata is incomplete or inconsistent on Windows.
- No live Secure Tunnel or third-party extension acceptance.
- No multi-minute soak with handle/thread/claim sampling.

## 15. Readiness assessment

**READY WITH DOCUMENTED LIMITATIONS.**

The three confirmed Windows defects found here are fixed with regression coverage, the full 152-tool capability profile is intact, native terminal/job-object/cancellation/UIA/browser behavior is substantially verified, and no open defect was confirmed in production code. Remaining gaps (LSP session completion, upload/download metadata, full-suite completion, live tunnel/extension, soak) are documented above rather than hidden.

## 16. Independent isolated recheck — 2026-11-08

This section records a later run and supersedes the preceding sections only where it states new results. The earlier section describes its own checkout and source state; do not attribute its measurements to this run.

- **VERIFIED:** Windows 10 Enterprise 10.0.19045, x64, Intel Core i3-12100F (4 cores / 8 logical processors), approximately 15.8 GiB RAM; active console session and accessible input desktop.
- **VERIFIED:** CPython 3.12.8; PowerShell 7.6.9 (Windows PowerShell 5.1.19041.6456 was used for the UIA fixture); Git 2.48.1.windows.1; GitHub CLI 2.74.2; Node.js 22.18.0; MCP 2.0.1; Playwright 1.64.1; UIAutomation 2.0.29; pytest 9.2.0; Ruff 0.17.4.
- **VERIFIED:** Fresh checkout `C:\Users\Meliodas\Downloads\computerpilot-mcp-windows-validation`, branch `fix/cross-platform-runtime-hardening`, starting source SHA `bfc318084d503056b8c30de20b6e9e61adb411d0`; tests below ran against that source plus test-only commit `917d46020da00832969728ed1da58480379c5551`. The production source is unchanged by this recheck.
- **PARTIALLY VERIFIED:** PyPI timed out. A separate earlier validation environment built against the same CPython was copied into this checkout's isolated virtual environment; matching browser binaries were copied into this checkout. `pip check` passed. No global packages were installed.
- **VERIFIED:** The protected original ComputerPilot workspace was not used for this run; its configuration and private database were not opened, copied, or migrated. No comparison of all original-workspace files was performed.

### Current capability and native checks

- **VERIFIED:** `main.py --check` reported 152 unique full-profile tools. The real stdio catalog contained 152 unique names and object input schemas; the repository catalog tests checked additional schema and registration contracts. Full filesystem, terminal, Git, browser, workflows, jobs, LSP and Windows desktop capability registration remains enabled. This observed count is configuration-specific.
- **VERIFIED:** The local health check returned `ok: true` and exercised MCP startup, registration, filesystem and terminal operations. Real stdio requests executed `run_process`, `run_cmd` and `run_powershell` from a disposable path containing spaces.
- **VERIFIED:** Grouped Windows tests included 27 Job Object tests, including owned process-tree cleanup and durable worker crash, plus three real HTTP cancellation/disconnection tests. The earlier focused run after the PID-file fix passed 74 tests with two skips. Audit-redaction and resource coordination tests are included in the grouped suite; the earlier section contains separate real MCP terminal and synthetic bearer-ID observations.
- **PARTIALLY VERIFIED:** Ten stale-claim recovery cases passed, including live fencing, runtime release after exit, unknown marker handling and quarantine preservation. One POSIX SIGKILL case was skipped on Windows. This does not establish every requested Windows PID-reuse or crash-race scenario.
- **VERIFIED:** All 15 synthetic migration tests passed, including legacy and v11 upgrades, interrupted-upgrade rollback, WAL, concurrent opening, reopen and future-schema rejection. The Windows CI workflow invokes the full test suite, including this module; no private database was touched.
- **VERIFIED:** A real, off-screen, test-owned WinForms window was found by PID and title. MCP UIA traversed and located its controls, set a value, invoked a button, and verified the value through a fixture-owned file. The foreground window was unchanged, and the fixture was terminated. The previous section records a separate test-only screenshot, keyboard and mouse run.
- **PARTIALLY VERIFIED:** This recheck did not repeat foreground switching, keyboard, mouse or screenshot acceptance. It did not inspect or capture personal desktop content.
- **VERIFIED:** A real isolated Chromium session, reached via MCP stdio and a loopback HTTP fixture, passed semantic snapshot, fill, select, click and text extraction. A second session retained independent page state; both sessions closed, and no process from the copied Chromium directory remained afterward. A further run verified the exact uploaded file contents on the page and checked the downloaded file's path, contents and reported byte count.
- **PARTIALLY VERIFIED:** Browser cancellation and idle eviction were not repeated here. Both smoke runs exited successfully but their stderr contained an asyncio subprocess-destructor `RuntimeError: Event loop is closed`; its owning component and implications were not established. Do not treat the absence of a surviving Chromium process as proof of all transport cleanup.
- **VERIFIED:** The real Pyright acceptance script, using system Node and the isolated Pyright JavaScript entry point, passed cross-file definitions, references, diagnostics, edit synchronization, restart and process cleanup twice; no orphan PIDs remained. The script exercises the language session manager, not MCP transport. Its `source_index` received a reply.
- **UNVERIFIED:** The earlier failing Python/Node wrapper-launcher route, LSP cancellation over MCP, and the reason that route failed were not resolved by the successful direct-Node run.
- **VERIFIED:** Grouped tests included 26 local extension fixture cases and nine real-provider lifecycle cases using repository-owned subprocess fixtures. These are not a live third-party service.
- **PARTIALLY VERIFIED:** 29 tunnel-runtime and 26 supervisor tests passed; no tunnel binary was found on this session's `PATH`. A real authenticated or externally exposed tunnel was not started, and private tunnel configuration was not inspected.

### Verification results and test fixes

Compilation, full Ruff, full mypy (313 files), startup, health and Windows platform validation all passed after the test edits. Platform validation ran launcher validation, Full Doctor, the quick benchmark and a 60-workflow restart soak: three durable jobs succeeded, with zero reported leaked leases, orphan operations or orphan events.

| Non-overlapping group | Files | Passed | Skipped | Failed |
| --- | ---: | ---: | ---: | ---: |
| 1 | 40 | 571 | 8 | 0 |
| 2 | 40 | 758 | 4 | 0 |
| 3 | 40 | 722 | 19 | 0 |
| 4 | 40 | 446 | 4 | 0 |
| Total | 160 | **2497** | **35** | **0** |

Each group used a distinct disposable base directory and a 120-second per-test timeout. The 35 skips include POSIX-only cancellation/recovery probes and a Windows symlink-dependent test; they are not passes. CI at the starting SHA has a successful Windows Python 3.10/3.12 matrix and Windows platform release gate.

- **FIXED AND VERIFIED:** A Windows Job Object test observed an existing but still-empty PID fixture file and raised `JSONDecodeError`. The fixture now publishes the completed file with an atomic rename. The original test and surrounding suite passed afterward; process ownership assertions were preserved.
- **FIXED AND VERIFIED:** A resource-claim test replaced `sleep` on the shared Python `time` module, exposing unrelated background threads to its failing stub. The stub is now scoped to the resource-claim module; the affected claim, timing and Job Object tests passed together (83 passed, one skipped).
- **UNVERIFIED:** Before these edits, one ungrouped full run timed out in `test_completed_timing_history_is_bounded_and_latest_records_survive` while `flush_timings()` waited for its queue; a timing writer was idle on queue input. The shared-clock patch is a plausible contributor, but the exact cause was not proven. The grouped pass does not prove that a single-process full-suite run now completes.

### Bounded resource run

A separate 182.28-second stdio run performed 232 disposable read, write, CMD and health operations: zero reported failures; mean latency 22.23 ms, p95 63.0 ms; sampled CPU 2.73% of one logical core. Sampled aggregate owned-process working set was 133,197,824 bytes initially, 132,706,304 bytes at the end and 133,275,648 bytes peak. Sampled handles rose from 458 to 507; threads went from 21 to 11, with peak 21; peak sampled process count was three. Active and quarantined claims were zero at the first and last samples. Shutdown took 0.188 seconds, with zero observed owned survivors. CPU sampling may miss short-lived processes. This limited run does not prove long-term resource or handle stability and did not exercise every integration.

### GitHub and readiness

Historical baseline runs `42118454560` and `42118454569` succeeded at `c3496172773654534eb0307f41981852f15a8bba`. At the start of this recheck, CI run `42433807442` and platform run `42433807367` succeeded at `bfc318084d503056b8c30de20b6e9e61adb411d0`. After the test fixes, [CI run 42438687907](https://github.com/dibbed/computerpilot-mcp/actions/runs/42438687907) and [platform validation 42438687906](https://github.com/dibbed/computerpilot-mcp/actions/runs/42438687906) succeeded at `c711ee3b40baee2dbb8253c3f26d8625e65c6505`, including both Windows CI jobs and the Windows platform gate. A later report-only update records those results and the additional transfer check; its own CI must be checked at its exact SHA. `main` was observed at `b88a2e17040036181ed99be3a0a0122c4dcb5e88`.

**READY WITH DOCUMENTED LIMITATIONS** for review, not release: the grouped Windows suite and the listed native checks passed, but the ungrouped timeout, earlier LSP wrapper failure, subprocess-destructor warning, untested external tunnel and long-duration handle behavior remain open. No production defect was conclusively established by this recheck. No merge, release, or production database migration was performed.
