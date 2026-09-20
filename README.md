# Windows Developer Agent MCP

A local Windows developer-agent backend built on the Model Context Protocol (MCP). It exposes filesystem, code intelligence, process execution, durable jobs, browser automation, desktop interaction, Git, testing, system diagnostics, project memory, and image delivery through one MCP server designed for long-running local use.

Current MCP release: **v0.2.0**. This project version is independent from the bundled upstream tunnel-client binary version.

The project can run through the OpenAI Secure MCP Tunnel or as a loopback-only Streamable HTTP server.

> Release history belongs in [CHANGELOG.md](CHANGELOG.md). Binary provenance and hashes are documented in [BINARY_PROVENANCE.md](BINARY_PROVENANCE.md).

## What This Project Provides

- Full local Windows filesystem access with atomic writes, backups, exact edits, anchored edits, and AST-aware Python refactoring.
- PowerShell, CMD, and native process execution with bounded output delivery and background process support.
- Durable SQLite-backed jobs that survive MCP runtime restarts and support idempotent submission, cancellation, output retrieval, and version-aware waiting.
- Python codebase intelligence plus explicit Language Server Protocol queries and transactional semantic edits.
- Fast file search with exact counting, bounded scans, and optional immutable snapshot pagination.
- Guarded Git inspection, branch creation, staging, committing, conflict discovery, and explicit file restoration.
- Integrated pytest, Ruff, and mypy execution with unified diagnostics, affected-test selection, change-aware verification, and durable incremental watches.
- Optional Playwright browser automation with shared browser pools and isolated sessions.
- Desktop screenshots and model-visible PNG/JPEG/WebP delivery.
- Windows system diagnostics for processes, CPU, memory, disks, services, installed programs, and environment metadata.
- Versioned project memory with provenance and optimistic concurrency checks.
- A supervised runtime with health monitoring, restart backoff, mutation-aware draining, recovery metadata, and a loopback control panel.
- Bounded local state for audit logs, backups, artifacts, job history, browser sessions, caches, and search snapshots.

## Architecture

The recommended launcher is `START_MCP.bat`. It creates or reuses the local Python environment, validates dependencies, runs startup checks when required, and starts the supervisor.

### Secure Tunnel Mode

This is the default mode.

```text
ChatGPT / MCP Client
        |
        | OpenAI Secure MCP Tunnel
        v
  tunnel-client.exe
        |
        | local stdio MCP transport
        v
   local_pc_mcp.py
        |
        v
 Windows Developer Agent MCP
```

The supervisor owns the tunnel runtime, monitors heartbeat and readiness, performs bounded restart recovery, and exposes the local control panel.

### Local HTTP Mode

Local HTTP mode bypasses the external tunnel and exposes MCP only on loopback:

```text
MCP Client
    |
    | http://127.0.0.1:8765/mcp
    v
Windows Developer Agent MCP
```

The Streamable HTTP server is stateless and binds to `127.0.0.1` by default.

## Requirements

- Windows 10 or Windows 11, x86-64 / amd64.
- Python 3.10 or newer.
- Windows PowerShell 5.1 or PowerShell 7+.
- Tunnel mode only: a valid control-plane API key and tunnel profile.
- Browser automation only: Playwright plus the required browser runtime.

The repository includes the Windows tunnel runtime binaries required by the default tunnel mode:

- `tunnel-client.exe`
- `cloudflared.exe`
- `cloudflared-manifest.json`

Keep these files together as a matched set. See [BINARY_PROVENANCE.md](BINARY_PROVENANCE.md) before replacing them.

## Quick Start

### 1. Prepare Tunnel Credentials

Skip this section if you only want local HTTP mode.

The recommended local secret location is:

```text
.secrets\control_plane_api_key.txt
```

Put only the control-plane API key in that file. `.secrets/` is ignored by Git.

You can alternatively use an environment variable:

```powershell
$env:CONTROL_PLANE_API_KEY = "your-api-key"
```

If you have more than one tunnel profile, select one explicitly:

```powershell
$env:MCP_TUNNEL_PROFILE = "your-profile"
```

When no profile is specified, the launcher attempts to detect a configured profile and otherwise falls back to `default`.

### 2. Start the MCP Runtime

From the repository root:

```cmd
START_MCP.bat
```

You can also double-click `START_MCP.bat` in Explorer.

The launcher performs the following startup flow:

```text
[1/5] Create or reuse .venv
[2/5] Verify Python version
[3/5] Install or validate dependencies
[4/5] Run fast validation or the full startup doctor
[5/5] Start the MCP supervisor
```

On later starts, a successful validation fingerprint allows unchanged environments to skip the expensive full doctor path.

Keep the launcher window open while using the service.

### 3. Open the Local Control Panel

When the supervisor is running:

```text
http://127.0.0.1:8766/
```

The panel provides runtime health, process information, logs, and Restart / Stop controls.

### 4. Stop the Runtime

Use either:

- `Ctrl+C` in the launcher window, or
- **Stop** in the local control panel.

The supervisor attempts a bounded mutation drain before stopping the managed runtime.

## Local HTTP Mode

To run without the Secure MCP Tunnel:

```powershell
$env:MCP_START_MODE = "local-http"
.\START_MCP.bat
```

The MCP endpoint becomes available at:

```text
http://127.0.0.1:8765/mcp
```

To return to tunnel mode in the same shell:

```powershell
Remove-Item Env:MCP_START_MODE -ErrorAction SilentlyContinue
```

For direct development use, after the virtual environment exists you can also run:

```powershell
.\.venv\Scripts\python.exe main.py --transport streamable-http --host 127.0.0.1 --port 8765 --path /mcp
```

The supervised launcher remains the recommended normal runtime because it adds health monitoring and restart handling.

## Browser Automation Setup

Browser automation is optional. The core server does not require Playwright.

Install the browser dependency set:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-browser.txt
```

Install Chromium for Playwright:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

Validate browser startup:

```powershell
.\.venv\Scripts\python.exe -m scripts.doctor --mode local-http --browser
```

Browser sessions use isolated `BrowserContext` instances and can share compatible browser processes. Session and pool counts are bounded and idle resources are reclaimed automatically.

## Configuration

Configuration is read from environment variables. `.env.example` documents the full supported set and default values.

Common settings:

| Variable | Purpose | Default |
| --- | --- | --- |
| `MCP_START_MODE` | Launcher mode: `tunnel` or `local-http` | `tunnel` |
| `MCP_TUNNEL_PROFILE` | Secure Tunnel profile | auto-detected / `default` |
| `MCP_TOOL_PROFILE` | Tool catalog profile (`minimal`, `coding`, `git`, `testing`, `desktop`, `browser`, `operations`, or `full`) | `full` |
| `CONTROL_PLANE_API_KEY` | Tunnel control-plane credential | none |
| `MCP_MAX_RUNNING_JOBS` | Maximum simultaneously active durable jobs | `4` |
| `MCP_BROWSER_IDLE_SEC` | Idle browser-session lifetime | `900` |
| `MCP_BROWSER_POOL_IDLE_SEC` | Empty browser-pool lifetime | `120` |
| `MCP_BROWSER_MAX_SESSIONS` | Maximum logical browser sessions | `20` |
| `MCP_BROWSER_MAX_POOLS` | Maximum shared browser pools | `6` |
| `MCP_VISION_MAX_BYTES` | Maximum original image payload before bounded preview | `8388608` |
| `MCP_VISION_JPEG_QUALITY` | JPEG quality used for large image previews | `88` |
| `MCP_SUPERVISOR_DRAIN_SEC` | Normal restart / stop mutation-drain deadline | `15` |
| `MCP_SUPERVISOR_WATCHDOG_DRAIN_SEC` | Watchdog best-effort drain deadline | `2` |
| `MCP_WINDOWS_JOB_OBJECTS` | Windows Job Object process ownership | enabled |

Resource retention, cache, search-snapshot, audit, backup, artifact, and job-history limits are also configurable. See [.env.example](.env.example) rather than duplicating every tuning value here.

## Tool Capabilities

The MCP catalog is grouped around development workflows rather than one large generic command tool.

`full` remains the backward-compatible default catalog. Smaller profiles reduce tool-selection ambiguity for specialized agents. The always-available `discover_tool_domains` tool reports the active profile and `recommend_tools` ranks only tools registered in that profile, including concise guidance for overlapping editing, process, testing, and desktop actions.

### Filesystem and Editing

- Read files with bounded windows and delivery controls.
- Create, copy, move, and delete files.
- Atomic whole-file writes with optional SHA-256 preconditions.
- Exact replacement and uniquely anchored replacement.
- Python function and class body replacement through AST-aware editing.
- Safe multi-edit refactoring with validation and rollback.
- Recoverable backups for protected edits.

Filesystem read-modify-write operations use keyed path locks inside the runtime. For edits that may race with external programs, use `expected_sha256` where supported.

### Project and Code Intelligence

- Project summaries.
- Python function and class discovery.
- Import discovery.
- Dependency graph generation.
- Cached per-file Python metadata with bounded LRU storage.
- Fast file and content search with pagination.
- Optional search snapshots for stable multi-page traversal.
- Language Server Protocol discovery for definitions, references, symbols, hover, call hierarchy, and diagnostics.
- Transactional symbol rename and code-action edits with workspace boundaries, overlap checks, hash preconditions, and rollback.

Language-server tools accept an explicit server command; the MCP does not silently install or select a language server. Workspace edits support `changes` and versioned text edits in `documentChanges`. LSP create, rename, and delete resource operations are rejected.

### Processes and Terminal Commands

- Run native executables.
- Run PowerShell and CMD commands.
- Start and monitor background processes.
- Read incremental process output.
- Terminate process trees.

On supported Windows hosts, runtime-owned command trees use Windows Job Objects for deterministic cleanup. An emergency compatibility fallback can be selected with `MCP_WINDOWS_JOB_OBJECTS=0`.

### Durable Jobs

Long-running commands can be submitted to the durable job subsystem instead of being tied to one MCP request.

Durable jobs provide:

- SQLite-backed persistent state.
- Idempotency keys.
- Bounded admission control.
- Independent worker processes.
- Restart survival.
- Queue and execution timeout handling.
- Cancellation.
- Incremental output retrieval.
- Monotonic job versions.
- `job_wait` for bounded version-aware waiting.

Queued, running, and orphaned jobs are not removed by terminal-history retention.

### Browser Automation

The optional Playwright toolset supports:

- Chromium, Firefox, and WebKit session creation when their runtimes are installed.
- Navigation and page interaction.
- Element filling and clicking.
- Screenshot capture.
- Isolated session contexts.
- Shared compatible browser processes.
- Idle cleanup and stale-session recovery.

A slow operation in one browser session does not globally serialize unrelated sessions.

### Images and Screenshots

The server supports model-visible PNG, JPEG, and WebP images.

Typical browser screenshot flow:

```text
browser_screenshot
      |
      v
screenshot file path
      |
      v
read_file(path, delivery="auto") or view_image(path)
      |
      v
MCP image content / model vision
```

Desktop screenshots can also be captured from the Windows desktop. Oversized `auto` image deliveries are converted to bounded JPEG previews while the original file remains on disk.

### Git and Testing

Git tools provide status, bounded patches and commit inspection, blame, merge-base lookup, changed-file and conflict discovery, and branch metadata. Guarded mutation tools can create a branch, stage explicit paths, commit the current index, and restore explicit files from an explicit revision. They do not expose force, reset, clean, or push operations, and pathspecs cannot be interpreted as command options.

Testing tools integrate:

- pytest
- Ruff
- mypy
- affected-test selection from changed paths or a Git base
- one-call change-aware verification (`verify_changes`)
- merged diagnostics from selected Ruff, mypy, and pytest backends (`collect_diagnostics`)
- durable incremental verification watches (`start_validation_watch`)

They return structured counts and bounded diagnostic samples suitable for agent workflows. `verify_changes` syntax-checks changed
Python files, then runs Ruff, mypy, and the narrowest sound pytest scope. Repository-wide configuration and shared-fixture changes
fall back to the full suite. A timed-out or unavailable required verifier always makes the aggregate result fail; Ruff fixes are opt-in.

`collect_diagnostics` emits one stable schema with `file`, range, severity, code, message, and source fields, plus per-backend status and a `required_unknown` flag. `start_validation_watch` submits a persistent job that snapshots the repository, debounces bursts, verifies settled changes, and writes one JSONL record per generation. Use the normal `job_status`, `job_output`, `job_wait`, and `cancel_job` tools to manage its lifecycle.

### Windows and System Diagnostics

The server can inspect:

- CPU utilization.
- Virtual memory and swap.
- Mounted volumes.
- Processes.
- Windows services.
- Installed programs.
- Environment variable names, with values opt-in.
- Foreground-window metadata.

## Output Delivery

Large command output can be delivered without forcing the full payload into one MCP response.

Supported delivery modes include inline, file-backed, and automatic bounded delivery depending on the tool.

To make compatible output tools prefer automatic bounded delivery:

```powershell
$env:MCP_OUTPUT_DEFAULT = "auto"
```

Automatic delivery keeps small responses inline and uses bounded previews, cursors, or disk-backed artifacts for larger output. The full captured bytes remain available.

Related optional controls include:

```text
MCP_INLINE_SOFT_LIMIT_BYTES
MCP_INLINE_HARD_LIMIT_BYTES
MCP_PREVIEW_BYTES
```

## Runtime Supervision and Recovery

The supervisor is responsible for keeping the managed MCP runtime healthy without blindly replaying mutations.

Key behaviors:

- Heartbeat and readiness monitoring.
- Restart backoff for unstable runtimes.
- Mutation-aware `RUNNING -> DRAINING -> STOPPING` lifecycle.
- Bounded drain deadlines for restart and watchdog recovery.
- Best-effort process-tree cleanup.
- Resilient cleanup if Windows process-table inspection temporarily fails.
- Runtime-generation-specific lifecycle control files.
- Recovery metadata for mutations whose final result is unknown after a crash.

If a mutation began but the runtime disappeared before a known completion result was recorded, the operation is marked `uncertain`. It is not automatically replayed. This avoids duplicate side effects.

`server_health` exposes the current runtime state, resource pressure, job counts, browser counts, storage usage, configured resource budgets, and operation-recovery summary.

## Local Control Panel

The supervisor panel is available at:

```text
http://127.0.0.1:8766/
```

It is intended for local operator use only.

Security controls include:

- loopback-only binding,
- Host validation,
- Origin validation for mutations,
- a per-session control token,
- credential redaction in supervisor logs.

The panel can show runtime state and request controlled Restart or Stop operations.

## Local State

Generated runtime data is stored under `.agent_state/` and is excluded from Git.

Important paths include:

| Path | Purpose |
| --- | --- |
| `.agent_state/jobs.sqlite3` | Durable job state |
| `.agent_state/jobs/` | Durable job stdout / stderr files |
| `.agent_state/artifacts/` | File-backed output artifacts |
| `.agent_state/backups/` | Recoverable edit backups |
| `.agent_state/screenshots/` | Browser and desktop screenshots |
| `.agent_state/search_snapshots/` | Immutable search pagination snapshots |
| `.agent_state/audit.jsonl` | Metadata-only audit trail |
| `.agent_state/operation-recovery.jsonl` | Mutation recovery metadata |
| `.agent_state/runtime_lifecycle/` | Supervisor / runtime generation control state |
| `.agent_state/supervisor.log*` | Rotating supervisor logs |

These stores are bounded by age, count, byte, TTL, or runtime limits depending on the subsystem.

## Project Memory

Project memory is stored under `memory/` and uses versioned structured records with provenance information and optimistic revisions.

The memory subsystem is intentionally bounded. It is designed for compact project facts such as architecture decisions, important paths, user preferences, and previous fixes rather than raw conversation transcripts.

Cross-process updates use revision checks so two writers starting from the same revision cannot silently overwrite each other.

## Health Checks and Diagnostics

### Startup Check

```powershell
.\.venv\Scripts\python.exe main.py --check
```

This validates server construction and tool registration.

### MCP Health Smoke Test

```powershell
.\.venv\Scripts\python.exe -m scripts.health_check --json
```

This verifies imports, MCP startup, tool registration, filesystem behavior, and terminal execution.

### Full Doctor

Tunnel mode:

```powershell
.\.venv\Scripts\python.exe -m scripts.doctor --mode tunnel --profile your-profile
```

Local mode:

```powershell
.\.venv\Scripts\python.exe -m scripts.doctor --mode local-http
```

Include a real disposable Chromium launch:

```powershell
.\.venv\Scripts\python.exe -m scripts.doctor --mode local-http --browser
```

The doctor validates dependency imports, `pip check`, MCP smoke checks, search backend diagnostics, optional browser startup, and Secure Tunnel diagnostics when tunnel mode is selected.

## Testing

Run the full automated suite:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Lint:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
```

Type check:

```powershell
.\.venv\Scripts\python.exe -m mypy core tools scripts tests
```

Compile Python sources:

```powershell
.\.venv\Scripts\python.exe -m compileall -q core tools scripts tests main.py local_pc_mcp.py
```

### Performance Benchmarks

A repeatable benchmark harness is included for startup, output delivery, project analysis, search, jobs, browser workloads, and long-running storage behavior.

Quick profile:

```powershell
.\.venv\Scripts\python.exe -m scripts.perf_benchmark --profile quick
```

Include durable jobs:

```powershell
.\.venv\Scripts\python.exe -m scripts.perf_benchmark --profile quick --include-jobs
```

Include browser workloads:

```powershell
.\.venv\Scripts\python.exe -m scripts.perf_benchmark --profile quick --include-browser
```

Full profile:

```powershell
.\.venv\Scripts\python.exe -m scripts.perf_benchmark --profile full --include-jobs --include-browser
```

Use repeated runs such as `--runs 5` when comparing changes.

### Optional Timing Instrumentation

Structured timing is opt-in:

```powershell
$env:MCP_TIMINGS = "1"
.\START_MCP.bat
```

Timing records are written under local ignored state and contain metadata and durations rather than command bodies, file contents, or typed browser text.

## Security Model

This project intentionally has broad local capabilities. Treat access to the MCP endpoint as equivalent to granting a development agent access to the Windows account running it.

Important safeguards:

- Internal HTTP services bind to loopback by default.
- Secrets are kept outside version control.
- `CONTROL_PLANE_API_KEY` is redacted from supervisor output where applicable.
- Audit records contain metadata rather than file bodies or secret command payloads.
- Destructive audit events use durable writes.
- Runtime mutations are not automatically replayed after ambiguous crashes.
- Local generated state is isolated under `.agent_state/`.
- Browser session counts, job concurrency, output artifacts, backups, logs, and caches have bounded policies.

Do not expose the local HTTP endpoint or control panel directly to an untrusted network.

## Troubleshooting

### The launcher runs the full doctor every time

The startup fingerprint is invalidated when relevant source files, Python/runtime inputs, requirements, installed package metadata, tunnel configuration, or selected environment variables change. A successful full doctor writes a new local validation fingerprint.

### `CONTROL_PLANE_API_KEY` is unavailable

For tunnel mode, either create:

```text
.secrets\control_plane_api_key.txt
```

or set:

```powershell
$env:CONTROL_PLANE_API_KEY = "your-api-key"
```

Local HTTP mode does not require the tunnel credential.

### Port `8080` is already in use

Tunnel mode uses the tunnel runtime's local admin/readiness service on port `8080`. First make sure another copy of `START_MCP.bat` or `tunnel-client.exe` is not already running.

Inspect the listener:

```powershell
Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
```

Then inspect the owning PID before terminating anything:

```powershell
Get-Process -Id <PID>
```

The supervisor includes fallback root-process cleanup so a temporary Windows process-enumeration failure does not intentionally leave its managed runtime behind.

### `WinError 1455: The paging file is too small`

This error can be raised by Windows while process-table APIs are under temporary commit/pagefile pressure. The supervisor treats process inspection as best-effort and keeps root-process cleanup independent from recursive `psutil` enumeration.

If Windows continues producing `1455` outside this project, check system memory/commit pressure and the configured Windows paging file.

### Browser tools are installed but Chromium does not launch

Run:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m scripts.doctor --mode local-http --browser
```

### A newly added MCP tool does not appear in the client

Some MCP clients cache the tool catalog for the lifetime of a connection. Restart or reconnect the MCP client after the server tool schema changes.

### A mutation appears under `operation_recovery.uncertain`

An uncertain record means the runtime ended after the mutation started but before a known final result was durably recorded. Inspect the target state before taking action. The supervisor deliberately does not replay uncertain mutations automatically.

## Project Structure

```text
.
├── START_MCP.bat                 # Recommended Windows launcher
├── main.py                       # MCP server entrypoint
├── local_pc_mcp.py               # stdio entrypoint used by the tunnel runtime
├── requirements.txt              # Core Python dependencies
├── requirements-browser.txt      # Optional Playwright dependencies
├── pyproject.toml                # pytest, Ruff, and mypy configuration
├── .env.example                  # Environment configuration reference
├── core/                         # Runtime, config, jobs, audit, lifecycle, output policies
├── tools/                        # MCP tool implementations
├── scripts/                      # Bootstrap, supervisor, doctor, health, benchmarks
├── memory/                       # Bounded project-memory data and documentation
├── tests/                        # Unit and integration tests
├── third_party/                  # Bundled third-party notices and licenses
├── tunnel-client.exe             # Secure MCP Tunnel runtime
├── cloudflared.exe               # Tunnel transport companion
├── BINARY_PROVENANCE.md          # Runtime binary provenance and hashes
├── CHANGELOG.md                  # Release history
├── CONTRIBUTING.md               # Contribution guide
├── SECURITY.md                   # Security reporting guidance
├── LICENSE                       # Apache License 2.0
└── THIRD_PARTY_NOTICES.md        # Third-party attribution
```

## Updating Bundled Tunnel Binaries

`tunnel-client.exe`, `cloudflared.exe`, and `cloudflared-manifest.json` must be treated as a matched runtime set.

When updating them:

1. Use only an official upstream release.
2. Download the correct Windows architecture.
3. Verify the release artifact hash before replacement.
4. Replace the matched runtime files together.
5. Recompute local SHA-256 hashes.
6. Update [BINARY_PROVENANCE.md](BINARY_PROVENANCE.md).
7. Update third-party notices if upstream licensing changed.
8. Run pytest, Ruff, mypy, compileall, health checks, and the full doctor.
9. Validate both local HTTP and tunnel startup before merging the change.

Do not replace only one bundled executable unless upstream documentation explicitly states that the combination is compatible.

## Development Workflow

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution details.

For normal repository changes:

1. Create a focused branch.
2. Make the smallest coherent change.
3. Run the relevant focused tests.
4. Run repository validation before merge.
5. Push the branch and open a pull request.
6. Require CI to pass.
7. Merge through the pull request.
8. Delete the temporary branch.

## Release History

See [CHANGELOG.md](CHANGELOG.md) for release notes and historical implementation details.

The README intentionally documents the current project and how to operate it rather than duplicating per-version development history.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

Third-party runtime attribution is available in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Author

**Ali Khalili**

GitHub: [github.com/dibbed](https://github.com/dibbed/)
