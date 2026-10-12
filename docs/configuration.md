# Configuration

Environment variables are the runtime configuration interface. [`.env.example`](../.env.example) is the primary checked-in reference for supported runtime settings and examples.

This page highlights the settings most users are likely to change.

## Startup and tool catalog

| Variable | Purpose | Default |
| --- | --- | --- |
| `MCP_START_MODE` | `tunnel` or `local-http` | `tunnel` |
| `MCP_TOOL_PROFILE` | active catalog profile | `full` |
| `MCP_EXECUTION_ROUTER_ENABLED` | enable read-only adaptive route diagnostics and routed workflow planning | enabled |
| `MCP_EXECUTION_ROUTER_POLICY` | versioned deterministic routing policy | `deterministic-v1` |
| `MCP_EXECUTION_ROUTER_EXPLAIN` | include bounded candidate explanations in recommendations | enabled |
| `CONTROL_PLANE_API_KEY` | tunnel control-plane credential | unset |
| `MCP_TUNNEL_PROFILE` | explicit tunnel profile | auto-detected / `default` |

Named tool profiles are `minimal`, `coding`, `git`, `testing`, `documents`, `desktop`, `browser`, `operations`, and `full`. The `documents` profile keeps filesystem, native document, recovery, and workflow domains together. Router candidate generation always uses the tools actually registered in the selected profile; catalog metadata does not bypass profile or platform filtering. Unsupported router policy names fail closed only when router APIs/routed steps are invoked and do not prevent the MCP server from starting.

## Managed tunnel runtime

| Variable | Purpose | Default |
| --- | --- | --- |
| `MCP_TUNNEL_AUTO_UPDATE` | periodically check the official upstream release | enabled |
| `MCP_TUNNEL_UPDATE_INTERVAL_HOURS` | minimum interval between latest-release checks | `24` |
| `MCP_TUNNEL_VERSION` | pin one upstream tunnel version | latest stable |
| `MCP_TUNNEL_UPDATE_REQUIRED` | require update verification to succeed | disabled |
| `MCP_TUNNEL_ALLOW_PRERELEASE` | allow prerelease selection | disabled |
| `MCP_TUNNEL_CLIENT_BIN` | explicit local runtime override | unset |

The managed cache is stored under `.agent_state/tunnel-runtime/`.

## Durable jobs and workflows

| Variable | Purpose | Default |
| --- | --- | --- |
| `MCP_MAX_RUNNING_JOBS` | maximum concurrently active durable jobs | `4` |
| `MCP_JOB_HISTORY_MAX_COUNT` | retained terminal job count | `1000` |
| `MCP_JOB_HISTORY_MAX_AGE_DAYS` | terminal job retention age | `30` |
| `MCP_WORKFLOW_HISTORY_MAX_COUNT` | retained safe terminal workflow count | `1000` |
| `MCP_WORKFLOW_HISTORY_MAX_AGE_DAYS` | terminal workflow retention age | `30` |
| `MCP_WORKFLOW_DB_WARN_BYTES` | health warning threshold for workflow DB | `256 MiB` |

Queued/running/orphaned jobs and unresolved workflow evidence are protected from ordinary terminal-history cleanup.

## Browser and image delivery

| Variable | Purpose | Default |
| --- | --- | --- |
| `MCP_BROWSER_IDLE_SEC` | idle session lifetime | `900` |
| `MCP_BROWSER_POOL_IDLE_SEC` | empty pool lifetime | `120` |
| `MCP_BROWSER_MAX_SESSIONS` | logical browser-session budget | `20` |
| `MCP_BROWSER_MAX_POOLS` | shared browser-pool budget | `6` |
| `MCP_BROWSER_SEMANTIC_MAX_NODES` | maximum nodes in one semantic browser snapshot | `500` |
| `MCP_BROWSER_SEMANTIC_MAX_BYTES` | semantic snapshot/extraction text budget | `256 KiB` |
| `MCP_BROWSER_DOWNLOAD_MAX_BYTES` | maximum accepted controlled browser download size | `512 MiB` |
| `MCP_VISION_MAX_BYTES` | original image size before bounded preview | `8 MiB` |
| `MCP_VISION_JPEG_QUALITY` | preview JPEG quality | `88` |

## Supervisor lifecycle

| Variable | Purpose | Default |
| --- | --- | --- |
| `MCP_SUPERVISOR_DRAIN_SEC` | normal mutation-drain deadline | `15` seconds |
| `MCP_SUPERVISOR_WATCHDOG_DRAIN_SEC` | watchdog best-effort drain | `2` seconds |
| `MCP_WINDOWS_JOB_OBJECTS` | Windows Job Object ownership | enabled |

## Transport observability and watchdog

| Variable | Purpose | Default |
| --- | --- | --- |
| `MCP_TRANSPORT_UPSTREAM_IDLE_SEC` | classify a healthy but command-idle upstream period | `300` seconds |
| `MCP_TUNNEL_POLL_STALL_GRACE_SEC` | grace beyond the runtime-reported poll deadline | `15` seconds |
| `MCP_TUNNEL_POLL_STALL_CONFIRMATIONS` | consecutive confirmed stall observations before recovery eligibility | `3` |
| `MCP_TUNNEL_POLL_WATCHDOG` | allow conservative restart after confirmed poll stall | enabled |
| `MCP_TOOL_STALL_SEC` | diagnostic threshold for a long-running local MCP tool | `3600` seconds |
| `MCP_TRANSPORT_HISTORY_INTERVAL_SEC` | minimum persisted transport-history interval | `60` seconds |

These controls distinguish healthy upstream idleness from a confirmed control-plane poll stall. Upstream idle alone never triggers automatic restart.

## Search and project caches

| Variable | Purpose | Default |
| --- | --- | --- |
| `MCP_AST_CACHE_MAX_FILES` | bounded parsed-file metadata entries | `10000` |
| `MCP_AST_CACHE_MAX_BYTES` | project metadata cache budget | `64 MiB` |
| `MCP_SEARCH_SNAPSHOT_TTL_SEC` | search continuation snapshot lifetime | `180` seconds |
| `MCP_SEARCH_SNAPSHOT_MAX_COUNT` | maximum snapshots | `32` |
| `MCP_SEARCH_SNAPSHOT_MAX_BYTES` | aggregate snapshot budget | `128 MiB` |

## Local storage retention

The runtime also exposes age/count/byte policies for:

- artifacts;
- backups;
- durable job history/output;
- workflow history;
- audit rotation.

Set a retention dimension to `0` only where the configuration explicitly documents `0` as disabling that dimension.

## Output delivery

Compatible output tools can choose inline, file-backed, or automatic bounded delivery.

Useful controls include:

```text
MCP_OUTPUT_DEFAULT
MCP_INLINE_SOFT_LIMIT_BYTES
MCP_INLINE_HARD_LIMIT_BYTES
MCP_PREVIEW_BYTES
```

The delivery layer is designed to preserve full disk-backed data while avoiding unnecessarily large model-facing responses.

## Timing instrumentation

Structured runtime timing is opt-in:

```text
MCP_TIMINGS=1
```

Timing records are local metadata/durations; they are not intended to log command bodies, file contents, secrets, or typed UI values.
## Extension configuration foundation (v0.8 in progress)

The offline reader is opt-in: MCP_EXTENSIONS_ENABLED defaults false; only 1, true, yes, or on enable it. MCP_EXTENSIONS_CONFIG may specify an absolute local JSON path; omit it for config_home()/computerpilot-mcp/extensions.json. The existing platform helper gives XDG precedence, then Windows APPDATA, macOS Application Support, or POSIX ~/.config. This does not move native .agent_state.

Disabled mode does not read provider config. Missing files mean zero extensions. Invalid/future-version files disable all extensions with a safe code, preserve the original file, and leave native tools available. Every load independently validates UTF-8, byte/depth limits, duplicate JSON keys, strict schema1 and portable absolute provider paths. Disabled placeholders need no existing executables/directories or resolved references until activation. Definitions are immutable snapshots; disk edits require runtime restart. No watcher, installer or external process is introduced by this reader.

Provisional Core ceilings live in core/extensions/models.py:ExtensionLimits and are measured/frozen by M14. Providers may only tighten timeouts. See [extension authoring](development/MCP-EXTENSION-AUTHORING.md) for the proposed contract and the [agent contract](../AGENTS.md) for the brief implementation handoff. Public exposure and mutation activation remain gated; foundation code does not authorize launching unreviewed providers.

References are env-only, with explicit safe variable names; file references are deferred. Nonsecret values permit only PYTHONUNBUFFERED, PYTHONIOENCODING, LANG, LC_ALL, LC_CTYPE, TZ, NO_COLOR and TERM. Inheritance additionally permits OS launch/temp variables plus explicit HOME/APPDATA/USERPROFILE. No wildcard/PATH/injection-variable inheritance is allowed. HOME-style access still requires operator review; it can enable credential-file discovery.

Known credential flags, interpolation/control characters and resolved credential literals in operator fields are rejected even for disabled definitions. Missing references do not invalidate disabled configuration; resolving a private environment requires all configured credentials and tool-required reference names, otherwise auth_missing is returned without values/names. The builder does not launch code or authorize it. Provisional private environment ceilings are 8 KiB/value and 32 KiB total; no full parent environment is copied or public values projected. Unknown arbitrary text cannot be universally classified as a secret: operator review and reference use remain required.
