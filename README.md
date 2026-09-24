# ComputerPilot MCP

**Cross-platform computer use and developer automation over MCP.**

[![Release](https://img.shields.io/github/v/release/dibbed/computerpilot-mcp?label=release)](https://github.com/dibbed/computerpilot-mcp/releases/latest)
[![CI](https://github.com/dibbed/computerpilot-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/dibbed/computerpilot-mcp/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/dibbed/computerpilot-mcp?style=flat)](https://github.com/dibbed/computerpilot-mcp/stargazers)

ComputerPilot MCP gives MCP-capable AI agents one supervised local runtime for **computer use**, **developer automation**, browser control, filesystem/code intelligence, terminal and process execution, Git, testing, durable background jobs, recovery, and workflow orchestration.

Windows adds native screenshots, desktop input, and semantic UI Automation. Linux and macOS share the portable filesystem, terminal, browser, Git, testing, jobs, recovery, and workflow core.

Current MCP release: **v0.3.0 Cross-Platform Preview**. The project was renamed from `windows-agent-mcp` to `computerpilot-mcp`. The backward-compatible MCP server identifier `ali_windows_agent_mcp` intentionally remains unchanged so existing tunnel/client configurations continue to work.

The project can run through the OpenAI Secure MCP Tunnel or as a loopback-only Streamable HTTP server.

> Release history belongs in [CHANGELOG.md](CHANGELOG.md). v0.3.0 release evidence is recorded in [docs/V0.3.0-VALIDATION.md](docs/V0.3.0-VALIDATION.md). Binary provenance and managed-runtime rules are documented in [BINARY_PROVENANCE.md](BINARY_PROVENANCE.md).

## Why ComputerPilot MCP

- **Computer use + desktop automation** — native Windows screenshot/input/UI Automation, plus portable browser automation.
- **Developer automation** — files, search, code intelligence, terminal/process execution, Git, testing, diagnostics, and project memory.
- **Reliable agent runtime** — durable background jobs, recoverable operations, workflows, supervised restarts, mutation-aware drains, and health reporting.
- **Cross-platform by design** — Windows amd64 is fully supported, with Linux/macOS preview targets and architecture-aware release packaging.
- **Secure remote access without vendored tunnel binaries** — verified managed OpenAI Secure MCP Tunnel bootstrap with checksum and version validation.

## Quick Start at a Glance

Windows:

```cmd
START_MCP.bat
```

Linux/macOS:

```sh
./start_mcp.sh
```

Local HTTP mode is available at `http://127.0.0.1:8765/mcp`; the supervised control panel runs on `http://127.0.0.1:8766/`.

## Platform Support

| Platform | Status | Native CI in v0.3.0 | Launcher | Native desktop/UIA |
| --- | --- | --- | --- | --- |
| Windows amd64 | Supported | Python 3.10 + 3.12 | `START_MCP.bat` | Supported |
| Linux amd64 | Preview | Python 3.10 + 3.12 | `start_mcp.sh` | Not exposed |
| macOS arm64 | Preview | Python 3.10 + 3.12 | `start_mcp.sh` | Not exposed |
| Linux arm64 | Preview package target | Packaging/updater mapping | `start_mcp.sh` | Not exposed |
| macOS amd64 | Preview package target | Packaging/updater mapping | `start_mcp.sh` | Not exposed |

The Python core is architecture-neutral. The Secure Tunnel updater selects the official upstream asset for the detected OS/architecture. Linux arm64 and macOS amd64 packages are published from the same source/runtime contract, but v0.3.0 does not claim a dedicated native hosted-runner execution for those two architecture combinations.

Windows native desktop screenshot/input and semantic UI Automation are capability-gated and are not registered on Linux/macOS. Browser automation is the portable UI path when Playwright is installed.

## What This Project Provides

- Cross-platform filesystem, editing, search, code intelligence, Git, testing, durable jobs/workflows, recovery, and project memory.
- Native process execution, Windows CMD, PowerShell where installed, and explicit POSIX `sh` on Linux/macOS.
- Deterministic process-tree ownership through Windows Job Objects or dedicated POSIX sessions/process groups.
- Portable CPU/memory/disk/environment diagnostics with OS-specific service and installed-software backends.
- Optional Playwright browser automation with bounded shared pools and isolated sessions.
- Windows-only native desktop screenshot/input and semantic UI Automation, registered only when supported.
- Supervised runtime health, restart backoff, mutation-aware drain, loopback control panel, and bounded local state.
- Verified managed OpenAI Secure Tunnel bootstrap/update with automatic OS/architecture selection, checksum/version validation, managed-cache fallback, and bounded retry backoff.

## Architecture

Recommended launchers:

- Windows: `START_MCP.bat`
- Linux/macOS: `./start_mcp.sh`

Both create/reuse `.venv`, validate dependencies, run cached/full startup checks, resolve the tunnel runtime when needed, and start the Python supervisor.

### Secure Tunnel Mode

```text
ChatGPT / MCP Client
        |
        | OpenAI Secure MCP Tunnel
        v
 managed tunnel-client
        |
        | local stdio MCP transport
        v
   local_pc_mcp.py
        |
        v
 ComputerPilot MCP
```

### Local HTTP Mode

```text
MCP Client
    |
    | http://127.0.0.1:8765/mcp
    v
ComputerPilot MCP
```

The HTTP server is stateless and loopback-only by default.

## Requirements

- Python 3.10 or newer.
- Windows 10/11 amd64, a supported Linux environment, or macOS.
- POSIX hosts require `sh`.
- Tunnel mode requires a control-plane API key and tunnel profile.
- Browser automation requires Playwright plus the desired browser runtime.
- Semantic desktop UI Automation is Windows-only.

The repository does **not** vendor tunnel-client or Cloudflared binaries. On the first tunnel-mode startup, the launcher detects the host OS and architecture, resolves the latest non-prerelease `openai/tunnel-client` release, selects the matching official `tunnel-client-runtime-cloudflared` archive, verifies both GitHub SHA-256 metadata and upstream `SHA256SUMS.txt`, validates the binary version, and installs an immutable managed runtime under `.agent_state/tunnel-runtime/`.

All project release archives are source/runtime-controller packages only. After the first verified download, the managed cache is reused for offline startup and periodically checked for newer upstream releases.

## Release Artifacts

v0.3.0 packaging produces:

- `computerpilot-mcp-v0.3.0-windows-amd64.zip`
- `computerpilot-mcp-v0.3.0-linux-amd64.tar.gz`
- `computerpilot-mcp-v0.3.0-linux-arm64.tar.gz`
- `computerpilot-mcp-v0.3.0-macos-amd64.tar.gz`
- `computerpilot-mcp-v0.3.0-macos-arm64.tar.gz`
- `SHA256SUMS.txt`

Artifacts are deterministically generated from tracked Git blobs. Packaging fails if runtime/secrets/build-state paths are tracked, embeds `RELEASE-MANIFEST.json`, and preserves executable mode for `start_mcp.sh`.

## Quick Start

### 1. Tunnel credentials

Skip for local HTTP mode. Recommended secret path on every platform:

```text
.secrets/control_plane_api_key.txt
```

PowerShell:

```powershell
$env:CONTROL_PLANE_API_KEY = "your-api-key"
$env:MCP_TUNNEL_PROFILE = "your-profile"
```

POSIX:

```sh
export CONTROL_PLANE_API_KEY="your-api-key"
export MCP_TUNNEL_PROFILE="your-profile"
```

### 2. Start

Windows:

```cmd
START_MCP.bat
```

Linux/macOS:

```sh
./start_mcp.sh
```

Shared startup flow:

```text
[1/5] Create or reuse .venv
[2/5] Verify Python version and resolve Secure Tunnel runtime
[3/5] Install or validate dependencies
[4/5] Run cached validation or full startup doctor
[5/5] Start MCP supervisor
```

Use `MCP_TUNNEL_VERSION` to pin an upstream tunnel release, `MCP_TUNNEL_AUTO_UPDATE=0` to disable release checks, or `MCP_TUNNEL_UPDATE_REQUIRED=1` for fail-closed freshness.

### 3. Control panel

```text
http://127.0.0.1:8766/
```

The panel is loopback-only and reports runtime lifecycle, health, processes, durable jobs, local storage, structured events/errors, and guarded restart/stop controls.

## Local HTTP Mode

Windows:

```powershell
$env:MCP_START_MODE = "local-http"
.\START_MCP.bat
```

Linux/macOS:

```sh
MCP_START_MODE=local-http ./start_mcp.sh
```

Endpoint:

```text
http://127.0.0.1:8765/mcp
```

Direct development:

```sh
python -m main --transport streamable-http --host 127.0.0.1 --port 8765 --path /mcp
```

## Browser Automation Setup

```sh
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
python -m scripts.doctor --mode local-http --browser
```

## Configuration

Configuration is read from environment variables. `.env.example` documents the full supported set and default values.

Common settings:

| Variable | Purpose | Default |
| --- | --- | --- |
| `MCP_START_MODE` | Launcher mode: `tunnel` or `local-http` | `tunnel` |
| `MCP_TUNNEL_PROFILE` | Secure Tunnel profile | auto-detected / `default` |
| `MCP_TUNNEL_AUTO_UPDATE` | Check official tunnel-client releases during startup | enabled |
| `MCP_TUNNEL_UPDATE_INTERVAL_HOURS` | Minimum time between latest-release checks | `24` |
| `MCP_TUNNEL_VERSION` | Optional exact upstream release pin | latest stable |
| `MCP_TUNNEL_UPDATE_REQUIRED` | Fail startup if a requested update cannot be verified | disabled |
| `MCP_TOOL_PROFILE` | Tool catalog profile (`minimal`, `coding`, `git`, `testing`, `desktop`, `browser`, `operations`, or `full`) | `full` |
| `CONTROL_PLANE_API_KEY` | Tunnel control-plane credential | none |
| `MCP_MAX_RUNNING_JOBS` | Maximum simultaneously active durable jobs | `4` |
| `MCP_WORKFLOW_HISTORY_MAX_COUNT` | Maximum retained safe terminal workflows | `1000` |
| `MCP_WORKFLOW_HISTORY_MAX_AGE_DAYS` | Maximum age for safe terminal workflow history | `30` |
| `MCP_WORKFLOW_DB_WARN_BYTES` | Workflow DB size that degrades health | `268435456` |
| `MCP_WORKFLOW_CLEANUP_INTERVAL_SEC` | Minimum workflow-history cleanup interval | `300` |
| `MCP_BROWSER_IDLE_SEC` | Idle browser-session lifetime | `900` |
| `MCP_BROWSER_POOL_IDLE_SEC` | Empty browser-pool lifetime | `120` |
| `MCP_BROWSER_MAX_SESSIONS` | Maximum logical browser sessions | `20` |
| `MCP_BROWSER_MAX_POOLS` | Maximum shared browser pools | `6` |
| `MCP_VISION_MAX_BYTES` | Maximum original image payload before bounded preview | `8388608` |
| `MCP_VISION_JPEG_QUALITY` | JPEG quality used for large image previews | `88` |
| `MCP_SUPERVISOR_DRAIN_SEC` | Normal restart / stop mutation-drain deadline | `15` |
| `MCP_SUPERVISOR_WATCHDOG_DRAIN_SEC` | Watchdog best-effort drain deadline | `2` |
| `MCP_WINDOWS_JOB_OBJECTS` | Windows Job Object process ownership | enabled |

Resource retention, cache, search-snapshot, audit, backup, artifact, job-history, and workflow-history limits are also configurable. See [.env.example](.env.example) rather than duplicating every tuning value here.

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

Language-server MCP tools do not accept caller-supplied executable commands. They use the trusted discovered `pyright-langserver --stdio` command, which must already be installed and available on `PATH`; the MCP never installs it implicitly. One-shot requests follow `initialize -> initialized -> optional didOpen -> request -> didClose -> shutdown -> exit`, enforce bounded framed output before body accumulation, and keep document URIs inside the workspace. Workspace edits support `changes` and versioned text edits in `documentChanges`; resource operations are rejected, and externally supplied code-action edits require source SHA-256 guards for every edited file.

### Processes and Terminal Commands

- Run native executables without implicit shell expansion.
- Run CMD on Windows.
- Run PowerShell where `powershell` / `pwsh` is installed.
- Run explicit POSIX `sh -c` commands on Linux/macOS.
- Start/monitor background processes, read incremental output, and terminate owned process trees.

Windows uses Job Objects for descendant cleanup. Linux/macOS use dedicated POSIX sessions/process groups with bounded TERM/KILL escalation. `MCP_WINDOWS_JOB_OBJECTS=0` remains an emergency Windows-only compatibility fallback.

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
- `job_wait` for bounded version-aware waiting with a 5-second progress heartbeat, bounded incremental stdout/stderr chunks, and resumable byte cursors.

Queued, running, and orphaned jobs are not removed by terminal-history retention. Durable-job `idempotency_key` values are execution identities, not credentials; callers must never place secrets in them.

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

### Semantic Windows UI Automation

For standard Windows applications, prefer semantic UI Automation over coordinates:

- `ui_list_windows` scopes work by title, PID, or native handle.
- `ui_find_elements` and `ui_get_element` locate controls by name, Automation ID, control type, or class.
- `ui_invoke`, `ui_set_value`, and `ui_select` re-resolve the locator immediately before mutation and require exactly one match.
- `ui_wait_for_element` provides a bounded wait for dynamic interfaces.

Traversal is breadth-first with explicit depth/node limits. Duplicate matches, stale elements, missing UIA patterns, and timeouts fail closed. Audit events contain locator metadata and character counts, never the value typed into a control. Coordinate tools remain available as a fallback for canvas, game, remote-desktop, or otherwise non-UIA surfaces.

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

### System Diagnostics

The server can inspect:

- CPU utilization.
- Virtual memory and swap.
- Mounted volumes.
- Processes.
- System services through Windows SCM, Linux systemd/SysV detection, or macOS launchd.
- Installed software through Windows Registry, Linux package-manager backends, or macOS application/system metadata.
- Environment variable names, with values opt-in.
- Foreground-window metadata on supported Windows desktop hosts.

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

Recovery tools provide a complete, paginated view instead of the five-item health summary:

- `list_uncertain_operations` and `inspect_uncertain_operation`
- `get_operation_history`
- `reconcile_operation` using `file_exists`, `file_absent`, `file_sha256`, `git_head`, or `process_identity`
- `acknowledge_uncertain_operation` with explicit operator evidence

A process match requires PID, creation time, and executable identity so PID reuse cannot produce false evidence. Conclusive postconditions may resolve an operation as succeeded or failed; inconclusive checks do not change durable state. Legacy schema-v1 records remain readable and are migrated incrementally through appended evidence events.

The eight legacy uncertain records observed before this release remain intact for operator review: six were `run_process`, one was `run_pytest`, and one was `delete_file`. Five involved Python/backend work or long-running services and three invoked `gh`; they are historical ambiguity, not proof of plugin failure. No record is replayed automatically.

## Durable Workflow Orchestration

Workflow definitions are immutable ordered steps stored in SQLite. Use `workflow_plan` and `workflow_start` to validate and queue work, `workflow_execute` to claim and run it, and `workflow_status` plus `workflow_operations` to inspect aggregate and side-effect state. The standalone worker uses the same executor API:

```powershell
.\.venv\Scripts\python.exe -m scripts.workflow_worker --once
```

Executable actions are defined by a typed allowlist covering verification, patching, affected-test selection, guarded Git actions, durable jobs, and file/HTTP checks. Browser and desktop workflow actions remain unavailable until a concrete executor adapter exists and therefore fail fast during planning/start. Arbitrary shell text is rejected. Mutating steps require supported intent postconditions; exact execution definitions are persisted separately from bounded/redacted public projections, literal declared secret fields fail closed, transitions use optimistic version guards, and retries are bounded to explicitly transient failures. Workflow idempotency also binds exact new-workflow inputs, so reusing a key with different inputs fails instead of silently returning an older request.

Each step materializes a durable operation with its own version and idempotency key. Executors claim a fenced lease, renew it while the action is running, and persist canonical recovery intent before supported mutations. Cancellation is persistent and cooperative: a running workflow enters `cancelling`, cancellable durable jobs receive the request, and a side effect that already completed is recorded before the aggregate becomes `cancelled`. Interrupted non-terminal effects become `uncertain` and are never replayed blindly. Result-independent reconciliation uses persisted identities such as patch output hashes, Git parent/tree/message evidence, or durable-job request keys. If automated evidence remains inconclusive, `workflow_acknowledge_operation` records an explicit operator assertion with actor and reason. Crash recovery also repairs aggregates from already-persisted terminal operation truth instead of converting a known succeeded/failed operation back into ambiguity.

State ownership is intentionally separated: the workflow database owns definitions and aggregate/operation lifecycle; the job store owns process completion and output; the recovery journal owns legacy standalone mutation ambiguity; project memory is advisory and is never execution authority.

Built-in plans are `implement_and_verify`, `safe_git_commit`, and `prepare_release`. They do not push, tag, or deploy. `deploy_and_healthcheck` requires an explicit project adapter and otherwise fails closed.

`server_health` exposes current runtime state, resource pressure, job and browser counts, storage budgets, the legacy operation-recovery summary, and bounded workflow/operation/event/lease health. Core workflow counts and pressure metrics are available both at top level and in `workflow_health`. `ok` remains the backward-compatible server-responsiveness flag; `health_status` and `degraded_reasons` separately expose unresolved standalone recovery-journal mutations, unresolved workflow operations, workflow DB pressure, or broader resource pressure.

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
| `.agent_state/workflows.sqlite3` | Exact/public workflow definitions, cancellation state, operation intent/evidence, leases, events, and retained history |
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

### Workflow Release Soak

```powershell
.\.venv\Scripts\python.exe -m scripts.workflow_soak --workflows 300 --failures 30 --uncertain 10 --restarts 3 --durable-jobs 5 --json
```

This deterministic release gate exercises hundreds of durable workflows, expected failures, interrupted-operation reconciliation, fresh-process database reopens, isolated MCP restart/startup checks, retention cleanup, durable jobs, lease/orphan leak checks, main DB/WAL/SHM growth, and RSS growth.

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

This project intentionally has broad local capabilities. Treat access to the MCP endpoint as equivalent to granting a development agent access to the local OS account running it.

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
├── START_MCP.bat                 # Windows launcher
├── start_mcp.sh                  # Linux/macOS launcher
├── main.py                       # MCP server entrypoint
├── local_pc_mcp.py               # stdio entrypoint used by the tunnel runtime
├── requirements.txt              # Runtime + quality dependency aggregate
├── requirements-runtime.txt      # Runtime dependencies
├── requirements-quality.txt      # pytest/Ruff/mypy validation dependencies
├── requirements-dev.txt          # Development aggregate
├── requirements-browser.txt      # Optional Playwright dependencies
├── pyproject.toml                # pytest, Ruff, and mypy configuration
├── .env.example                  # Environment configuration reference
├── core/                         # Runtime, config, jobs, audit, lifecycle, output policies
├── tools/                        # MCP tool implementations
├── scripts/                      # Bootstrap, supervisor, doctor, validation, packaging, benchmarks
├── memory/                       # Bounded project-memory data and documentation
├── tests/                        # Unit and integration tests
├── third_party/                  # Bundled third-party notices and licenses
├── BINARY_PROVENANCE.md          # Managed upstream runtime provenance policy
├── CHANGELOG.md                  # Release history
├── CONTRIBUTING.md               # Contribution guide
├── SECURITY.md                   # Security reporting guidance
├── LICENSE                       # Apache License 2.0
└── THIRD_PARTY_NOTICES.md        # Third-party attribution
```

## Managed Secure Tunnel Runtime

Upstream tunnel binaries are intentionally excluded from Git and from every project release artifact. Tunnel mode uses the managed updater in `scripts/tunnel_runtime.py`.

On first use it:

1. Detects Windows/Linux/macOS and amd64/arm64.
2. Queries the latest official non-prerelease `openai/tunnel-client` release.
3. Selects the matching `tunnel-client-runtime-cloudflared-vX.Y.Z-<os>-<arch>.zip`.
4. Verifies the GitHub asset digest and the upstream `SHA256SUMS.txt` entry.
5. Extracts into `.agent_state/tunnel-runtime/<version>/<platform>/tunnel-client-runtime-cloudflared/`, keeping legacy/full-client installs separate so a live Windows runtime never has to be deleted during migration.
6. Executes the downloaded runtime only after its reported semantic version matches the release tag.

Later starts reuse that verified cache. When the update interval expires, the updater checks upstream again and atomically replaces the managed runtime only after full verification. If the network is unavailable and a verified managed cache already exists, startup can continue from that cache. A completely fresh installation with no cache fails closed if the first secure download cannot be verified.

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
