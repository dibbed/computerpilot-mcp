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

Named tool profiles are `minimal`, `coding`, `coding_core`, `read_batch`, `git`, `testing`, `documents`, `desktop`, `browser`, `operations`, and `full`. The `documents` profile keeps filesystem, native document, recovery, and workflow domains together. Router candidate generation always uses the tools actually registered in the selected profile; catalog metadata does not bypass profile or platform filtering. Unsupported router policy names fail closed only when router APIs/routed steps are invoked and do not prevent the MCP server from starting.

Unreleased opt-in `coding_core` keeps a fixed 43-tool subset of the coding catalog. Set `MCP_TOOL_PROFILE=coding_core` before startup and restart to select it; switch back to `coding`/`full` with another restart. Extensions require explicit eligibility for the new profile. Its live-model UX pilot is pending separate budget/authorization; `full` remains the default. See [profiles](tooling.md#profiles).

The unreleased opt-in `read_batch` profile adds one bounded read-only `native_read_batch` tool alongside `read_file` and five existing discovery/health/routing tools (seven total). It must be selected explicitly before server startup, followed by a client reconnect to refresh a cached catalog. The existing `full` default and its tool count do not change. See [native read batching](tooling.md#opt-in-native-read-batching) for file, response, concurrency, timeout and cancellation limits.

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
| `MCP_WORKFLOW_DAG_ENABLED` | operator opt-in for DAG execution in MCP and worker | disabled |
| `MCP_WORKFLOW_MAX_PARALLEL_STEPS` | maximum accepted effective per-workflow parallel cap | `16` |
| `MCP_MAX_RUNNING_WORKFLOW_STEPS` | shared active DAG-step budget | `16` |
| `MCP_MAX_RUNNING_ANALYZERS` | shared managed Ruff/mypy/pytest budget | `3` |
| `MCP_MAX_RUNTIME_ADMISSION_RECORDS` | per-table retained admission-record limit; unresolved records are protected | `10000` |
| `MCP_JOB_HISTORY_MAX_COUNT` | retained terminal job count | `1000` |
| `MCP_JOB_HISTORY_MAX_AGE_DAYS` | terminal job retention age | `30` |
| `MCP_WORKFLOW_HISTORY_MAX_COUNT` | retained safe terminal workflow count | `1000` |
| `MCP_WORKFLOW_HISTORY_MAX_AGE_DAYS` | terminal workflow retention age | `30` |
| `MCP_WORKFLOW_DB_WARN_BYTES` | health warning threshold for workflow DB | `256 MiB` |

Queued/running/orphaned jobs and unresolved workflow evidence are protected from ordinary terminal-history cleanup.

DAG execution remains off by default; planning, persistence, inspection and dry runs remain available. After the same-version recovery, ownership, migration, independent-review and platform gates pass, set `MCP_WORKFLOW_DAG_ENABLED=1` and restart the MCP server and workflow workers. Start with an explicitly selected fixture DAG at `max_parallel_steps=2`; sequential definitions and omitted DAG caps retain cap 1. All processes sharing state must use compatible budget settings. Disable new DAG execution by setting `MCP_WORKFLOW_DAG_ENABLED=0` and restarting workers; drain or cancel active work first, and reconcile uncertainty before retrying. Disabling is not rollback and never clears claims or evidence. Back up SQLite with the connection backup API before migration; an older binary requires its matching pre-upgrade backup rather than opening a newer schema.

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

### Recoverable backups

| Variable | Purpose | Default |
| --- | --- | --- |
| `MCP_BACKUP_MAX_BYTES` | aggregate backup byte limit; `0` disables it | `256 MiB` |
| `MCP_BACKUP_MAX_COUNT` | backup file count limit, including empty files; `0` disables it | `0` (disabled) |
| `MCP_BACKUP_MAX_AGE_DAYS` | backup age limit; `0` disables it | `30` days |
| `MCP_BACKUP_CLEANUP_INTERVAL_SEC` | minimum interval between coalesced background cleanup scans | `5` seconds |

Cleanup removes eligible expired backups first, then the oldest eligible backups until both enabled byte and count limits are met. A removal under byte pressure increments `removed_for_quota`; a count-only removal increments `removed_for_count`, so the reason counters do not overlap. The newest restore point, explicitly protected paths, and backups within the 60-second recent-write grace period remain protected even when limits are exceeded. In that case cleanup reports `quota_satisfied=false` if either enabled limit remains exceeded.

The count setting accepts `0` through `1000000` (out-of-range integers are clamped; invalid values use `0`) and takes effect at runtime restart. It adds no scheduler or index: cleanup retains the existing flat directory scan and sort, so a configured count limit does not bound scan acquisition time. Health and panel snapshots expose the configured count limit; health uses its existing backup count observation without a second scan. No count-based retention runs by default.

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

Tool invocations have an internal 32-hex `correlation_id`, including when timing is disabled. Audit records and enabled timing records share this ID. `parent_operation_id` is present when a recovery operation exists; it is an observation reference, not an execution capability. Durable jobs keep bounded `trace_schema_version=1` metadata privately in their existing spec, outside the command's idempotency fingerprint. The scheduler and worker restore it explicitly across process boundaries. Older jobs without this metadata remain linked by `job_id`; their original request trace cannot be reconstructed.

Timing phases describe different intervals and must not be added together:

| Phase | Measured interval |
|---|---|
| `request_pipeline` | MCP dispatch through the typed result, before transport delivery |
| `serialization` | Existing JSON serialization probe, not socket/network latency |
| `claim_wait` | Resource admission after path/database setup, ending at admission or denial |
| `worker_launch` | Launching the durable worker process |
| `process_spawn` | Starting the owned command process |
| `process_execution` | Command wait/polling, including bounded termination when needed; excludes later ownership release and output drain |
| `job_worker` | Worker control flow after trace lookup; overlaps its command phases |
| `job_wait` | Long-poll and progress collection before trace lookup and response delivery |

Timing is best-effort: a missing observation does not establish that an operation was not executed. Timing failures do not change durable job/recovery outcomes. Existing retention and queue limits still apply.

## Extension configuration (v0.8)

The offline reader is opt-in: `MCP_EXTENSIONS_ENABLED` defaults `false`; only `1`, `true`, `yes`, or `on` enable it. `MCP_EXTENSIONS_CONFIG` may specify an absolute local JSON path; omit it for `config_home() / 'computerpilot-mcp' / 'extensions.json'`. The existing platform helper gives XDG precedence, then Windows APPDATA, macOS Application Support, or POSIX `~/.config`. This does not move native `.agent_state`.

Disabled mode does not read provider config. Missing files mean zero extensions. Invalid/future-version files disable all extensions with a safe code, preserve the original file, and leave native tools available. Every load independently validates UTF-8, byte/depth limits, duplicate JSON keys, strict schema 1 and portable absolute provider paths. Disabled placeholders need no existing executables/directories or resolved references until activation. Definitions are immutable snapshots; disk edits require runtime restart. No watcher, installer or external process is introduced by this reader.

Core ceilings are defined in `core/extensions/models.py:ExtensionLimits` and are frozen by Section 35 budgets and M14 benchmark measurements. Providers may only tighten timeouts. See [extension authoring](development/MCP-EXTENSION-AUTHORING.md) for authoring details and the [agent contract](../AGENTS.md) for the brief implementation handoff.

References are env-only, with explicit safe variable names; file references are deferred. Nonsecret values permit only PYTHONUNBUFFERED, PYTHONIOENCODING, LANG, LC_ALL, LC_CTYPE, TZ, NO_COLOR and TERM. Inheritance additionally permits OS launch/temp variables plus explicit HOME/APPDATA/USERPROFILE. No wildcard/PATH/injection-variable inheritance is allowed. HOME-style access still requires operator review; it can enable credential-file discovery.

Known credential flags, interpolation/control characters and resolved credential literals in operator fields are rejected even for disabled definitions. Missing references do not invalidate disabled configuration; resolving a private environment requires all configured credentials and tool-required reference names, otherwise auth_missing is returned without values/names. The builder does not launch code or authorize it. Provisional private environment ceilings are 8 KiB/value and 32 KiB total; no full parent environment is copied or public values projected. Unknown arbitrary text cannot be universally classified as a secret: operator review and reference use remain required.

### Offline publication and atomic configuration updates

Offline configuration candidate validation and publication are provided via `validate_extension_config_candidate` and `publish_extension_config` in `core.extensions.config`:
- **Offline validation before publication**: Evaluates candidate configuration against schema constraints, JSON depth/size bounds, unique keys, strict schema version 1, and security filters (no secrets in arguments, no command interpolation, absolute local paths) without spawning child processes or initiating network connections.
- **Cross-process lock & atomic replacement**: Publication acquires an `exclusive_file_lock` on the target `.lock` companion, writes bytes to a restrictive temporary file in the same parent directory (`0o600`), flushes and `fsync`s, and replaces atomically via `os.replace`. Temporary files are cleaned up on error.
- **Future schema protection**: If the existing configuration on disk specifies a future schema version (`schema_version > 1`), publication refuses to overwrite it, returning `config_version_unsupported` to prevent older runtimes from corrupting newer configurations.
- **Compare-and-swap (CAS) revision matching**: When `expected_revision` is specified, publication computes the SHA-256 hash of the existing configuration bytes and raises `config_conflict` if there is a mismatch, preventing concurrent overwrite races.
- **Restart-only contract**: The live server freezes accepted configuration upon startup. Changes published on disk do not trigger dynamic hot-reloading; they take effect only upon subsequent runtime restart.
- **Data preservation on remove/rename**: Removing or renaming an extension from configuration closes capabilities and reaps processes, while preserving all historical recovery journals, uncertain operations, and audit records under their original `extension_id`.
