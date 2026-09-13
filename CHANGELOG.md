# Changelog

All notable project-level changes are documented here.

Project releases are independent from the bundled upstream tunnel-client.exe version; binary runtime provenance remains in BINARY_PROVENANCE.md.

## [Unreleased]

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
