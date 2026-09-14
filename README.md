# Windows Developer Agent MCP

A high-performance, full-access local Windows developer-agent backend powered by the Model Context Protocol (MCP Python SDK v2). The server provides atomic file operations, validated AST refactoring, durable background jobs, system diagnostics, and seamless integration with the OpenAI Secure MCP Tunnel.

---

## Current Status

The optimization roadmap is complete through **Phase D / v0.0.15**. The `main` branch also contains the final post-release D hardening for externally closed Playwright pages/contexts and expanded browser lifecycle benchmarks; the published `v0.0.15` tag remains immutable.

| Phase | Release | Focus | Status |
| --- | --- | --- | --- |
| A | `v0.0.12` | Measurement & Observability | ✅ Complete |
| B | `v0.0.13` | Core Performance | ✅ Complete |
| C | `v0.0.14` | Project Intelligence | ✅ Complete |
| D | `v0.0.15` | Runtime & Browser | ✅ Complete |

Phase A established structured timing and a repeatable benchmark baseline. Phase B added fast-start validation, bounded output/artifact reuse, keyed mutation locking, JobStore/query improvements, and single-flight/coalesced runtime work. Phase C added bounded version-aware Python metadata caching plus streaming and snapshot-based search pagination. Phase D completed shared Playwright browser pools, isolated session contexts, idle reclamation, crash/stale-session recovery, concurrency hardening, and browser lifecycle benchmarking.

The optional compact MCP tool surface considered during Phase D remains intentionally **disabled/not implemented**: the measured catalog serialization cost did not justify introducing another tool-profile mode without stronger host-level token/context evidence. The default full 59-tool surface therefore remains unchanged.

Current post-D validation on Windows: **203 pytest tests**, Ruff with zero violations, mypy with zero issues across 79 source files, compileall, the 59-tool health check, Full Doctor, real Chromium recovery tests, and a real mixed Chromium/Firefox pooling benchmark all pass. The next planned roadmap phase is **Phase E / v0.0.16 — Durable Jobs & Reliability**.

---

## Features

- **Robust Filesystem Tools**: Atomic file creation, writing, copying, moving, and deletion; paginated listing and regex file search; complete reads with streaming byte cursors.
- **Precision Code Editing**: Exact match, anchored replacements, and AST function/class body substitutions with syntax validation and rollback on syntax error.
- **Process & Command Execution**: Synchronous and background execution for PowerShell, CMD, and native Windows executables with streaming spooling (1 MiB RAM limit rolling over to temp disk), bounded auto-delivery, byte cursors, and reusable finalized artifacts.
- **Durable Job Store**: SQLite-backed background job queue (`.agent_state/jobs.sqlite3`) with idempotency keys, progress tracking, survivor workers across supervisor restarts, constant-query list/status paths, and one persistent SQLite connection per running worker.
- **Concurrency Hardening**: Keyed resource locks protect filesystem read-modify-write operations and project-memory updates; browser navigation is serialized only within the same session rather than across unrelated sessions.
- **System Diagnostics**: Live inspection of CPU, memory, disks, environment variables, installed applications, and Windows services.
- **Codebase Intelligence**: Fast Python AST parsing for classes, functions, imports, and project summaries.
- **Validation Suite**: Compact summaries from integrated `pytest`, `Ruff`, and `mypy` tools.
- **Git Integration**: Working-tree status, diff statistics, and commit log pagination.
- **Optional Browser Automation**: Playwright automation with shared browser-process pools, isolated per-session contexts, idle eviction, crash recovery, screenshot capture, and UI interaction.
- **Resilient Supervisor & Control Panel**: Heartbeat watchdog, automatic backoff recovery, and a loopback-only control panel at `http://127.0.0.1:8766`.

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
