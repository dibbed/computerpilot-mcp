# Configuration

Environment variables are the runtime configuration interface. [`.env.example`](../.env.example) is the primary checked-in reference for supported runtime settings and examples.

This page highlights the settings most users are likely to change.

## Startup and tool catalog

| Variable | Purpose | Default |
| --- | --- | --- |
| `MCP_START_MODE` | `tunnel` or `local-http` | `tunnel` |
| `MCP_TOOL_PROFILE` | active catalog profile | `full` |
| `CONTROL_PLANE_API_KEY` | tunnel control-plane credential | unset |
| `MCP_TUNNEL_PROFILE` | explicit tunnel profile | auto-detected / `default` |

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
| `MCP_VISION_MAX_BYTES` | original image size before bounded preview | `8 MiB` |
| `MCP_VISION_JPEG_QUALITY` | preview JPEG quality | `88` |

## Supervisor lifecycle

| Variable | Purpose | Default |
| --- | --- | --- |
| `MCP_SUPERVISOR_DRAIN_SEC` | normal mutation-drain deadline | `15` seconds |
| `MCP_SUPERVISOR_WATCHDOG_DRAIN_SEC` | watchdog best-effort drain | `2` seconds |
| `MCP_WINDOWS_JOB_OBJECTS` | Windows Job Object ownership | enabled |

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
