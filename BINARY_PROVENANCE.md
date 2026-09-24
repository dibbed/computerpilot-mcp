# Secure Tunnel Runtime Provenance

This repository does not vendor OpenAI tunnel-client or Cloudflared executables.

## Runtime source

Tunnel mode acquires its runtime only from the official public repository:

- Upstream repository: `openai/tunnel-client`
- Release discovery: GitHub `releases/latest` by default
- Default flavor: `tunnel-client-runtime-cloudflared`
- Supported host mappings: Windows/Linux/macOS on amd64/arm64
- Local managed cache: `.agent_state/tunnel-runtime/`

The managed runtime is local machine state and is intentionally ignored by Git.

## First-run installation

When no compatible managed runtime is installed, `scripts/tunnel_runtime.py`:

1. detects the host OS and CPU architecture;
2. requests the latest official non-draft, non-prerelease upstream release;
3. selects `tunnel-client-runtime-cloudflared-vX.Y.Z-<os>-<arch>.zip`;
4. requires the asset URL to be an approved `github.com/openai/tunnel-client` URL;
5. verifies the GitHub release asset SHA-256 digest;
6. downloads and verifies upstream `SHA256SUMS.txt`;
7. rejects archive path traversal and symbolic links;
8. extracts into an immutable version/platform directory under `.agent_state/tunnel-runtime/`;
9. executes the downloaded binary with `--version` and requires it to match the release tag;
10. atomically records the selected path, platform, flavor, version, asset name, and archive hash in `current.json`.

A fresh installation fails closed if the runtime cannot be downloaded and verified.

## Update and offline behavior

The default updater periodically checks the official latest release. A newer runtime is published locally only after all verification succeeds.

If a previously verified managed cache exists and a later update check fails because the network or upstream service is unavailable, the existing managed runtime may be reused. There is no repository-bundled binary fallback.

Operators can pin a specific upstream version with `MCP_TUNNEL_VERSION`, disable automatic checks with `MCP_TUNNEL_AUTO_UPDATE=0`, require a successful update check with `MCP_TUNNEL_UPDATE_REQUIRED=1`, or provide an explicit local override with `MCP_TUNNEL_CLIENT_BIN`.

## Repository and release policy

The following upstream runtime files must not be tracked or embedded in project release archives:

- `tunnel-client` / `tunnel-client.exe`
- `tunnel-client-runtime*`
- `tunnel-client-runtime-cloudflared*`
- `cloudflared` / `cloudflared.exe`
- `cloudflared-manifest.json`
- upstream runtime ZIP/license/SPDX sidecars

`.gitignore` excludes these files, and `scripts.package_release` fails closed if an upstream tunnel runtime file is tracked at repository root.

This keeps project releases platform-neutral at the source/controller layer and prevents stale upstream executables from being silently redistributed.
