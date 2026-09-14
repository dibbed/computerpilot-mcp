# Changelog

All notable project-level changes are documented here.

Project releases are independent from the bundled upstream tunnel-client.exe version; binary runtime provenance remains in BINARY_PROVENANCE.md.

## [Unreleased]

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
