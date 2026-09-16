# Windows Developer Agent MCP

A high-performance, full-access local Windows developer-agent backend powered by the Model Context Protocol (MCP Python SDK v2). The server provides atomic file operations, validated AST refactoring, durable background jobs, system diagnostics, and seamless integration with the OpenAI Secure MCP Tunnel.

---

## Current Status

The optimization roadmap is complete through **Phase F / v0.0.17 — State & Long-term Maintenance**. F1 through F6 are complete with bounded audit maintenance, backup/artifact/job-history retention, versioned provenance-aware project memory, centralized cache/storage budgets, bounded browser capacity, live resource telemetry in `server_health`, and final long-running/restart validation. Content-hash backup deduplication was benchmarked and intentionally not implemented because the real storage corpus did not justify the added complexity. The current project version is `0.0.17`.

| Phase | Release | Focus | Status |
| --- | --- | --- | --- |
| A | `v0.0.12` | Measurement & Observability | ✅ Complete |
| B | `v0.0.13` | Core Performance | ✅ Complete |
| C | `v0.0.14` | Project Intelligence | ✅ Complete |
| D | `v0.0.15` | Runtime & Browser | ✅ Complete |
| E | `v0.0.16` | Durable Jobs & Reliability | ✅ Complete |
| F | `v0.0.17` | State & Long-term Maintenance | ✅ Complete |

Phase A established structured timing and a repeatable benchmark baseline. Phase B added fast-start validation, bounded output/artifact reuse, keyed mutation locking, JobStore/query improvements, and single-flight/coalesced runtime work. Phase C added bounded version-aware Python metadata caching plus streaming and snapshot-based search pagination. Phase D completed shared Playwright browser pools, isolated session contexts, idle reclamation, crash/stale-session recovery, concurrency hardening, and browser lifecycle benchmarking.

The optional compact MCP tool surface considered during Phase D remains intentionally **disabled/not implemented**: the measured catalog serialization cost did not justify another tool-profile mode. Phase D therefore kept its 59-tool typed surface; Phase E3 intentionally adds one read-only typed tool, `job_wait`, bringing the current full surface to **60 tools**.

Final Phase-E validation on Windows completed with **267 pytest tests**. Phase F progressively raised that baseline through the F3 hardening audit (**311 tests**), F4 (**326 tests**), and the final F5/F6 release candidate at **330 pytest tests**. The `v0.0.17` release candidate passes Ruff with zero violations, mypy with zero issues across **99 source files**, compileall, the **60-tool** health check, and Full Doctor including a real disposable Chromium launch. F6 additionally completed three warning-clean state/concurrency soak cycles under Python `-X dev`, **20/20** fresh-process MCP health startups, a 20-cycle bounded storage-growth soak, and full-scale audit/backup/artifact/job-history benchmarks. `server_health` reports version `0.0.17` with normal resource pressure on the validation host.

---

## Features

- **Robust Filesystem Tools**: Atomic file creation, writing, copying, moving, and deletion; paginated listing and regex file search; complete reads with streaming byte cursors.
- **Precision Code Editing**: Exact match, anchored replacements, and AST function/class body substitutions with syntax validation and rollback on syntax error.
- **Process & Command Execution**: Synchronous and background execution for PowerShell, CMD, and native Windows executables with streaming spooling, bounded auto-delivery, byte cursors, reusable finalized artifacts, and Windows Job Object ownership for deterministic runtime-owned process-tree cleanup.
- **Durable Job Store**: SQLite-backed background job queue (`.agent_state/jobs.sqlite3`) with idempotency keys, bounded max-running admission, lightweight worker scheduling, survivor workers across supervisor restarts, monotonic state versions, bounded `job_wait` long-polling, persistent SQLite connections for running workers/waits, and terminal-only history/output retention that never prunes queued/running/orphaned jobs.
- **Concurrency Hardening**: Keyed resource locks protect filesystem read-modify-write operations and project-memory updates; browser navigation is serialized only within the same session rather than across unrelated sessions.
- **System Diagnostics**: Live inspection of CPU, memory, disks, environment variables, installed applications, and Windows services.
- **Codebase Intelligence**: Fast Python AST parsing for classes, functions, imports, and project summaries.
- **Validation Suite**: Compact summaries from integrated `pytest`, `Ruff`, and `mypy` tools.
- **Git Integration**: Working-tree status, diff statistics, and commit log pagination.
- **Optional Browser Automation**: Playwright automation with shared browser-process pools, isolated per-session contexts, idle eviction, crash recovery, screenshot capture, and UI interaction.
- **Resilient Supervisor & Control Panel**: Heartbeat watchdog, automatic backoff recovery, mutation-aware bounded drain before restart/stop, and a loopback-only control panel at `http://127.0.0.1:8766`.
- **Bounded Audit Trail**: Metadata-only audit events use a bounded process-local batching queue, cross-process file serialization, durable flushes for destructive operations, and size-based rotation/retention for long-running deployments.
- **Bounded Recoverable Backups**: Atomic filesystem edits keep recoverable `.bak` snapshots while background/coalesced retention enforces configurable age and byte budgets without deleting the newest restore point.
- **Bounded Delivery Artifacts**: Disk-backed output snapshots are retained by configurable age, count, and byte budgets; the artifact being returned and the newest snapshot are protected during cleanup, while stale cache entries self-heal by recreating missing snapshots.
- **Versioned Project Memory**: Compact 128 KiB project memory uses schema-v2 records with stable IDs, provenance, verification timestamps, project/item revisions, backward-compatible legacy migration, and cross-process optimistic concurrency checks.
- **Resource-Aware Health**: Centralized cache/storage/session budgets are exposed with live RSS, cache, browser, process, job, artifact, backup, and audit usage plus value/max/ratio pressure telemetry; browser logical sessions and process pools are hard-bounded in addition to idle eviction.

---

## Phase F1 — Audit Batching, Rotation & Retention

F1 keeps audit semantics metadata-only while removing per-event open/write/close overhead. Each process uses a bounded queue (`2048` by default), a single writer, batches up to `64` records, and flushes a partial batch within `50 ms`. Queue saturation falls back to synchronous append instead of silently dropping records. Destructive operations such as file deletion/move, process termination, job cancellation, and supervisor runtime termination use durable audit writes that are fsynced before returning; normal MCP lifecycle shutdown also explicitly flushes queued audit metadata.

Audit storage is now bounded by size-based rotation. The active `.agent_state/audit.jsonl` rotates at **8 MiB** by default and retains **5 rotated files** (`audit.1.jsonl` through `audit.5.jsonl`) in addition to the active file. Append and rotation are protected by a cross-process lock so the MCP runtime, supervisor, and independent durable-job workers can share the same audit trail safely. The policy is configurable with `MCP_AUDIT_BATCH_SIZE`, `MCP_AUDIT_FLUSH_MS`, `MCP_AUDIT_QUEUE_MAX`, `MCP_AUDIT_MAX_FILE_BYTES`, and `MCP_AUDIT_KEEP_FILES`.

The benchmark gate passed decisively on the validation host. With 20,000 metadata events and three runs, legacy synchronous audit writes measured **2077.088 ms** median at one thread and **2199.630 ms** at eight threads. The production batched writer measured **268.806 ms** and **396.403 ms** respectively while preserving all 20,000 JSONL records and the same payload bytes. This is why batching is enabled rather than remaining a deferred experiment.

---

## Phase F2 — Backup Retention & Deduplication Gate

F2 bounds `.agent_state/backups` without weakening the existing crash-safe edit pipeline. Recoverable backups are now published through a temporary file, fsynced, and atomically renamed before the target edit proceeds. Retention age is based on the UTC backup-event timestamp embedded in the backup filename rather than the source file's copied mtime, so a freshly-created backup of an old source file is never mistaken for an old backup.

Cleanup is intentionally background/coalesced instead of scanning the backup directory in every foreground write. It runs on MCP startup and after successful backup-producing writes, applies age retention first and then an oldest-first byte quota, and always preserves at least the newest restore point. A backup created by the current write is explicitly protected during that cleanup pass. Defaults are **30 days** and **256 MiB**, configurable with `MCP_BACKUP_MAX_AGE_DAYS` and `MCP_BACKUP_MAX_BYTES`; setting either limit to `0` disables that dimension. `MCP_BACKUP_CLEANUP_INTERVAL_SEC` controls the minimum interval between coalesced scans.

Content-addressed backup deduplication was evaluated but did not pass the production gate. The canonical F2 inventory on commit `e8cf7a0` scanned **1429 backups / 33,409,275 bytes** and found **169 duplicate files**, but their duplicate content represented only **95,960 bytes (0.2872%)** of recoverable storage. The gate requires at least **1 MiB** and **10%** potential savings, so a blob-store/dedup layer would add complexity without meaningful storage benefit and is intentionally not implemented. The same report's 2000-file synthetic retention case removed 1500 files (1000 by age and 500 by quota) and finished at **500 files / 1,024,000 bytes** with no cleanup errors.

---

## Phase F3 — Artifact & Durable-Job History Retention

F3 bounds the two remaining disk-backed output histories without changing live execution semantics. `.agent_state/artifacts` now uses age, count, and byte retention with defaults of **168 hours (7 days)**, **512 files**, and **512 MiB**. Artifact cleanup is coalesced/background, runs on runtime startup and after artifact creation, explicitly protects the artifact currently being returned plus the newest artifact, and ignores non-artifact files. Existing finalized-artifact cache entries already verify that the referenced file still exists, so a retained cache key whose old file has expired simply recreates a fresh snapshot on the next delivery request.

Durable-job retention is terminal-only. `succeeded`, `failed`, `cancelled`, `timed_out`, and `interrupted` history is bounded by **30 days**, **1,000 terminal rows**, and **1 GiB** of associated job output by default; `queued`, `running`, and `orphaned` jobs are never eligible. Cleanup runs from lifecycle maintenance rather than independent durable workers, preserving the Phase-E ownership model. `job_output` now takes the same keyed stdout/stderr locks used by cleanup, closing the row-read/output-delete race. For crash consistency, retention deletes an eligible terminal DB row transactionally before removing its output directory; if the process dies or directory deletion fails after the row commit, the leftover 32-hex job directory is an orphan and a later pass removes it only after rechecking that no DB row exists. The newest terminal result is retained as a safety floor even when it alone exceeds a configured quota. Once a terminal row ages out, its old idempotency key is no longer retained, so reusing that key after history expiry may create a new job.

The canonical F3 benchmark on commit `a2900da` first measured the real local corpus at **22 artifacts / 6,600,000 bytes** and **4 terminal jobs / 539 output bytes**. In the synthetic scale cases, the artifact cleanup pass scanned **5,000** files, removed **4,375** by age/count/quota, and completed in **1,121.777 ms**, leaving **625 files / 640,000 bytes** with zero errors. The job-history pass scanned **2,000 terminal rows**, removed **1,750** rows (1,000 by age, 500 by count, 250 by byte quota), completed in **1,732.422 ms**, left **250 terminal rows / 512,000 output bytes**, and preserved all **3/3 queued/running/orphaned rows**. The initial per-row SQLite deletion design measured roughly 14.9 seconds at this scale; batching cleanup into 100-job lock/transaction groups reduced that cleanup cost by about eightfold while preserving the same read/delete locking semantics.

---

## Phase F4 — Versioned Memory, Provenance & Optimistic Revisions

F4 upgrades compact project memory from section-level `list[str]` data to **schema v2** records while keeping the existing four sections and the **128 KiB** per-project ceiling. Each record now carries a stable `id`, immutable `text`, `source`, optional `source_ref`, `created_at`, optional `verified_at`, and an item-level `revision`; the project document carries its own monotonic `revision` and `updated_at`. New writes default to `source="manual"`, while callers can explicitly use `user`, `project_scan`, `manual`, or `tool`. Verification metadata is additive: re-verifying an already verified record is a no-op, while adding verification or changing provenance increments that record's revision and the project revision.

Legacy memory remains readable without rewriting the source file. Old string items are normalized in memory with deterministic stable IDs, `source="legacy"`, `revision=1`, and the legacy document revision remains `0` until a semantic update occurs. The first successful update materializes schema v2 atomically. A real read-only migration check against `memory/psychology_atlas.json` preserved its exact SHA-256 while normalizing **115 records** (46 architecture decisions, 34 important paths, 6 user preferences, 29 previous fixes); materializing a copy expanded the file from **18,839** to **37,041 bytes**, still safely below the 128 KiB budget.

`memory_update` now accepts `expected_revision`. A stale revision returns controlled `memory_conflict` guidance without writing, and destructive `replace=True` requires an explicit expected revision so a blind replace cannot silently overwrite newer memory. The check/merge/atomic-save sequence is serialized both by the existing process-local resource lock and by a cross-process lock stored under `.agent_state/memory_locks`, so two independent MCP processes racing from the same revision cannot both commit. In the cross-process acceptance test, exactly one revision-0 writer succeeded and the other received `memory_conflict`. Oversized structured updates fail with `memory_too_large` before replacement and preserve the previous file byte-for-byte. Memory remains data only; provenance or stored text does not grant tool authority or override runtime safety policy.

---

## Phase F5 — Central Resource Budgets & Runtime Health

F5 consolidates long-running resource ceilings behind `Settings.resource_budgets()` while keeping each subsystem responsible for its own eviction/retention policy. Existing AST cache, search snapshot, durable-job admission, artifact, backup, job-history, and audit budgets remain the source of truth; their age/TTL settings are surfaced alongside count/byte ceilings. Browser lifetime control is strengthened with hard defaults of **20 logical sessions** and **6 browser pools** (`MCP_BROWSER_MAX_SESSIONS`, `MCP_BROWSER_MAX_POOLS`) in addition to the existing idle reclamation. Session creation reserves capacity before asynchronous context/navigation work begins, so concurrent opens cannot oversubscribe the configured logical-session limit.

`server_health` now reports live `rss_mb`, AST cache entries/bytes, search snapshot count/bytes, browser sessions/pools/contexts, MCP-owned background processes, queued/running/orphaned jobs, job DB/output/storage bytes, artifact count/bytes, backup count/bytes, audit file count/bytes, and an overall `resource_pressure` classification. A separate `resource_usage` map exposes bounded resources as `{value, max, unit, usage_ratio}`, while `resource_budgets` exposes the configured ceilings/TTLs. Health collection is observational only: it does not evict caches, kill processes, prune storage, or override subsystem policy.

The current host reports normal pressure with roughly **74 MiB RSS**, **22 artifacts / 6.6 MB**, about **35 MB of backups**, about **3.4 MB of audit data**, **4 terminal jobs**, and no active browser/background/durable-job work at measurement time. A 50-run resource-health timing gate measured **82.87 ms median**, **85.24 ms p95**, and **98.82 ms maximum** on the bounded state corpus. Because the control-plane health scan remains below 100 ms and all scanned storage domains are already bounded, the roadmap's optional incremental storage counters remain deferred rather than adding reconciliation complexity prematurely.

---

## Phase F6 — Long-Running Validation & Release

F6 validates the completed Phase-F state model rather than adding another runtime feature. The release-candidate soak ran three complete cycles of the audit, backup, artifact, job-retention, memory, browser, and MCP-integration suites under Python `-X dev` with `ResourceWarning` promoted to an error. A separate restart/recovery pass covered durable workers, admission, lifecycle drain, operation recovery, supervisor restart, and launcher behavior. That pass exposed two validation-harness resource leaks—an unclosed crash-helper stderr pipe and unclosed HTTP error responses—which were fixed before repeating the suite warning-free.

Fresh-process stability was checked with **20 independent MCP health startups**, all returning the full **60-tool** surface. A 20-cycle storage-growth soak repeatedly added backup, artifact, and terminal-job history data and verified convergence on the configured synthetic ceilings every cycle: backups and artifacts remained at or below **64 KiB**, job history at or below **50 KiB**, and all three queued/running/orphaned control rows survived every cleanup pass. Full-scale release benchmarks also revalidated **20,000 audit events**, **2,000 backups**, **5,000 artifacts**, and **2,000 terminal jobs** with zero retention errors or lost active state; the live backup corpus still fails the deduplication gate at only about **0.27%** potential savings.

---

## Architecture

The application runs in two primary modes:

1. **Secure MCP Tunnel Mode (Default)**:
   - `START_MCP.bat` launches `scripts/bootstrap.ps1`.
   - Bootstraps `.venv` (Python 3.10+) and uses a successful-validation fingerprint for a fast preflight; full dependency/MCP/Tunnel checks run only when validation is required or when `scripts.doctor` is invoked explicitly.
   - Launches `scripts/supervisor.py` managing `tunnel-client.exe`.
   - `tunnel-client.exe` creates an outbound encrypted tunnel to the OpenAI control plane, invoking companion `cloudflared.exe` for Cloudflare edge routing.
   - Outbound requests from the control plane are forwarded over loopback to the local MCP server (`main.py`).

2. **Local HTTP Mode**:
   - Bypasses external tunnels entirely.
   - Starts a Streamable HTTP MCP server listening on `http://127.0.0.1:8765/mcp`.

---

## Requirements

- **Operating System**: Windows 10 / Windows 11 (x86-64 / amd64)
- **Python**: Python 3.10 or newer (managed automatically in `.venv` by the launcher)
- **PowerShell**: Windows PowerShell 5.1 or PowerShell 7+
- **Credentials** (Tunnel Mode only): A valid control-plane API key stored locally outside version control.

---

## Quick Start

### Starting in Default Tunnel Mode

1. Double-click `START_MCP.bat` or run:
   ```cmd
   START_MCP.bat
   ```
2. Keep the launcher window open. On a valid fingerprint the launcher performs only the fast preflight; otherwise it runs the full doctor before starting the runtime supervisor.
3. Access the local operator panel at `http://127.0.0.1:8766/`.

To stop the service, press `Ctrl+C` in the terminal window or click **Stop** in the local control panel.

---

## Local HTTP Mode

To run locally without connecting to the OpenAI Tunnel:

```powershell
$env:MCP_START_MODE = 'local-http'
.\START_MCP.bat
```

The local Streamable HTTP MCP endpoint will be available at:
`http://127.0.0.1:8765/mcp`

### Fast Startup and Full Doctor

A successful full validation is fingerprinted in ignored local state. Normal startup checks that fingerprint and skips the expensive dependency/MCP smoke path when the validated inputs have not changed. Changes to Python, requirements, project source, relevant package metadata, tunnel binaries/profile configuration, or relevant runtime environment invalidate the fingerprint and force full validation.

Run the complete diagnostic path explicitly with:

```powershell
.\.venv\Scripts\python.exe -m scripts.doctor
```

The full doctor runs dependency imports, `pip check`, MCP/filesystem/terminal smoke checks, optional browser validation, content-search backend diagnostics, and Tunnel doctor checks in Tunnel mode. Search diagnostics report `content_search_backend`, `ripgrep_path`, and `ripgrep_version` without logging file contents or credentials.

---

## Local Configuration

### Control Plane API Key

When operating in Tunnel mode, the tunnel daemon requires a control-plane API key. This key is loaded locally using one of the following mechanisms:

1. **Secret File (Recommended)**:
   Place the API key in:
   ```
   .secrets\control_plane_api_key.txt
   ```
   *(This path is strictly ignored by `.gitignore` and is never committed).*

2. **Environment Variable**:
   Set `CONTROL_PLANE_API_KEY` before starting:
   ```powershell
   $env:CONTROL_PLANE_API_KEY = "your-api-key"
   ```

### Tunnel Profile

By default, the launcher automatically detects any configured local tunnel profile. To specify an explicit profile:

```powershell
$env:MCP_TUNNEL_PROFILE = "<your-profile>"
.\START_MCP.bat
```

### Bounded Output Delivery

Command and captured-output tools preserve the legacy full-inline default unless the controlled migration flag is enabled. The flag applies to `run_process`, `run_powershell`, `run_cmd`, `process_output`, and `job_output`. To make large responses bounded by default:

```powershell
$env:MCP_OUTPUT_DEFAULT = 'auto'
```

`delivery="inline"` remains an explicit full-inline request. `delivery="auto"` keeps small output inline, returns a bounded preview plus cursor/full-source metadata for medium output, and returns an artifact descriptor plus bounded preview for large output. Full bytes are retained; auto delivery does not discard command output.

Optional policy controls:

```text
MCP_INLINE_SOFT_LIMIT_BYTES   # default: 131072 (128 KiB)
MCP_INLINE_HARD_LIMIT_BYTES   # unset by default; safety ceiling for explicit inline
MCP_PREVIEW_BYTES             # default: 262144 (256 KiB), internally capped at 1 MiB
```

The artifact descriptor includes a stable path/SHA-256 for finalized snapshots, and repeated final file delivery reuses the existing descriptor instead of copying and hashing the same output again. Finalized artifact cache keys use canonical Windows path identity so case/path aliases do not create duplicate snapshots.

### Core Performance and Concurrency Hardening

The `0.0.13` release established the Core Performance baseline. Version `0.0.14` keeps that design and includes the hardening below:

- Filesystem read-modify-write tools hold keyed, canonical-path resource locks for the full mutation transaction. Multi-path operations acquire keys in stable order to avoid lock-order deadlocks while unrelated files remain concurrent.
- Project-memory updates use the same process-local keyed locking model, preventing same-runtime lost updates while preserving atomic file replacement.
- Browser operations use per-session locks. A slow navigation in one session does not serialize unrelated browser sessions.
- Durable job tools reuse one `JobStore` for the MCP server lifetime. Status/list queries fetch only the columns they need, avoid N+1 reads, and do not materialize private command specs or fingerprints.
- Each durable worker skips redundant schema initialization and owns one SQLite connection for its runtime polling/update loop.
- Control-panel process/job/storage aggregation uses short TTLs plus single-flight refreshes so concurrent `/api/status` requests do not repeat the same expensive work.
- Normal startup uses a successful-validation fingerprint and keeps the full Doctor separate; unchanged startup avoids repeated dependency/MCP smoke validation.
- Pytest temporary/cache state is kept under ignored `.agent_state/`, avoiding Windows `%TEMP%` symlink cleanup failures and repository-root cache permission noise.

These locks and caches are process-local coordination mechanisms. External editors/processes remain outside that locking boundary, so stale-write-sensitive file replacement should still use `expected_sha256` where appropriate.

### Project Intelligence and Search Pagination

Version `0.0.14` adds bounded reusable project intelligence and an explicit fast/consistent search model:

- Python project tools cache compact per-file metadata instead of retaining full ASTs. Cache identity is the canonical path plus `mtime_ns` and file size, with LRU limits from `MCP_AST_CACHE_MAX_FILES` (default `10000`) and `MCP_AST_CACHE_MAX_BYTES` (default `67108864`).
- Concurrent requests for the same Python source share one per-path parse. Successful MCP filesystem mutations invalidate affected files/subtrees immediately, while edits made by external programs are still detected through the file-version check on the next read.
- `find_function`, `find_class`, `find_imports`, and `dependency_graph` reuse this metadata cache. A changed file is reparsed without forcing unrelated cached files to be reparsed.
- `search_files` keeps `count_mode="exact"` as the backward-compatible default: it performs the bounded full scan, returns a globally path-sorted page, and reports `total_count`.
- `count_mode="none"` is the fast name-search mode. It returns traversal-order results, leaves `total_count` as `null`, and stops once it has enough matches to answer the requested page plus `has_more`. Content/both searches continue to require exact mode.
- `snapshot=true` is opt-in consistent pagination. It intentionally completes the bounded search first, writes an immutable JSONL snapshot under ignored `.agent_state\search_snapshots\`, and returns an opaque `cursor`. Cursor pages are read from that fixed snapshot and do not rescan the filesystem, so later file changes do not reorder or replace already-snapshotted results.
- Cursors are bound to the canonical root and all result-shaping search parameters. They expire instead of silently switching to a fresh search. Snapshot publication is atomic, IDs are random rather than user-supplied paths, and expired/old snapshots are removed under TTL/count/byte quotas.

Snapshot policy controls:

```text
MCP_SEARCH_SNAPSHOT_TTL_SEC     # default: 180 seconds
MCP_SEARCH_SNAPSHOT_MAX_BYTES   # default: 134217728 (128 MiB total budget)
MCP_SEARCH_SNAPSHOT_MAX_COUNT   # default: 32 snapshots
```

Use streaming mode when lowest first-page latency matters. Use `snapshot=true` when stable multi-page traversal matters more than first-page latency. If a bounded scan itself is truncated, the response reports `scan_truncated=true`; it never invents a cursor beyond results that were actually captured.

### Durable Job State Foundation (Phase E1)

Phase E1 prepares the durable-job subsystem for bounded admission and future version-aware waiting without changing the per-running-job worker architecture:

- Phase E1 introduced explicit SQLite job-schema revisioning. The current E2 schema is `PRAGMA user_version=3`: existing `jobs.sqlite3` files migrate in place under serialized `BEGIN IMMEDIATE` transactions, preserving rows, request keys, command specs, and idempotency fingerprints while adding internal launch-reservation metadata.
- Every durable job row has a monotonic integer `version`. New jobs start at `version=1`; worker claim, process metadata, cancellation, reconciliation, timeout, failure, and terminal-state updates increment the version. Public job status/list/output responses expose the current version for later `after_version` waiting.
- `timeout_sec` remains the execution timeout and starts only after the command process has actually started. Queue age is no longer charged against execution time.
- `queue_timeout_sec` is a new optional submit parameter. Its default is `None`, so queued jobs do not expire merely because they have waited longer than 60 seconds. When provided, it is stored as an absolute `queue_deadline`; expiry becomes a terminal `timed_out` result with an explicit queue-timeout error.
- The worker's atomic `queued -> running` claim includes the queue deadline predicate, so a job cannot race past an already-expired queue deadline even if status reconciliation and worker startup happen concurrently.
- Default submissions deliberately keep the pre-E1 fingerprint shape when no queue deadline is requested, so idempotency keys created before the migration remain reusable without false conflicts.
- Schema initialization is safe under concurrent JobStore creation, including WAL setup contention; databases with a future unsupported jobs schema are rejected instead of being silently downgraded.

E1 deliberately left admission and `job_wait` for later subphases. E2 implemented admission/launch control, and E3 now consumes the monotonic version foundation through bounded `job_wait` long-polling.

### Durable Job Admission Control (Phase E2)

Phase E2 keeps SQLite as the authoritative job state and preserves one independent worker per running job; it does **not** introduce a shared resident `jobd`:

- `MCP_MAX_RUNNING_JOBS` bounds active durable jobs across processes; the conservative default is `4` and the accepted range is `1..256`. Both `running` jobs and `orphaned` commands that are still alive consume capacity.
- Worker claims are serialized with `BEGIN IMMEDIATE` and capacity is derived from authoritative row state rather than a separate slot counter. Terminal transitions therefore release capacity naturally, while stale running/orphaned rows are reconciled before admission so a crashed worker cannot permanently leak a slot.
- Queue cancellation is race-safe: queued jobs become terminal `cancelled` atomically, while a claim that wins first can only leave a running row with `cancel_requested=1`; a queued cancellation cannot silently race into an uncancelled command.
- The initial E2 implementation let every queued job create a waiting worker. A real 50-job benchmark kept `peak_active_jobs=4` but still reached 155 processes and 3280.344 MiB peak process-tree RSS, so the roadmap's second-stage scheduler gate was triggered.
- The final E2 launcher uses short-lived SQLite launch reservations (`launch_token` / `launch_started`) so concurrent schedulers reserve at most available capacity. Stale reservations are recoverable, a reserved worker must present the matching token before claiming the job, and launch failure becomes a durable terminal failure instead of an ambiguous replay.
- Submission performs a synchronous launch pass before returning, so a short-lived submitter can exit without stranding the first durable worker. Each terminal worker also kicks a successor before exit; a small background scheduler remains only for recovery/coordination while queued work exists. Running commands remain independent processes and survive MCP runtime restarts.
- On the same Windows host, the final committed 50-job default-cap benchmark (`e9fc0b9`) kept `peak_active_jobs=4` and completed in 20817.842 ms while reducing peak process-tree RSS from 3280.344 MiB to 491.629 MiB and peak process count from 155 to 25. A calibration run with `MCP_MAX_RUNNING_JOBS=8` completed the 50-job case in 17955.369 ms at 748.305 MiB / 37 processes; the default remains `4` to favor predictable local resource use. These are host-specific regression measurements, not universal guarantees.

Configuration:

```text
MCP_MAX_RUNNING_JOBS=4
```

Short-job throughput is intentionally traded for bounded resource use because each running job still owns an independent durable worker. A warm shared worker pool remains deferred/conditional rather than being folded into E2.

### Versioned Durable Job Wait (Phase E3)

Phase E3 adds a typed read-only long-poll API without introducing Windows event/pipe IPC:

```text
job_wait(job_id, after_version, timeout=30)
```

- `version` remains the authoritative synchronization primitive. `after_version=0` returns the current job state immediately because every job starts at version 1; when the current version equals `after_version`, the call waits until that version advances or the bounded timeout expires.
- The public timeout range is `0..300` seconds. Timeout is a normal response (`changed=false`, `timed_out=true`) rather than an exception, and it includes the latest observed job state/version so the caller can immediately wait again.
- If `after_version` is ahead of the authoritative current version, the call returns controlled `job_version_ahead` guidance instead of waiting for an impossible/mismatched state.
- Each wait reuses exactly one SQLite connection for its lifetime. Polling is adaptive: 50 ms during the first second, 100 ms through 5 seconds, 250 ms through 30 seconds, then 500 ms for longer waits. State reconciliation still runs while waiting, so queue-deadline expiry and dead-worker/orphan transitions can advance the version even without another client call.
- The synchronous MCP tool is offloaded by the SDK rather than blocking the server event loop. Integration coverage verifies a state update can execute while `job_wait` is blocked.
- Lost notifications are avoided by design because there is no event as the source of truth: every wake cycle re-reads the monotonic version. A change occurring between a read and sleep is observed on the next bounded poll.

The committed E3 benchmark (`9b328f8`) on the local Windows host measured a single waiter waking **54.006 ms** after the authoritative update, a 200 ms timeout returning in **211.776 ms**, and **50 concurrent waiters** all observing the same version change with **46.787 ms** wake latency, **75.164 MiB** peak process-tree RSS, and no extra child processes. These host-specific results do not justify the extra failure/recovery complexity of Named Events or pipes, so event IPC remains deliberately deferred behind the roadmap benchmark gate.

### Runtime Mutation Drain and Restart Safety (Phase E4)

Phase E4 gives the MCP runtime an explicit lifecycle used only when it is supervised:

```text
RUNNING → DRAINING → STOPPING
```

- Every MCP tool whose annotation is mutating/non-read-only is centrally guarded by `core/tooling.py`. The guard set is verified against the registered tool annotations so a future mutating tool cannot silently bypass lifecycle tracking.
- While `RUNNING`, each mutating tool increments a process-local active-mutation counter for the complete tool body and decrements it in `finally`, including command execution, filesystem validation/refactor work, browser/desktop mutations, job submit/cancel, and test/lint execution. Durable job commands themselves are not owned by this counter after submission and retain their existing restart-survival semantics.
- The supervisor gives each runtime generation unique private control/status files under local state. On restart/stop it writes a `drain` request; the runtime watcher acknowledges `DRAINING`, rejects new mutations with retryable `runtime_draining`, continues serving read-only tools, and publishes `active_mutations` until it reaches zero. Only after an acknowledged zero count does the supervisor send `stop` and clean up the runtime.
- The handshake closes the enter-vs-restart race: the supervisor never treats a stale `active_mutations=0` snapshot as drained; it requires the runtime to acknowledge the current request ID after entering `DRAINING`. Status publication is retried on every valid control poll so transient Windows read/replace sharing contention cannot permanently lose an acknowledgement.
- User restart/stop uses `MCP_SUPERVISOR_DRAIN_SEC` (default `15` seconds). Watchdog recovery is deliberately best-effort and uses `MCP_SUPERVISOR_WATCHDOG_DRAIN_SEC` (default `2` seconds), so an actually unhealthy runtime cannot block recovery forever. If the MCP lifespan never reached request-serving state and produced no lifecycle status, the supervisor does not spend the full drain deadline waiting for an impossible acknowledgement.
- Standalone MCP servers without supervisor lifecycle paths remain backward-compatible: mutation guards are unmanaged/no-op, so direct development/test servers are not left in a stale `STOPPING` state across repeated in-process server instances.

E4 integration coverage includes a real `run_process` mutation staying active across a drain request, read-only access continuing while new writes are rejected, a real supervisor restart requested during an in-flight mutation that waits for completion before cleanup, bounded watchdog fallback for a mutation that does not finish, durable-job restart survival, and retry of a transiently failed status-file acknowledgement.

### Uncertain Operations and Windows Process Ownership (Phase E5)

Phase E5 completes the reliability hardening that deliberately remained outside E4. It does **not** retry arbitrary mutations after a restart.

- Every supervised mutating tool writes a metadata-only `begin` record to ignored local `.agent_state/operation-recovery.jsonl` and `fsync`s it **before** user-visible mutation work starts. Records contain an operation ID, runtime-generation ID, operation type, bounded target identity, timestamps, and known-result state; command arguments, file contents, typed text, and other payload bodies are not journaled.
- Normal completion writes a durable `status="completed"` result. If a runtime disappears after `begin` but before a known result, the next runtime generation appends `status="uncertain"` with `reason="runtime_ended_without_known_result"`. `server_health.operation_recovery` exposes the bounded uncertain summary so the state is detectable and can be reconciled manually/client-side.
- Journal begin is fail-closed: if the runtime cannot durably record recovery metadata, the mutation is not started. A truncated final JSON record after a crash is ignored, and future appends first restore a newline boundary so a damaged tail cannot consume the subsequent uncertainty record.
- There is intentionally no supervisor callback that re-executes journaled operations. A watchdog integration test kills a runtime in the middle of a side effect; generation two marks the operation uncertain while the side-effect marker remains exactly once.
- On Windows, normal MCP-owned command processes now start with `CREATE_SUSPENDED`, are assigned to a non-inheritable Job Object configured with `KILL_ON_JOB_CLOSE`, and only then have their primary thread resumed. This removes the child-assignment window. When the runtime dies, the OS closes its Job Object handles and cleans the entire owned tree even if Python cleanup never runs.
- Durable job commands use the same backend but the Job Object handle belongs to the independent **job worker**, not the MCP runtime. Therefore an MCP/supervisor restart does not kill durable work, while a worker crash deterministically terminates that worker's command and descendants. Existing psutil cleanup remains the fallback for external PIDs and for hosts where Job Object setup is unavailable.
- The Windows backend has an emergency opt-out, `MCP_WINDOWS_JOB_OBJECTS=0`. When enabled but unavailable, fallback occurs while the first candidate process is still suspended, so user code is not double-executed; responses report `process_ownership="psutil_fallback"` rather than pretending Job Object ownership succeeded.

The E5 gate passed on the real Windows validation host while the MCP process itself was already inside a Job Object, proving nested assignment in this environment. A parent and grandchild were both alive before Job Object close and both dead afterward; runtime-owned background cleanup and worker-crash cleanup also passed. In a 30-run no-op spawn calibration, ordinary `Popen` measured **36.166 ms median / 40.781 ms p95**, while suspended Job Object spawning measured **55.670 ms median / 56.384 ms p95**, a **19.505 ms median fixed overhead** accepted in exchange for deterministic ownership. These are host-specific validation numbers, not universal performance guarantees.

### Runtime and Browser Pooling

Version `0.0.15` changes browser ownership from one Playwright `Browser` per session to shared compatible browser pools with one isolated `BrowserContext` per session:

- Pool identity includes browser engine, headless mode, and launch-affecting configuration, so incompatible configurations never share a process.
- Compatible sessions share one Playwright `Browser` instance while cookies, local/session storage, pages, and navigation state remain isolated inside separate contexts.
- Closing one session closes only that session's context. Reopening the same session with an incompatible configuration is transactional: the old context stays usable until the replacement context has opened and navigated successfully.
- Per-session locking remains the operation boundary. Navigation or interaction in one session does not reintroduce a global browser lock.
- Idle contexts are reclaimed after `MCP_BROWSER_IDLE_SEC` (default `900` seconds). An empty browser pool is reclaimed after `MCP_BROWSER_POOL_IDLE_SEC` (default `120` seconds). Cleanup is single-flight and will not evict a context while an operation or context attach is active.
- Browser disconnects invalidate the dead pool and mark dependent sessions stale. Externally closed pages/contexts are also detected as stale without discarding a still-healthy Browser process: non-open operations return `browser_session_stale`, while `browser_open_page` recreates the session context inside the compatible pool. A page/context closed during navigation is distinguished from a Browser-process disconnect, and launch/context failures never publish half-created sessions.

Browser idle controls:

```text
MCP_BROWSER_IDLE_SEC       # default: 900 seconds
MCP_BROWSER_POOL_IDLE_SEC  # default: 120 seconds
```

The browser benchmark reports Playwright browser-instance count and active-context count separately from operating-system process-tree count. On the local Windows validation host, the original D release comparison changed the 20-session Chromium case from 20 Browser instances in `v0.0.14` to 1 shared Browser instance plus 20 contexts in `v0.0.15`; peak process-tree RSS changed from 3757.762 MiB to 1506.699 MiB, peak process count from 83 to 26, and elapsed time from 2606.737 ms to 1272.600 ms. The final post-D harness additionally records open, parallel-navigation, cleanup, and idle-reclamation timings. On the final validation run, 20 Chromium sessions still used exactly 1 Browser plus 20 contexts, and the mixed 5 Chromium + 5 Firefox case completed with exactly 2 Browser instances plus 10 isolated contexts. These host-specific measurements are regression evidence, not universal performance guarantees.

---

## Bundled Tunnel Runtime

This repository includes pre-built third-party runtime binaries required for the OpenAI Secure MCP Tunnel:

- `tunnel-client.exe`: OpenAI Secure MCP Tunnel client daemon.
- `cloudflared.exe`: Cloudflare Tunnel companion daemon.
- `cloudflared-manifest.json`: Upstream pinned build manifest.

### Why Both Executables Are Included

The project utilizes OpenAI's Tunnel client ([openai/tunnel-client](https://github.com/openai/tunnel-client)). The supported Windows distribution model places `tunnel-client.exe` and `cloudflared.exe` side by side. When establishing a tunnel over Cloudflare edge transport, `tunnel-client.exe` internally discovers and executes `cloudflared.exe` from its adjacent directory. 

Even though this project's Python codebase does not invoke `cloudflared.exe` directly, **deleting `cloudflared.exe` will break tunnel connectivity**. Both executables and their manifest must remain together as a matched set.

For detailed provenance, versions, and cryptographic hashes, see [BINARY_PROVENANCE.md](BINARY_PROVENANCE.md).

---

## Updating the Bundled Tunnel Runtime

When an upstream release of the tunnel client is available, maintainers must treat the runtime components as a **matched set** (`tunnel-client.exe`, `cloudflared.exe`, `cloudflared-manifest.json`). Users should **not** independently replace only one component unless official upstream compatibility documentation explicitly permits it.

Follow this verified 19-step update procedure:

1. **Official Upstream Releases Only**: Go ONLY to the official OpenAI repository releases:
   [https://github.com/openai/tunnel-client/releases](https://github.com/openai/tunnel-client/releases)
2. **Select Architecture**: Select the correct official Windows architecture/build (e.g., `windows-amd64`).
3. **Prefer Bundled Release Artifact**: Prefer the official bundled release artifact containing the compatible `tunnel-client` + `cloudflared` pair and associated manifest/provenance files (e.g., `tunnel-client-v<version>-windows-amd64.zip`).
4. **Download Integrity Metadata**: Download integrity metadata supplied by the release, such as `SHA256SUMS.txt`, provenance/signature files, or equivalent, when provided upstream.
5. **Verify Downloaded Artifact**: Verify the downloaded artifact hash BEFORE replacing repository binaries:
   ```powershell
   Get-FileHash -Algorithm SHA256 .\tunnel-client-v<version>-windows-amd64.zip
   ```
6. **No Unofficial Mirrors**: Never download replacement executables from mirrors, forums, random file hosts, or unofficial repositories.
7. **Extract Outside Project**: Extract the official bundle into a temporary directory outside the project first.
8. **Confirm Versions**: Confirm component versions before replacement:
   ```powershell
   .\tunnel-client.exe --version
   .\cloudflared.exe --version
   ```
9. **Replace Matched Set Together**: Replace the intended matched runtime files together in the repository root (`tunnel-client.exe`, `cloudflared.exe`, and `cloudflared-manifest.json`).
10. **Recompute Local Checksums**: Recompute local SHA-256 checksums after replacement:
    ```powershell
    Get-FileHash -Algorithm SHA256 .\tunnel-client.exe, .\cloudflared.exe, .\cloudflared-manifest.json
    ```
11. **Update Provenance Documentation**: Update [BINARY_PROVENANCE.md](BINARY_PROVENANCE.md) with:
    - New `tunnel-client.exe` version, commit SHA, and hash.
    - New `cloudflared.exe` version, build date, and hash.
    - New `cloudflared-manifest.json` hash.
    - Upstream release tag, commit, and update date.
12. **Update Third-Party Licensing**: Update applicable third-party license/NOTICE files ([THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), `third_party/`) if upstream licensing material changed.
13. **Run Safe Validation Suite**: Execute the safe local validation suite:
    ```powershell
    .\.venv\Scripts\python.exe -m pytest
    .\.venv\Scripts\python.exe -m ruff check .
    .\.venv\Scripts\python.exe -m mypy core tools scripts tests
    .\.venv\Scripts\python.exe -m compileall -q core tools scripts tests
    ```
14. **Local HTTP Smoke Test**: Perform a local HTTP smoke test that does not require production credentials:
    ```powershell
    $env:MCP_START_MODE = 'local-http'
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
    ```
15. **Local Tunnel Validation**: Perform Tunnel validation separately using the maintainer's LOCAL credential/profile stored outside version control.
16. **Run Tunnel Doctor**: Run the Tunnel doctor command using a generic profile placeholder:
    ```powershell
    .\tunnel-client.exe doctor --profile <your-profile>
    ```
17. **Confirm Startup**: Confirm startup still works cleanly with `START_MCP.bat`.
18. **Review Changes**: Review git status, working tree diffs, binary hashes, [BINARY_PROVENANCE.md](BINARY_PROVENANCE.md), and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md):
    ```powershell
    git status
    git diff BINARY_PROVENANCE.md THIRD_PARTY_NOTICES.md
    ```
19. **Commit Jointly**: Commit the updated binary set and provenance documentation together in a single commit.

---

## Local Control Panel

The supervisor exposes a local loopback management interface at `http://127.0.0.1:8766/`:

- **Health Monitoring**: Displays live health state of the MCP server, heartbeat timestamp, and Tunnel `/readyz` endpoint.
- **Process Management**: Shows active child processes, memory usage, and allows clean **Restart** or **Stop**.
- **Log Streaming**: Live view of rotated supervisor events with automatic redaction of sensitive credentials.
- **Security Boundary**: The panel binds strictly to loopback (`127.0.0.1`), enforces strict Host checking, validates browser Origin headers, and requires a dynamic per-session CSRF token (`X-Control-Token`).

---

## Testing and Validation

The project includes an automated test suite, static type checking, and linter validation:

```powershell
# Run unit and integration tests
.\.venv\Scripts\python.exe -m pytest

# Run Ruff linter
.\.venv\Scripts\python.exe -m ruff check .

# Run Mypy static type checker
.\.venv\Scripts\python.exe -m mypy core tools scripts tests

# Run MCP startup smoke check
.\.venv\Scripts\python.exe -m scripts.health_check --json
```

The final Phase-D validation snapshot on Windows is **203 passing pytest tests**, Ruff with zero violations, mypy with zero issues across 79 source files, successful `compileall`, a passing 59-tool MCP health check, and a passing Full Doctor using the project `.venv`. Real Playwright validation covers Chromium launch/recovery, externally closed page/context recreation, idle context/pool reclamation, and mixed Chromium/Firefox pool isolation.

### Performance Timing

Structured tool timing is opt-in and never changes MCP tool response payloads. Enable it only while profiling:

```powershell
$env:MCP_TIMINGS = '1'
.\START_MCP.bat
```

Timing records are metadata-only JSONL entries written to `.agent_state\timings.jsonl`. They include input validation, queue/dispatch wait, tool body, result conversion, MCP request pipeline, response serialization probe, process spawn/execution/drain, output delivery, and artifact snapshot phases. Command arguments, file contents, typed browser text, and other payload bodies are not recorded. Timing writes are coalesced through a bounded background writer so profiling I/O does not sit directly on the tool hot path. The serialization phase is an equivalent response-model serialization probe used only while profiling; it records only duration and byte count. `MCP_TIMINGS_FILE` can override the local output path for isolated runs.

### Repeatable Performance Benchmarks

The benchmark harness stores reports under ignored local state by default:

```powershell
# Fast representative suite; real jobs and browsers remain opt-in
.\.venv\Scripts\python.exe -m scripts.perf_benchmark --profile quick

# Include real durable worker launches
.\.venv\Scripts\python.exe -m scripts.perf_benchmark --profile quick --include-jobs

# Include the optional Playwright runtime
.\.venv\Scripts\python.exe -m scripts.perf_benchmark --profile quick --include-browser

# Full scale points: 100 MiB output, 10K AST files, 100K search files,
# and up to 50 jobs / 20 browser sessions when those suites are enabled.
.\.venv\Scripts\python.exe -m scripts.perf_benchmark --profile full --include-jobs --include-browser
```

Use repeated runs for comparisons, for example `--runs 5`. Reports contain latency distributions, full process-tree peak RSS and I/O samples, complete wire-style tool catalog bytes, and case-specific details. Startup includes the real `scripts/bootstrap.ps1` validation path as well as cold/warm MCP server construction. Output cases preserve the current default delivery behavior instead of forcing file delivery, and project cases include both raw AST parsing and a real `find_function` tool lookup so metadata-cache work remains measurable. Search cases separately measure exact scans, streaming first-page latency, snapshot creation, and snapshot page-two reads with zero filesystem rescan. Browser cases report shared Browser-instance/context counts plus open, parallel-navigation, and cleanup latency; the full profile also measures background idle-session/empty-pool reclamation and attempts a 5 Chromium + 5 Firefox mixed-pool case. Benchmark temp directories older than 24 hours are cleaned on a later run. Heavy job/browser cases require explicit opt-in so a normal benchmark cannot accidentally create a large process burst.

---

## Security Notes

- **Credentials Outside Version Control**: The repository strictly ignores `.secrets/` and `.env*`. Never stage or commit credentials.
- **Log Masking**: The runtime supervisor and launcher automatically mask `CONTROL_PLANE_API_KEY` with `[redacted]` before writing to logs.
- **Loopback Enforcement**: All internal HTTP servers (MCP Streamable HTTP on port `8765`, Control Panel on port `8766`) bind exclusively to `127.0.0.1`.
- **Local State Isolation**: Working databases (`.agent_state/jobs.sqlite3`), session caches, and generated artifacts are stored in `.agent_state/` and ignored by Git.
- **Audit Logging**: Destructive and mutating file operations log metadata (timestamps, tool name, targets) to `.agent_state/audit.jsonl`. Content bodies and secret arguments are excluded from audit records.

---

## Project Structure

```
.
├── .gitignore                     # Enterprise-grade Git ignore configuration
├── .gitattributes                 # Binary and text line-ending rules
├── .editorconfig                  # Formatting and encoding standards
├── BINARY_PROVENANCE.md           # Provenance and SHA-256 records for bundled binaries
├── CONTRIBUTING.md                # Development and contribution guide
├── LICENSE                        # Apache License 2.0
├── NOTICE                         # Project copyright and third-party attribution
├── README.md                      # This documentation
├── SECURITY.md                    # Vulnerability reporting guidelines
├── START_MCP.bat                  # User-facing Windows launcher
├── THIRD_PARTY_NOTICES.md         # Attribution for bundled third-party code
├── cloudflared-manifest.json      # Upstream Cloudflare build manifest
├── cloudflared.exe                # Bundled Cloudflare companion binary
├── pyproject.toml                 # Tool configuration (pytest, ruff, mypy)
├── requirements.txt               # Core Python dependencies
├── requirements-browser.txt       # Optional browser automation dependencies
├── tunnel-client.exe              # Bundled OpenAI Secure MCP Tunnel client
├── core/                          # MCP server core engine, executor, jobs, config
├── memory/                        # Project memory subsystem documentation
├── scripts/                       # Bootstrap, health check, and supervisor
├── tests/                         # Test suite covering tools, launcher, supervisor
├── third_party/                   # Vendored upstream third-party licenses
└── tools/                         # MCP tool implementations (fs, edit, win, git, etc.)
```

---

## Third-Party Components

This repository redistributes precompiled binary software components under the Apache License 2.0:
- **OpenAI Secure MCP Tunnel Client** (`tunnel-client.exe`), Copyright 2026 OpenAI.
- **Cloudflare Tunnel Client** (`cloudflared.exe`), Copyright Cloudflare, Inc.

Full licensing details and upstream notices are available in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [BINARY_PROVENANCE.md](BINARY_PROVENANCE.md).

---

## License

This project is licensed under the **Apache License, Version 2.0**.
See [LICENSE](LICENSE) and [NOTICE](NOTICE) for details.

---

## Author

**Ali Khalili**  
GitHub: [https://github.com/dibbed/](https://github.com/dibbed/)
