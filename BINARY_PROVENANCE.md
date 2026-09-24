# Bundled Binary Provenance and Managed Tunnel Runtime

This document separates two different responsibilities:

1. the **Git-tracked offline fallback** binaries shipped with the repository; and
2. the **managed runtime** selected by normal tunnel startup.

The managed runtime is intentionally installed outside tracked source under
`.agent_state/tunnel-runtime/` so updating the Secure MCP Tunnel does not rewrite
the Git checkout or replace a running executable in place.

> The tunnel-client and Cloudflared binaries are third-party components authored
> by OpenAI and Cloudflare, Inc. They are not authored by Ali Khalili.

## 1. Git-tracked fallback inventory

The current repository fallback remains the older full-client matched set:

| File | Version | Architecture | SHA-256 |
| --- | --- | --- | --- |
| `tunnel-client.exe` | `0.0.11+8d55683eeef80bc5e360d95abf4692454fafc615` | windows/amd64 | `7D3C7D492CE84B52835E11865A835A8A5BCD4A669DEE84E169AA11B314DC952A` |
| `cloudflared.exe` | `2026.7.2` | windows/amd64 | `88024CF82CEC72D10604C13AA4670016DCA375C602E200B551EC9D53B31E874D` |
| `cloudflared-manifest.json` | pinned to Cloudflared `2026.7.2` | N/A | `149C1B5C0095FFAB41C3986D620CA18C35373E05C5B6D4A7A5` |

The manifest checksum above is retained from the original fallback provenance.
When validating a local checkout, compute the file digest directly rather than
treating this table as a substitute for an integrity check.

### Fallback tunnel-client provenance

- Upstream repository: `openai/tunnel-client`
- Upstream release: `v0.0.11`
- Upstream Git commit: `8d55683eeef80bc5e360d95abf4692454fafc615`
- Original asset: `tunnel-client-v0.0.11-windows-amd64.zip`
- Original archive SHA-256: `eb912c86c6ccde90cda805cb17009507176a656725cf86c36fabe1901a12e29b`

These tracked files are retained so tunnel mode still has a known local fallback
when a managed release has not yet been installed and GitHub is unavailable.

## 2. Normal managed-runtime policy

Normal startup uses `scripts.tunnel_runtime` before launching the Supervisor.

The resolver:

1. honors an explicit `MCP_TUNNEL_CLIENT_BIN` override;
2. otherwise checks an already verified managed runtime;
3. recognizes a locally present `tunnel-client-runtime[.exe]` when one exists;
4. checks the official `openai/tunnel-client` stable release channel when the
   configured update interval has elapsed;
5. downloads the exact full-client archive for the detected OS/architecture;
6. validates the release asset SHA-256 from GitHub release metadata;
7. independently matches the same archive against upstream `SHA256SUMS.txt`;
8. extracts only after those checks into a staging directory;
9. runs the downloaded binary with `--version` and requires the reported
   semantic version to equal the selected release tag;
10. atomically publishes the immutable version directory under
    `.agent_state/tunnel-runtime/<tag>/<platform-arch>/`;
11. points the Supervisor at that managed binary.

A failed network request, invalid archive, digest mismatch, version mismatch, or
unsafe ZIP path cannot replace a known-good runtime.

As of 2026-09-24, the latest stable upstream release observed during this work is
`v0.0.14`. The updater does **not** hard-code that version unless
`MCP_TUNNEL_VERSION=v0.0.14` is explicitly configured.

## 3. Update controls

| Variable | Meaning | Default |
| --- | --- | --- |
| `MCP_TUNNEL_AUTO_UPDATE` | Enable official release checks during startup | `1` |
| `MCP_TUNNEL_UPDATE_INTERVAL_HOURS` | Minimum interval between latest-release checks | `24` |
| `MCP_TUNNEL_VERSION` | Optional exact stable release pin | latest stable |
| `MCP_TUNNEL_UPDATE_REQUIRED` | Fail startup instead of using a valid fallback after update failure | `0` |
| `MCP_TUNNEL_ALLOW_PRERELEASE` | Permit an explicitly selected prerelease | `0` |
| `MCP_TUNNEL_CLIENT_BIN` | Explicit operator-selected binary path; bypasses auto-update | unset |

The default behavior is availability-preserving: an update failure is visible,
but a previously validated runtime or local fallback may still be used.
Operators that require strict freshness can set
`MCP_TUNNEL_UPDATE_REQUIRED=1`.

## 4. Platform selection

The updater currently understands official upstream archive naming for:

- Windows amd64 / arm64
- Linux amd64 / arm64
- macOS (darwin) amd64 / arm64

This only makes **tunnel runtime acquisition** platform-aware. It does not mean
the Windows Agent MCP itself is already cross-platform. Windows-only process
ownership, UI Automation, desktop tooling, launcher behavior, system
diagnostics, and other OS-specific surfaces remain separate migration work.

## 5. Security and release rules

- Only HTTPS assets under the official `openai/tunnel-client` GitHub release
  namespace are accepted.
- Draft releases are rejected.
- Prereleases are rejected unless explicitly enabled.
- Release archive integrity must agree with both GitHub asset metadata and
  upstream `SHA256SUMS.txt`.
- ZIP traversal paths are rejected before extraction.
- A downloaded binary must successfully report the expected release version.
- Managed releases are installed into versioned immutable directories rather
  than overwriting the active executable.
- Secrets and tunnel profile contents are never written into updater metadata.
- `.agent_state/` remains ignored by Git and must never be packaged as a
  source or release artifact.

## 6. Historical local runtime note

A local project archive supplied during v0.2.6 follow-up contained:

- full `tunnel-client.exe` at **0.0.11**
- `tunnel-client-runtime.exe` at **0.0.14**
- the matching v0.0.14 runtime SPDX/license evidence

The previous Supervisor still launched the full `tunnel-client.exe`, so that
manual runtime addition did not change the actually executed tunnel version.
The managed resolver removes this ambiguity by selecting and reporting the
binary that the Supervisor will really execute.
