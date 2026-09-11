# Contributing Guidelines

Thank you for your interest in contributing to the Windows Developer Agent MCP project!

---

## 1. Development Environment

- **Operating System**: Windows 10 or Windows 11 (x86-64 / amd64).
- **Python**: Python 3.10+ required.
- **PowerShell**: Windows PowerShell 5.1 or PowerShell 7+.

### Bootstrap

Set up the development virtual environment and verify dependencies:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

This creates `.venv`, installs requirements from `requirements.txt`, and validates imports.

---

## 2. Quality & Validation Standards

Before submitting changes, all checks must pass cleanly:

```powershell
# 1. Run unit and integration tests
.\.venv\Scripts\python.exe -m pytest

# 2. Check code style and linting
.\.venv\Scripts\python.exe -m ruff check .

# 3. Verify static type safety
.\.venv\Scripts\python.exe -m mypy core tools scripts tests

# 4. Run MCP health checks
.\.venv\Scripts\python.exe -m scripts.health_check --json
```

---

## 3. Strict Rules for Contributions

### No Secrets
- Never commit credentials, private tokens, API keys, session data, or personal paths.
- Store test credentials only in mocks or temporary environment variables within test fixtures.

### Bundled Binary Rules
- Do **not** independently replace or upgrade `tunnel-client.exe` or `cloudflared.exe` without following the matched-set procedure.
- If binary updates are proposed, they must be sourced directly from official [openai/tunnel-client](https://github.com/openai/tunnel-client/releases) releases.
- Any change to binaries **must** be accompanied by updated hashes and metadata in [BINARY_PROVENANCE.md](BINARY_PROVENANCE.md) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

### Code Quality
- Preserve component boundaries and domain separation (`core/`, `tools/`, `scripts/`).
- Do not weaken or delete existing tests to force CI to pass.
