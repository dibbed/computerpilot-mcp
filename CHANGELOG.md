# Changelog

All notable project-level changes are documented here.

Project releases are independent from the bundled upstream tunnel-client.exe version; binary runtime provenance remains in BINARY_PROVENANCE.md.

## [Unreleased]

### Added
- Add Phase E1 durable-job state versioning. Every job row now carries a monotonic `version` exposed through status/list/output responses, forming the foundation for future `job_wait(after_version, timeout)` support.
- Add optional `queue_timeout_sec` submission semantics. The default remains no queue deadline; explicit queue expiry is stored separately from execution timeout and returns a terminal `timed_out` result.
- Add additive jobs database migrations: E1 introduced schema revision 2 with `version`/`queue_deadline`, and E2 advances to revision 3 with internal `launch_token`/`launch_started` reservation metadata while preserving existing rows and legacy idempotency fingerprints.
- Add Phase E2 process-safe durable-job admission with configurable `MCP_MAX_RUNNING_JOBS` (default `4`), atomic capacity-aware claims, and active-slot accounting that includes live orphaned commands.
- Add a lightweight SQLite-backed worker launcher that reserves only available capacity, recovers stale launch reservations, preserves independent per-running-job workers, and resumes queued work without introducing a shared resident job daemon.
- Add Phase E3 `job_wait(job_id, after_version, timeout)` as a bounded read-only long-poll over the authoritative monotonic job version. Wait timeout is capped at 300 seconds, timeout returns normal unchanged state, and an ahead-of-current version returns controlled `job_version_ahead` guidance.

### Fixed
- Detect externally closed Playwright pages/contexts as stale sessions. `browser_open_page` now recreates the session context inside the existing healthy pool instead of retaining a dead page, while non-open operations return `browser_session_stale`.
- Distinguish a page/context closed during navigation from a disconnected Browser process so the controlled error code and retry hint describe the actual failure.
- Remove the old implicit 60-second queued-job interruption heuristic so queue waiting no longer consumes or conflicts with execution-timeout semantics.
- Prevent a worker from claiming a queued job after its explicit queue deadline, including races between worker startup and status reconciliation.
- Make concurrent JobStore schema initialization resilient to SQLite WAL setup contention and reject unsupported future jobs schemas instead of silently downgrading them.
- Prevent max-running slot leaks by reconciling dead running workers and finished orphaned commands before new admission decisions; capacity is derived from authoritative row state rather than a separate counter.
- Make queued cancellation atomic with admission so a cancel/claim race cannot produce an uncancelled running command.
- Preserve submitter-exit durability under bounded launching by synchronously launching available workers before `submit()` returns and having terminal workers kick queued successors before exit.
- Make durable-job output-directory setup idempotent so a concurrent deduplicated submit can kick the scheduler and pre-create the job directory without causing the original inserter to fail or truncate already-created output files.
- Reuse one process-local scheduler coordinator under concurrent scheduler kicks, and remove failed-to-start coordinators from the registry so thread-start failure cannot poison later recovery attempts.

### Changed
- Browser benchmarks now report open latency, parallel-navigation latency, and cleanup latency for the 1/5/20-session cases, plus a dedicated background idle-eviction case with separate session and empty-pool reclamation timings.
- Refresh the README with an explicit completed-roadmap snapshot for Phases A-D (`v0.0.12` through `v0.0.15`), the benchmark-gated compact-tool decision, current post-D validation, and the next Phase E target.
- Document Phase E1 job timeout semantics explicitly: `timeout_sec` begins after command execution starts, while optional queue waiting uses its own deadline.
- Extend durable-job benchmarks with configured max-running, peak active-job, and peak queued-job details; short benchmark commands now remain alive long enough for admission behavior to be measurable.
- Extend the jobs benchmark with `job_wait` version-change, bounded-timeout, and 50-concurrent-waiter cases. E3 intentionally keeps adaptive SQLite polling with one reused connection per wait; Named Event/Pipe IPC remains deferred because the measured long-poll path does not justify the added recovery complexity.
- Increase the current typed MCP surface from 59 to 60 tools by adding the read-only `job_wait` API; this is an intentional Phase E feature addition, not the benchmark-rejected compact-tool profile from Phase D.

### Validation
- Real Chromium validation confirmed recovery from both externally closed Page and BrowserContext while preserving the same Browser pool.
- After installing the optional Playwright Firefox runtime on the validation host, the mixed 5 Chromium + 5 Firefox benchmark completed with exactly 2 Browser instances and 10 isolated contexts.
- Final post-D validation completed with 203 passing pytest tests, Ruff with zero violations, mypy with zero issues across 79 source files, successful compileall, a passing 59-tool health check, and Full Doctor.
- Phase E1 validation completed with 214 passing pytest tests, Ruff with zero violations, mypy with zero issues across 80 source files, successful compileall, and a passing 59-tool health check. The jobs migration was also validated against a transactionally copied real local database with all 4 existing rows/request keys preserved across schema 0 -> 2.
- Phase E2's initial worker-waiting design correctly capped 50 submitted jobs at 4 active jobs but still peaked at 3280.344 MiB / 155 processes, triggering the roadmap's lightweight-launcher gate. The final committed scheduler benchmark (`e9fc0b9`) kept the same 4-active-job cap and completed the 50-job case in 20817.842 ms at 491.629 MiB / 25 processes; a calibration run with cap 8 completed in 17955.369 ms at 748.305 MiB / 37 processes.
- Phase E2 final validation completed with 225 passing pytest tests, Ruff with zero violations, mypy with zero issues across 81 source files, successful compileall, a passing 59-tool health check, and Full Doctor including a real disposable Chromium launch.
- A post-E1/E2 hardening audit completed with 229 passing pytest tests, Ruff with zero violations, mypy with zero issues across 83 source files, successful compileall, a passing 59-tool health check, and Full Doctor. Additional live stress checks covered 48 concurrent duplicate submissions resolving to one job, 20 jobs submitted from 4 independent processes with an actual command peak exactly equal to cap 3, and a forced worker crash where the live orphaned child retained capacity until exit before the successor job started. A dedicated migration regression also verifies schema 2 -> 3 preserves existing version/queue-deadline state.
- Phase E3 validation completed with 239 passing pytest tests, Ruff with zero violations, mypy with zero issues across 82 source files, successful compileall, a passing 60-tool health check, and Full Doctor including a real disposable Chromium launch. MCP integration verifies `job_wait` does not block unrelated event-loop work and a second MCP client can follow a durable job by version after the first client disconnects.
- The committed E3 benchmark (`9b328f8`) measured one waiter waking 54.006 ms after an authoritative version update, a 200 ms wait timing out in 211.776 ms, and 50 concurrent waiters observing the same change with 46.787 ms wake latency, 75.164 MiB peak process-tree RSS, and no additional child process.

## [0.0.15] - 2026-09-14

### Added
- Add shared Playwright browser pools keyed by browser engine, headless mode, and launch-affecting configuration while keeping one isolated `BrowserContext` per session.
- Add configurable idle reclamation for browser sessions/contexts (`MCP_BROWSER_IDLE_SEC`, default 900 seconds) and empty pools (`MCP_BROWSER_POOL_IDLE_SEC`, default 120 seconds).
- Extend browser benchmarks to report Browser-instance count, active-context count, pool configuration, and the 1/5/20-session scale points; full profile also attempts a mixed Chromium/Firefox case.
- Add browser lifecycle/hardening coverage for context isolation, transactional migration, active-operation cleanup exclusion, pool attach races, launch retry, process disconnect, concurrent recreation, and disconnect during context creation.

### Changed
- Closing one browser session now closes only its context instead of terminating a Browser shared by other compatible sessions.
- Reopening a session with an incompatible browser configuration is transactional: the existing context remains available until the replacement has opened and navigated successfully.
- Browser cleanup is single-flight, tracks monotonic last-use timestamps, protects in-flight context attachment with pending-context accounting, and reclaims an empty pool only after its own idle grace period.

### Fixed
- Prevent idle cleanup from evicting a Browser pool in the gap between pool acquisition and `new_context()` attachment.
- Invalidate disconnected Browser pools, mark dependent sessions stale, return a specific stale-session error for non-open operations, and allow a later `browser_open_page` to recreate a healthy compatible pool.
- Prevent launch/context/navigation failures from publishing half-created sessions or poisoning future pool creation.
- Keep cleanup progress resilient when stale context or Browser close operations fail.

### Validation
- 198 pytest tests passed on Windows; Ruff passed with zero violations; mypy passed across 79 source files; compileall passed.
- The 59-tool MCP health check passed. Full Doctor passed in `local-http` mode using the project `.venv`, including dependency checks, `pip check`, MCP/filesystem/terminal smoke, Playwright import, and a real disposable Chromium launch. The quick benchmark profile also completed successfully.
- On the local Windows benchmark host, the 20-session Chromium case changed from 20 Browser instances in `v0.0.14` to 1 Browser instance plus 20 contexts in `v0.0.15`.
- In the same one-run 20-session comparison, elapsed time changed from 2606.737 ms to 1272.600 ms, peak process-tree RSS from 3757.762 MiB to 1506.699 MiB, and peak process count from 83 to 26.
- The mixed Chromium/Firefox benchmark is skipped on this host because the optional Firefox Playwright runtime is not installed.

## [0.0.14] - 2026-09-13

### Added
- Add a bounded, version-aware Python metadata cache keyed by canonical path, `mtime_ns`, and file size, with configurable LRU file/byte budgets.
- Add per-path parse single-flight so concurrent project-intelligence requests share one stable parse while unrelated files remain concurrent.
- Invalidate Python metadata after successful MCP file mutations, including recursive move/delete cases, while retaining stat-based detection for external edits.
- Add `search_files` `count_mode="none"` for traversal-order streaming name-search pages without an exact total count; the backward-compatible default remains `count_mode="exact"`.
- Add opt-in immutable disk-backed search snapshots with opaque cursors, query fingerprint binding, atomic publication, TTL expiry, and count/byte quotas.
- Add search benchmark cases for streaming first-page latency, snapshot creation, and zero-rescan snapshot continuation.

### Changed
- Exact name search now matches while walking instead of first materializing every scanned path, while preserving global path sorting and exact-count semantics over the bounded scan.
- Raise the default Python metadata file-count budget from 1,024 to 10,000 after large-project benchmarking; the 64 MiB byte cap remains the primary memory safety bound.
- Durable-job status/list queries fetch only reconciliation/public fields; workers skip redundant store initialization and reuse one runtime SQLite connection.
- `job_output` follows `MCP_OUTPUT_DEFAULT`, and finalized artifact descriptors use canonical Windows source identities for cache reuse.

### Fixed
- Prevent an in-flight Python parse invalidated by a concurrent mutation from returning or republishing stale metadata; retries now coalesce per canonical path until a stable version is observed.
- Move project-cache invalidation immediately after successful filesystem mutation and invalidate failed `safe_refactor` rollback paths so transient content cannot survive in metadata caches.
- Keep pytest temporary/cache state under ignored `.agent_state/` to avoid Windows temporary-directory cleanup failures and repository cache permission warnings.

### Validation
- 178 pytest tests passed on Windows; Ruff passed with zero violations; mypy passed across 74 source files; compileall passed.
- The 59-tool MCP health check passed; full Doctor passed in `local-http` mode and the subsequent fingerprint-only check succeeded.
- With the final 10,000-file metadata-cache default, the 10,000-file `find_function` benchmark median was 6,299.379 ms versus 9,196.297 ms with the earlier 1,024-entry budget; 10,000 cached metadata entries occupied about 11.5 MB under the 64 MiB byte cap.
- At 10,000 search files, median exact name search was 786.797 ms, streaming first-page search was 7.123 ms, snapshot creation was 915.792 ms, and snapshot page two was 0.627 ms with zero filesystem rescan.
- At 100,000 search files, median exact name search was 7,473.454 ms, streaming first-page search was 9.191 ms, snapshot creation was 8,323.974 ms, and snapshot page two was 0.642 ms with zero filesystem rescan; the snapshot was 17,900,337 bytes under the 128 MiB total snapshot budget.

## [0.0.13] - 2026-09-12

### Added
- Add keyed resource locks with Windows path canonicalization, stable multi-path ordering, and cleanup after success or failure.
- Add full startup Doctor fingerprinting with fail-closed invalidation and explicit content-search backend diagnostics (`content_search_backend`, `ripgrep_path`, `ripgrep_version`).
- Add bounded `delivery="auto"` output policy behind `MCP_OUTPUT_DEFAULT=auto`, with bounded previews, byte cursors, artifact descriptors, and configurable inline/preview thresholds while preserving explicit legacy inline behavior by default.
- Add short/slow TTL caches plus single-flight refreshes for expensive control-panel status components.

### Fixed
- Protect filesystem read-modify-write mutations and project-memory updates from same-runtime lost updates.
- Replace the browser-wide navigation lock with per-session locking so independent sessions no longer serialize network navigation.
- Correct Phase A benchmark fidelity so startup exercises the real bootstrap validation path and large output preserves the current default inline behavior.
- Measure peak RSS and I/O across the benchmark process tree so durable job workers and browser processes are included.
- Serialize the complete wire-style tool catalog and enforce search correctness expectations.
- Add a real `find_function` project lookup benchmark alongside raw AST parsing and age-bounded cleanup for stale benchmark temp directories.

### Changed
- Reuse one `JobStore` per MCP server registration and remove list-time N+1 job queries.
- Reuse a dedicated SQLite connection inside each durable worker and reduce cancellation polling frequency while keeping sub-second cancellation checks.
- Reuse finalized output descriptors instead of copying and hashing the same final artifact repeatedly.
- Split normal startup into a cached fast preflight and an explicit full Doctor path while preserving dependency, MCP/filesystem/terminal, optional-browser, search-backend, and Tunnel checks on full validation.
- Split opt-in tool profiling into validation, queue/dispatch wait, tool body, result conversion, request pipeline, and serialization phases.
- Coalesce timing JSONL writes through a bounded background writer to reduce observer I/O on the tool hot path.

### Validation
- 134 pytest tests passed.
- Ruff passed with zero violations.
- mypy passed across 69 source files.
- compileall and the 59-tool MCP health check passed.
- A 100 MiB command under auto delivery retained the full 104,857,600-byte artifact while keeping the structured response at about 67 KiB.
- After a successful full validation, repeated launcher fast-path runs measured about 0.84-0.90 seconds in the Phase A benchmark harness.
- The 20-session browser benchmark measured about 3.64 seconds versus the Phase A baseline of about 5.93 seconds; durable-job stress remained roughly flat as expected because this release targets DB/query churn rather than worker-count reduction.

## [0.0.12] - 2026-09-12

### Added
- Opt-in structured performance timing for MCP requests, tool bodies, process phases, output delivery, and artifact snapshots.
- Repeatable quick/full benchmark harness for catalog, startup, output, AST, search, durable jobs, and optional browser sessions.
- Benchmark tests plus latency, RSS, I/O, and serialization measurements.

### Changed
- Project MCP version metadata now follows the public release series and is 0.0.12.
- README documents timing and benchmark workflows.

### Validation
- 54 pytest tests passed.
- Ruff passed.
- mypy passed across 54 source files.
- compileall passed.
- Quick benchmark, 1/10-job benchmark, and one Chromium benchmark were validated.

## [0.0.11] - 2026-09-11

### Added
- Initial public Windows Developer Agent MCP baseline with 59 registered MCP tools.
