# Bundled Binary Provenance and Integrity

This document records the provenance, checksums, and licensing for third-party executable binaries bundled in this repository.

> **Notice**: The executable binaries (`tunnel-client.exe`, `cloudflared.exe`) and their companion manifest (`cloudflared-manifest.json`) are third-party software components authored by OpenAI and Cloudflare, Inc. They are **not** authored by Ali Khalili.

---

## 1. Inventory & Integrity Baseline

| File | Version | Architecture | SHA-256 Checksum | License |
| :--- | :--- | :--- | :--- | :--- |
| `tunnel-client.exe` | `0.0.11+8d55683eeef80bc5e360d95abf4692454fafc615` | `windows/amd64` | `7D3C7D492CE84B52835E11865A835A8A5BCD4A669DEE84E169AA11B314DC952A` | Apache-2.0 |
| `cloudflared.exe` | `2026.7.2` (built 2026-07-15T13:30:00Z) | `windows/amd64` | `88024CF82CEC72D10604C13AA4670016DCA375C602E200B551EC9D53B31E874D` | Apache-2.0 |
| `cloudflared-manifest.json` | Pinned to `cloudflared` `2026.7.2` | N/A | `149C1B5C0095FFAB41C3986D620CA18C35373E05C5B6CA0BEA88AC19F6D4A7A5` | Apache-2.0 |

---

## 2. Component Details

### `tunnel-client.exe`

- **Purpose**: OpenAI Secure MCP Tunnel client daemon. Establishes an outbound encrypted tunnel to the OpenAI MCP control plane, forwarding incoming MCP tool calls to the local agent server.
- **Upstream Project**: [openai/tunnel-client](https://github.com/openai/tunnel-client)
- **Upstream Release**: [v0.0.11](https://github.com/openai/tunnel-client/releases/tag/v0.0.11) (published 2026-08-07T06:56:35Z)
- **Upstream Git Commit**: `8d55683eeef80bc5e360d95abf4692454fafc615`
- **Release Asset**: `tunnel-client-v0.0.11-windows-amd64.zip`
- **Upstream Archive Checksum**: `eb912c86c6ccde90cda805cb17009507176a656725cf86c36fabe1901a12e29b` (verified in official `SHA256SUMS.txt`)
- **Authenticode Signature**: `NotSigned` (expected; standard for upstream open-source Go builds)
- **Why Bundled**: Enables immediate out-of-the-box startup on Windows via `START_MCP.bat` without requiring users to install Go, build from source, or manage external binary path environments.

### `cloudflared.exe`

- **Purpose**: Cloudflare Tunnel client daemon invoked internally by `tunnel-client.exe` when establishing transport tunnels via Cloudflare edge infrastructure.
- **Upstream Project**: [cloudflare/cloudflared](https://github.com/cloudflare/cloudflared)
- **Upstream Release**: [2026.7.2](https://github.com/cloudflare/cloudflared/releases/tag/2026.7.2)
- **Pinned Commit**: `8679787525edc8575b2948a7c4a50b6292c6d426`
- **Go Module Reference**: `github.com/cloudflare/cloudflared@v0.0.0-20260715110107-8679787525ed`
- **Why Bundled**: `tunnel-client.exe` expects `cloudflared.exe` to reside in the same directory for Cloudflare-backed transports. Removing or renaming `cloudflared.exe` breaks tunnel connectivity even though Python code does not call `cloudflared.exe` directly.

### `cloudflared-manifest.json`

- **Purpose**: Upstream integrity metadata distributed with `tunnel-client` to declare the exact version, commit, module sum, and platform targets of the bundled `cloudflared.exe`.
- **Why Bundled**: Maintained alongside the binary pair to preserve provenance traceability and upstream compatibility.

---

## 3. Bundled Distribution Policy

1. **Matched Set Invariant**: The three files (`tunnel-client.exe`, `cloudflared.exe`, and `cloudflared-manifest.json`) form an interdependent matched set. Neither executable should be replaced or updated independently.
2. **Authoritative Sources Only**: Replacements must originate exclusively from official [openai/tunnel-client releases](https://github.com/openai/tunnel-client/releases). Unofficial mirrors, forums, or third-party builds are strictly prohibited.
3. **Integrity Verification**: Any future binary update must verify upstream signatures and checksums before staging, and this document must be updated to match the new hashes.
