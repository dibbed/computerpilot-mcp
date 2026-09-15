# Changelog

All notable project-level changes are documented here.

Project releases are independent from the bundled upstream tunnel-client.exe version; binary runtime provenance remains in BINARY_PROVENANCE.md.

## [Unreleased]

### Added
- Add Phase F1 bounded audit batching with a process-local queue, single writer thread, configurable batch/flush/queue limits, cross-process append serialization, and synchronous fallback when the bounded queue is saturated.
- Add size-based audit rotation/retention with configurable `MCP_AUDIT_MAX_FILE_BYTES` (default 8 MiB) and `MCP_AUDIT_KEEP_FILES` (default 5 rotated files in addition to the active log).
- Add repeatable `audit` cases to `scripts.perf_benchmark`, covering legacy synchronous and production batched writes with both 1-thread and 8-thread contention.
- Add Phase F2 background/coalesced backup retention with configurable `MCP_BACKUP_MAX_BYTES` (default 256 MiB), `MCP_BACKUP_MAX_AGE_DAYS` (default 30), and `MCP_BACKUP_CLEANUP_INTERVAL_SEC` (default 5 seconds). Age and byte cleanup preserve at least the newest restore point and protect the backup produced by the current successful write.
- Add repeatable `backups` benchmark cases for real backup-storage duplicate inventory and a synthetic 2000-file age/quota retention workload.
- Add Phase F3 disk-backed artifact retention with configurable age/count/byte limits (`MCP_ARTIFACT_MAX_AGE_HOURS=168`, `MCP_ARTIFACT_MAX_COUNT=512`, `MCP_ARTIFACT_MAX_BYTES=512 MiB`) and coalesced cleanup after artifact creation plus runtime startup maintenance.
- Add terminal durable-job history/output retention with configurable age/count/byte limits (`30 days`, `1000` terminal rows, `1 GiB` by default); queued, running, and orphaned rows are never eligible.
- Add repeatable `retention` benchmark cases covering the real artifact/job-storage inventory plus synthetic 5000-artifact and 2000-terminal-job cleanup workloads.

### Changed
- Destructive audit events for file deletion/move, process termination, durable-job cancellation, and supervisor runtime termination now use durable fsync-backed audit boundaries; normal MCP lifecycle shutdown explicitly flushes queued audit records. Audit payloads remain metadata-only.
- Filesystem backups are now copied to a temporary file, fsynced, and atomically published before the target replacement continues. Retention age uses the backup-event timestamp encoded in the filename instead of the source file's copied mtime.
- Content-addressed backup deduplication remains intentionally unimplemented after the F2 benchmark gate found only 95,960 bytes (0.2872%) of potential savings across the real 33.41 MB backup corpus, below the 1 MiB + 10% implementation threshold.
- Runtime lifecycle maintenance now schedules artifact and durable-job retention from the supervised MCP process rather than independent job workers, preserving Phase-E durable process ownership semantics.
- `job_output` now holds the same keyed stdout/stderr locks used by retention while reading row state and output, closing the terminal row-read/output-delete race. Terminal cleanup commits the DB-row deletion before removing its output directory so a crash can leave only a detectable orphan directory, never a live history row pointing at already-pruned output.
- Expired terminal-job rows no longer retain their idempotency keys indefinitely; reusing a key after its history record has been pruned may submit a new job.

### Fixed
- Harden F1 audit rotation so a large queued batch is split on complete JSONL record boundaries instead of allowing the active segment to exceed its configured ceiling; serialize submit/close acceptance, make close timeouts observable/retryable, and fsync every retained segment at explicit flush/shutdown boundaries.
- Close the F2/F3 fresh-publication race by giving newly published backups and artifacts a short safety lease; artifacts are now copied to temporary files and atomically published only after the snapshot is complete.
- Synchronize finalized-artifact cache hits with retention and renew the artifact lease before returning a cached path, preventing cleanup from racing a successful cache hit into a dead path.
- Retry one durable-job submission when terminal-history retention removes the deduplicated row between the idempotency transaction and its follow-up status read, preserving the documented post-expiry reuse semantics.
- Reap detached durable-worker `Popen` handles in a daemon waiter without changing worker ownership, survival, or SQLite authority, eliminating long-running parent-handle leakage and Python `ResourceWarning`s.
- Close audit benchmark file readers explicitly so Windows validation runs do not retain file handles or contaminate cleanup/rotation measurements.

### Validation
- Phase F1 validation completed with **275 passing pytest tests**, Ruff with zero violations, mypy with zero issues across 91 source files, successful compileall, a passing 60-tool health check, and Full Doctor including a real disposable Chromium launch.
- The canonical 3-run F1 audit benchmark on commit `26cb41f` preserved all 20,000 records per sample. Single-thread median wall time improved from **2077.088 ms** (legacy synchronous) to **268.806 ms** (batched); 8-thread median improved from **2199.630 ms** to **396.403 ms**. The 8-thread caller p95 dropped from **4.4194 ms** to **0.2427 ms**.
- Phase F2 validation completed with **290 passing pytest tests**, Ruff with zero violations, mypy with zero issues across 93 source files, successful compileall, a passing 60-tool health check, and Full Doctor including a real disposable Chromium launch.
- The canonical F2 backup benchmark on commit `e8cf7a0` scanned 1429 real backups totaling 33,409,275 bytes and measured only 95,960 bytes (0.2872%) of deduplication savings, so the content-addressed dedup gate failed intentionally. The 2000-file synthetic retention case removed 1000 files by age and 500 more by quota, retaining 500 files / 1,024,000 bytes with zero errors and a satisfied quota.
- Phase F3 validation completed with **301 passing pytest tests**, Ruff with zero violations, mypy with zero issues across **97 source files**, successful compileall, a passing **60-tool** health check, and Full Doctor including a real disposable Chromium launch.
- The canonical F3 retention benchmark on commit `a2900da` measured the real corpus at 22 artifacts / 6,600,000 bytes and 4 terminal jobs / 539 output bytes. The 5000-artifact synthetic cleanup removed 4375 files and completed in **1121.777 ms**, leaving 625 files / 640,000 bytes. The 2000-terminal-job cleanup removed 1750 rows and completed in **1732.422 ms**, leaving 250 terminal rows / 512,000 output bytes while preserving all 3 queued/running/orphaned rows. A pre-batching calibration took roughly 14.9 seconds for the same job-history cleanup scale; 100-job transaction/lock batching reduced that cleanup cost by about eightfold.
- The dedicated post-F3 deep audit completed with **311 passing pytest tests**, Ruff with zero violations, mypy with zero issues across **95 source files**, successful compileall, a passing **60-tool** health check, and Full Doctor including a real disposable Chromium launch. Python `-X dev` integration runs are clean of the audit/job-worker handle warnings discovered during the audit.
- Cross-feature stress preserved **4000/4000 unique audit records** across 8 writer processes under rotation, retained all 12 concurrently-created fresh artifacts with zero temporary-file leakage, kept fresh backup publication safe before converging back to quota, and preserved queued/running/orphaned jobs across 3 concurrent history-cleanup passes. A 100-iteration finalized-artifact cache/cleanup race produced zero dead returned paths.
- The hardened F1 audit writer retained its performance gate after the correctness fixes: 20,000 events measured roughly **252.888 ms vs 2231.992 ms** synchronous at one thread and **289.107 ms vs 2335.559 ms** synchronous at eight threads while preserving every record and respecting the rotation ceiling.

## [0.0.16] - 2026-09-15

### Added
- Add Phase E1 durable-job state versioning. Every job row now carries a monotonic `version` exposed through status/list/output responses, forming the foundation for future `job_wait(after_version, timeout)` support.
- Add optional `queue_timeout_sec` submission semantics. The default remains no queue deadline; explicit queue expiry is stored separately from execution timeout and returns a terminal `timed_out` result.
- Add additive jobs database migrations: E1 introduced schema revision 2 with `version`/`queue_deadline`, and E2 advances to revision 3 with internal `launch_token`/`launch_started` reservation metadata while preserving existing rows and legacy idempotency fingerprints.
- Add Phase E2 process-safe durable-job admission with configurable `MCP_MAX_RUNNING_JOBS` (default `4`), atomic capacity-aware claims, and active-slot accounting that includes live orphaned commands.
- Add a lightweight SQLite-backed worker launcher that reserves only available capacity, recovers stale launch reservations, preserves independent per-running-job workers, and resumes queued work without introducing a shared resident job daemon.
- Add Phase E3 `job_wait(job_id, after_version, timeout)` as a bounded read-only long-poll over the authoritative monotonic job version. Wait timeout is capped at 300 seconds, timeout returns normal unchanged state, and an ahead-of-current version returns controlled `job_version_ahead` guidance.
- Add Phase E4 runtime lifecycle tracking (`RUNNING -> DRAINING -> STOPPING`) with a centrally enforced mutating-tool counter and private per-runtime control/status handshake for supervisor drain requests.
- Add Phase E5 metadata-only operation recovery journaling for supervised mutations. In-flight operations from a dead runtime generation become explicit `status="uncertain"` records; arbitrary mutations are never replayed automatically.
- Add the benchmark-gated Windows Job Object ownership backend for MCP-owned and durable-worker-owned command trees, with suspended assign-before-run spawning, kill-on-close cleanup, psutil fallback, and emergency `MCP_WINDOWS_JOB_OBJECTS=0` opt-out.

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
- Reject new mutating MCP tools with retryable `runtime_draining`/`runtime_stopping` responses after the supervised runtime acknowledges drain, while allowing read-only calls to continue until cleanup.
- Require current-request drain acknowledgement plus `active_mutations=0` before user restart/stop cleanup, closing the race where a stale zero-count snapshot could otherwise be mistaken for a drained runtime.
- Retry lifecycle status publication on every valid control poll so transient Windows file-sharing contention cannot permanently lose the drain acknowledgement.
- Bound unhealthy-watchdog drain attempts separately from normal user restart/stop so a wedged runtime cannot block watchdog recovery indefinitely.
- Fail closed before a supervised mutation starts if its recovery-journal `begin` record cannot be durably appended/fsynced; tolerate a crash-truncated final record without allowing it to consume later recovery records.
- Prevent blind replay after watchdog/runtime failure: a side effect completed before an uncertain crash remains single-execution and is surfaced for reconciliation rather than reissued.
- Close Windows Job Object ownership when synchronous/durable command roots finish and proactively reap descendants of background roots even when no later `process_output` call occurs.
- Ensure Job Object setup/assignment fallback happens while the candidate process is still suspended, preventing double execution when nested assignment is unavailable on a host.
- Retry supervisor lifecycle-control atomic replacement across transient Windows sharing failures instead of abandoning a drain request on the first `os.replace` error.
- After a runtime has published at least one valid lifecycle status, keep the supervisor drain loop alive through transient status-read failures until the bounded deadline rather than treating the runtime as never-started.
- Bound the append-only operation-recovery journal with periodic compaction: completed history is discarded after the configured record threshold while pending and uncertain operations remain durable; compaction failure cannot turn an already-durable operation result into a retryable failure.

### Changed
- Browser benchmarks now report open latency, parallel-navigation latency, and cleanup latency for the 1/5/20-session cases, plus a dedicated background idle-eviction case with separate session and empty-pool reclamation timings.
- Refresh the README with an explicit completed-roadmap snapshot for Phases A-D (`v0.0.12` through `v0.0.15`), the benchmark-gated compact-tool decision, current post-D validation, and the next Phase E target.
- Document Phase E1 job timeout semantics explicitly: `timeout_sec` begins after command execution starts, while optional queue waiting uses its own deadline.
- Extend durable-job benchmarks with configured max-running, peak active-job, and peak queued-job details; short benchmark commands now remain alive long enough for admission behavior to be measurable.
- Extend the jobs benchmark with `job_wait` version-change, bounded-timeout, and 50-concurrent-waiter cases. E3 intentionally keeps adaptive SQLite polling with one reused connection per wait; Named Event/Pipe IPC remains deferred because the measured long-poll path does not justify the added recovery complexity.
- Increase the current typed MCP surface from 59 to 60 tools by adding the read-only `job_wait` API; this is an intentional Phase E feature addition, not the benchmark-rejected compact-tool profile from Phase D.
- Add configurable supervisor drain deadlines: `MCP_SUPERVISOR_DRAIN_SEC` defaults to 15 seconds for user restart/stop and `MCP_SUPERVISOR_WATCHDOG_DRAIN_SEC` defaults to 2 seconds for best-effort unhealthy-runtime recovery.
- Keep standalone/non-supervised MCP instances backward-compatible by making lifecycle mutation tracking managed only when the private supervisor control/status paths are configured.
- Expose a bounded `operation_recovery` summary through `server_health`, including uncertain and currently pending supervised mutations without journaling command/file/text payload bodies.
- Enable Windows Job Object ownership by default after the real-host gate; retain `MCP_WINDOWS_JOB_OBJECTS=0` as an emergency compatibility fallback and identify fallback execution as `psutil_fallback` in process results.

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
- Phase E4 validation completed with 247 passing pytest tests, Ruff with zero violations, mypy with zero issues across 84 source files, successful compileall, a passing 60-tool health check, and Full Doctor including a real disposable Chromium launch. Integration coverage includes a real `run_process` held active through drain, read-only access during drain, a real supervisor restart requested mid-mutation that waits for completion before cleanup, bounded watchdog fallback, durable-job restart survival, mutating-annotation guard coverage, and transient lifecycle-status publish retry.
- Phase E5 recovery tests cover durable `begin/result/uncertain` transitions, fsync failure before dispatch, crash-truncated journal tails, metadata-only records, health visibility, and a real watchdog restart where the side effect occurs exactly once and the interrupted operation becomes `uncertain` rather than being replayed.
- Phase E5 Windows Job Object integration passed on the real Windows host even though the MCP process itself was already inside a Job Object. Parent/grandchild kill-on-close, synchronous descendant cleanup, background root-exit cleanup, durable-worker crash cleanup, runtime-owned cleanup, and no-double-execution fallback paths all pass. A 30-run no-op calibration measured ordinary `Popen` at 36.166 ms median / 40.781 ms p95 versus 55.670 ms median / 56.384 ms p95 for suspended Job Object ownership, a 19.505 ms median fixed overhead accepted for deterministic process-tree ownership.
- Phase E5 final validation completed with 261 passing pytest tests, Ruff with zero violations, mypy with zero issues across 88 source files, successful compileall, a passing 60-tool health check, and Full Doctor in local-http mode including a real disposable Chromium launch.
- A dedicated post-E1–E5 hardening audit completed with **267 passing pytest tests**, Ruff with zero violations, mypy with zero issues across 90 source files, successful compileall, a passing 60-tool health check, and Full Doctor including a real disposable Chromium launch. Live stress checks reconfirmed 20 jobs from 4 independent submitter processes with an actual-command peak exactly equal to cap 3, 16 independent duplicate submitters converging to one job/one side effect, 1/10/50-job benchmark success with peak active jobs capped at 4, and 50 concurrent `job_wait` callers observing the same authoritative version. A synthetic 10,001-record / 1.84 MiB recovery journal compacted to 2 records / 453 bytes while preserving the uncertain operation, and a force-killed `safe_refactor` after the file write but before validation was recovered as `uncertain` without automatic replay.
- Phase E6 release-candidate validation passed a focused **71-test** crash/restart/recovery suite. The canonical 3-run jobs benchmark on audit commit `8e06fee` completed all 1/10/50 concurrent job cases successfully with `MCP_MAX_RUNNING_JOBS=4`; the 50-job median was **18.370 s** with peak active jobs **4**, peak process-tree RSS **509.648 MiB**, and peak process count **25**. The 50-waiter `job_wait` case observed version 2 across all waiters with **76.823 ms** wake latency, **75.387 MiB** peak RSS, and no additional child process.

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
