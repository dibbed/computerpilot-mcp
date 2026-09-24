# Contributing to ComputerPilot MCP

Thanks for helping improve ComputerPilot MCP.

This repository contains a cross-platform MCP runtime with platform-specific adapters, durable local state, process execution, browser/desktop automation, recovery, workflows, and release automation. Changes should preserve those boundaries rather than making one platform or one execution path the implicit default.

## Development environment

### Requirements

- Python 3.10 or newer.
- Git.
- Windows, Linux, or macOS.
- `sh` on Linux/macOS.
- PowerShell is required only for Windows launcher/bootstrap paths and PowerShell-specific functionality.
- Playwright is optional unless you are changing browser behavior.
- `pyright-langserver` is optional unless you are validating LSP integrations.

### Bootstrap

The launchers create/reuse `.venv` and validate dependencies.

Windows:

```powershell
$env:MCP_START_MODE = "local-http"
.\START_MCP.bat
```

Linux/macOS:

```sh
MCP_START_MODE=local-http ./start_mcp.sh
```

For direct development you can also create a virtual environment and install:

```sh
python -m pip install -r requirements-dev.txt
```

Browser work additionally needs:

```sh
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
```

## Branch and commit workflow

Use a focused branch for each coherent change.

```text
branch
  ↓
implementation + focused tests
  ↓
repository validation
  ↓
commit
  ↓
push
  ↓
pull request
  ↓
CI / release gates when applicable
  ↓
merge
```

Prefer commits that leave the repository valid at that point in history. Do not force-push published/released history or rewrite release tags.

## Validation

Before opening a pull request, run the checks relevant to your change.

Core validation:

```sh
python -m compileall -q core tools scripts tests
python -m ruff check .
python -m mypy core tools scripts tests
python main.py --check
python -m scripts.health_check --json
python -m pytest
```

For cross-platform/release-sensitive changes:

```sh
python -m scripts.platform_validation --json
```

For packaging changes:

```sh
python -m scripts.package_release --version <current-version> --all --output-dir dist --json
```

Then validate `dist/SHA256SUMS.txt`.

For browser changes:

```sh
python -m scripts.doctor --mode local-http --browser
```

## Platform rules

Portable domains must continue to import and register on supported Windows, Linux, and macOS hosts.

Platform-specific behavior must remain capability-gated:

- Windows desktop screenshot/input and semantic UI Automation are Windows-only.
- `run_cmd` is Windows-only.
- POSIX shell behavior belongs on Linux/macOS.
- PowerShell tools are available only when PowerShell/pwsh exists.
- Process-tree ownership has separate Windows Job Object and POSIX process-group implementations.

Do not hide real portability failures with broad skips. Prefer fixing the assumption or making the capability contract explicit.

## Secrets and local state

Never commit:

- `.secrets/`
- `.agent_state/`
- `.venv/`
- local `.env` files
- generated databases/logs/output
- real API keys, tokens, cookies, or credentials

Sanitize logs before attaching them to issues or pull requests.

## Secure Tunnel runtime rules

The repository does **not** vendor OpenAI tunnel-client or Cloudflared executables.

Tunnel mode obtains a managed runtime through `scripts/tunnel_runtime.py` from official `openai/tunnel-client` releases and validates release metadata, SHA-256 data, archive safety, and binary version before publication under `.agent_state/tunnel-runtime/`.

Changes to this path must preserve fail-closed verification, safe archive extraction, managed-cache behavior, and [BINARY_PROVENANCE.md](BINARY_PROVENANCE.md).

Do not add upstream runtime executables or archives to Git or release packages.

## Recovery and mutation safety

Be especially careful when changing filesystem edits, Git mutations, process execution, jobs, recovery, or workflows.

Important invariants:

- do not blindly replay uncertain external side effects;
- keep mutation evidence durable enough for reconciliation;
- preserve hash/version guards where present;
- keep durable-job idempotency semantics stable;
- preserve workflow optimistic-version and lease rules;
- keep resource locks scoped rather than replacing them with one global lock.

Tests should cover failure/restart behavior when a change affects these invariants.

## Pull requests

A useful pull request explains:

- the problem;
- the change;
- how it was validated;
- platform impact;
- compatibility impact;
- documentation impact.

Use the repository pull request template and keep the scope reviewable.

## Documentation

Update public documentation when behavior, configuration, platform support, security boundaries, or release artifacts change.

Keep historical release notes factual. Do not rewrite published release history for presentation purposes.

See [docs/README.md](docs/README.md) for the public documentation map.
