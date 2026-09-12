# Windows Developer Agent MCP

A high-performance, full-access local Windows developer-agent backend powered by the Model Context Protocol (MCP Python SDK v2). The server provides atomic file operations, validated AST refactoring, durable background jobs, system diagnostics, and seamless integration with the OpenAI Secure MCP Tunnel.

---

## Features

- **Robust Filesystem Tools**: Atomic file creation, writing, copying, moving, and deletion; paginated listing and regex file search; complete reads with streaming byte cursors.
- **Precision Code Editing**: Exact match, anchored replacements, and AST function/class body substitutions with syntax validation and rollback on syntax error.
- **Process & Command Execution**: Synchronous and background execution for PowerShell, CMD, and native Windows executables with streaming spooling (1 MiB RAM limit rolling over to temp disk).
- **Durable Job Store**: SQLite-backed background job queue (`.agent_state/jobs.sqlite3`) with idempotency keys, progress tracking, and survivor workers surviving supervisor restarts.
- **System Diagnostics**: Live inspection of CPU, memory, disks, environment variables, installed applications, and Windows services.
- **Codebase Intelligence**: Fast Python AST parsing for classes, functions, imports, and project summaries.
- **Validation Suite**: Compact summaries from integrated `pytest`, `Ruff`, and `mypy` tools.
- **Git Integration**: Working-tree status, diff statistics, and commit log pagination.
- **Optional Browser Automation**: Playwright browser automation with screenshot capture and UI interaction.
- **Resilient Supervisor & Control Panel**: Heartbeat watchdog, automatic backoff recovery, and a loopback-only control panel at `http://127.0.0.1:8766`.

---

## Architecture

The application runs in two primary modes:

1. **Secure MCP Tunnel Mode (Default)**:
   - `START_MCP.bat` launches `scripts/bootstrap.ps1`.
   - Bootstraps `.venv` (Python 3.10+), installs requirements, and executes startup smoke checks.
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
2. Keep the launcher window open. The launcher runs dependency checks, smoke validation, and starts the runtime supervisor.
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

### Performance Timing

Structured tool timing is opt-in and never changes MCP tool response payloads. Enable it only while profiling:

```powershell
$env:MCP_TIMINGS = '1'
.\START_MCP.bat
```

Timing records are metadata-only JSONL entries written to `.agent_state\timings.jsonl`. They include the MCP request pipeline, tool body, process spawn/execution/drain, output delivery, and artifact snapshot phases. Command arguments, file contents, typed browser text, and other payload bodies are not recorded. `MCP_TIMINGS_FILE` can override the local output path for isolated runs.

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

Use repeated runs for comparisons, for example `--runs 5`. Reports contain latency distributions, peak benchmark-process RSS samples, process I/O counters, tool catalog bytes, and case-specific details. Heavy job/browser cases require explicit opt-in so a normal benchmark cannot accidentally create a large process burst.

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
