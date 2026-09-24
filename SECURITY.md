# Security Policy

ComputerPilot MCP intentionally exposes powerful local capabilities including filesystem mutation, process execution, browser automation, durable jobs, Git operations, and Windows desktop automation when supported.

Treat access to the MCP endpoint as equivalent to granting a development agent access to the OS account that runs the server.

## Reporting a vulnerability

Please do not open a public issue, discussion, or pull request for a suspected vulnerability that could expose credentials, allow unintended local execution, bypass a safety boundary, or compromise release/runtime verification.

Preferred reporting path:

1. Use GitHub Private Vulnerability Reporting / Security Advisories for this repository if available.
2. If private reporting is unavailable, contact the maintainer through the [GitHub profile](https://github.com/dibbed/) without publishing exploit details.

Include:

- affected project version/commit;
- operating system and architecture;
- Python version;
- affected start mode (`local-http` or tunnel);
- minimal reproduction steps;
- expected vs actual behavior;
- sanitized logs or evidence.

Never include real API keys, tokens, cookies, private source, or other credentials.

## Local authority model

The server is not a sandbox around the OS account running it.

Depending on the active tool profile and platform, an authorized MCP client may be able to:

- read and modify files accessible to the account;
- start or terminate processes;
- execute commands;
- interact with browsers;
- create durable jobs and workflows;
- inspect system metadata;
- use Windows native input/UI Automation.

Run it only under an account whose local permissions match the access you intend to grant.

## Network exposure

The local Streamable HTTP endpoint and control panel bind to loopback by default.

Do not directly expose:

- `127.0.0.1:8765/mcp`
- `127.0.0.1:8766/`

to an untrusted network.

The control panel adds host/origin checks and a session control token for guarded local mutations, but it is still intended for local operator use.

## Secrets

Tunnel credentials should be supplied through environment variables or:

```text
.secrets/control_plane_api_key.txt
```

`.secrets/` and local `.env` files are ignored by Git.

Do not put credentials in durable job idempotency keys, Git commit messages, issue reports, workflow descriptions, or other metadata that may be retained.

## Managed Secure Tunnel runtime

The repository and project release archives do not vendor tunnel-client or Cloudflared executables.

Tunnel mode uses `scripts/tunnel_runtime.py` to obtain the official `openai/tunnel-client` runtime. The updater validates the selected release asset, SHA-256 data, archive paths, symlink policy, and reported binary version before publishing it into `.agent_state/tunnel-runtime/`.

A fresh installation fails closed if no verified runtime can be obtained. An already-verified managed cache may be reused when a later update check is unavailable, according to the configured update policy.

See [BINARY_PROVENANCE.md](BINARY_PROVENANCE.md).

## Local state and logs

Generated state under `.agent_state/` can include:

- durable job/workflow metadata and output;
- screenshots;
- recoverable edit backups;
- search snapshots;
- structured audit records;
- supervisor logs;
- recovery evidence;
- the managed tunnel runtime.

Project memory records live under `memory/`.

Do not publish these directories as release artifacts or attach them wholesale to issues. Sanitize logs and inspect screenshots/output before sharing.

## Recovery behavior

The runtime deliberately avoids blind replay of ambiguous mutations.

When an operation may have produced an external side effect but its final result was not durably recorded, it can remain `uncertain` until reconciled from evidence or explicitly acknowledged by an operator.

This reduces duplicate side effects but does not turn arbitrary external commands into transactions.

## Dependency and release trust

Release packaging is generated from tracked Git blobs and rejects forbidden runtime/secret/build-state paths. Published releases include `SHA256SUMS.txt`.

GitHub release publication is gated on the repository's CI, platform validation, and packaging workflows for the same release SHA.

These controls reduce accidental release drift; they are not a formal security certification or guarantee.
