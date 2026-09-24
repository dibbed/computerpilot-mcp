# Third-Party Notices

ComputerPilot MCP is licensed under the Apache License, Version 2.0 (Copyright 2026 Ali Khalili).

The project depends on third-party software and can download an external Secure Tunnel runtime. The repository does **not** claim ownership or authorship of those components.

## OpenAI Secure MCP Tunnel runtime

Tunnel mode can download a managed runtime from the official public project:

- Project: `openai/tunnel-client`
- Upstream: https://github.com/openai/tunnel-client
- Runtime flavor used by default: `tunnel-client-runtime-cloudflared`
- License: Apache License 2.0

ComputerPilot MCP does not vendor the tunnel runtime executable in Git or in project release archives. It is downloaded separately into `.agent_state/tunnel-runtime/` and verified according to [BINARY_PROVENANCE.md](BINARY_PROVENANCE.md).

The repository keeps the upstream license/notice material under:

- [third_party/tunnel-client/LICENSE](third_party/tunnel-client/LICENSE)
- [third_party/tunnel-client/NOTICE](third_party/tunnel-client/NOTICE)

These files provide attribution and license context; their presence does not mean the upstream executable is bundled.

## Cloudflare Tunnel

The upstream `tunnel-client-runtime-cloudflared` distribution integrates Cloudflare Tunnel functionality.

- Project: `cloudflare/cloudflared`
- Upstream: https://github.com/cloudflare/cloudflared
- License: Apache License 2.0

The repository retains:

- [third_party/cloudflared/LICENSE](third_party/cloudflared/LICENSE)

ComputerPilot MCP does not separately vendor a Cloudflared executable in current project releases.

## Python dependencies

Runtime and development dependencies are declared in:

- `requirements-runtime.txt`
- `requirements-quality.txt`
- `requirements-dev.txt`
- `requirements-browser.txt`
- `pyproject.toml`

Notable dependencies include:

- `mcp`
- `pydantic`
- `psutil`
- `charset-normalizer`
- `Pillow`
- `uiautomation` on Windows
- `pytest`, `ruff`, and `mypy` for validation
- `playwright` for optional browser automation

Each dependency remains subject to its own upstream license and distribution terms.

## Release artifacts

Current release packaging is source/runtime-controller packaging. It intentionally excludes upstream tunnel-client/Cloudflared executables and local runtime state.

See [BINARY_PROVENANCE.md](BINARY_PROVENANCE.md) and [scripts/package_release.py](scripts/package_release.py) for the enforced packaging policy.
