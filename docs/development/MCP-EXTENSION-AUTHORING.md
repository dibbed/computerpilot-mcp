# External MCP extension authoring

Status: **v0.8 extension contract retained in v0.9.0.** Follow the [agent contract](../../AGENTS.md) for the brief verified handoff and next task, and inspect current source and CI for evidence. The full master plan remains only in the operator's Windows Downloads directory. This guide is the operator/provider contract. [Architecture](../architecture.md), [configuration](../configuration.md), [testing standards](TESTING-STANDARDS.md), and [release process](../release-process.md) remain their existing authorities.

The implemented bridge uses public `MCPServer.list_tools`/`call_tool` overrides and delegates native calls to the SDK. External direct and SDK calls share current profile/platform/auth/schema authority before and after queue waits. Listing performs no provider RPC or spawn; mutation visibility also checks local recovery capacity. Credential-bearing input schemas are withheld rather than altered. Nonfinite or nonpositive caller deadlines are rejected before dispatch. One call deadline uses the minimum caller/operator/Core limit and covers admission and provider execution; rejected and failed calls return fixed Core envelopes. Discovery adds `tool_counts` with native/external/total values. Recommendations include external source/provider/extension/domain/generation attribution only for eligible live tools, with 180-character descriptions and at most 25 matches. The original nine native profiles retain their catalogs; the opt-in coding_core/read_batch profiles require explicit eligibility; `full` requires explicit external profile approval and `minimal` permits only approved read-only external tools. Real stdio and modern streamable-HTTP roundtrips are covered; M0 through M15 are verified on main; mutation activation, process lifecycle, telemetry, panel observability, atomic configuration publication, and resource ceilings are all verified and frozen.

The internal call boundary uses `ExtensionLifecycleManager.execute_guarded_call`, validates and freezes exact JSON arguments before admission, and returns the Core `ExtensionResult` envelope. The owner session helper is internal. Large normalized output returns an explicitly truncated JSON preview; operator `ToolPolicy.allow_output_artifact` defaults to `false`. When explicitly enabled, overflow is stored as a bounded sanitized JSON artifact through existing retention, with path, SHA-256, bytes and media type. Credential echoes are scrubbed before previews/artifacts; sensitive binary output and credential-bearing stderr are withheld. Provider output schemas validate the original structured result internally; they are distinct from the public Core envelope schema. Resource links and embedded URIs are data and are never fetched automatically.

The schema subset rejects tuple-form `items` and bounds both syntax nodes and expanded local-reference work/depth before running the installed offline validator. References must point directly to named root `$defs` or `definitions`, using JSON Pointer/URI escaping; nested targets are unsupported. Namespace and decoded name remain distinct during existence, cycle and work checks. Acyclic references do not waive the complexity limit. Sanitization covers JSON/URL-escaped literals, decoded binary variants and numeric scalar echoes; provider SDK client/dispatcher payload logs are suppressed only within extension IO tasks.

## Opt-in and local administrator authority

`MCP_EXTENSIONS_ENABLED` defaults false. Missing config means zero extensions. With opt-in and invalid config, extension activation fails closed with safe diagnostics while native tools remain available. Import, `--check`, listing, route planning, health and panel polling never launch/discover providers.

The default config is `config_home() / 'computerpilot-mcp' / 'extensions.json'`. Reuse the platform helper's XDG/APPDATA/macOS precedence; `MCP_EXTENSIONS_CONFIG` may name only an absolute local file. The definition snapshot applies until runtime restart. There is no watcher, installer, OAuth flow, arbitrary-command MCP tool, remote trust editor or panel control API.

Only a local operator chooses and reviews an absolute executable, argument vector and working directory. Launch requires global opt-in, enabled definition, explicit `launch_approved`, appropriate platform, resolved references, and required-native ownership. Commands use `shell=False`, no interpolation or full parent environment. Disabled definitions still validate syntax/security but do not need an existing executable/cwd, matching platform or resolved references until activation.

Reviewed code runs with the OS user's privileges. Trust/environment filtering and Job/process-group ownership are not a sandbox; malicious local code can still access that user's files/network or attempt to escape ownership. Leave launch approval false if that residual risk is unacceptable.

## Definition and secret references

Schema version 1 is strict and rejects unknown keys, invalid types, duplicate IDs, unsafe identity/path/environment fields and excessive bytes/depth/counts before activation. Future versions are refused without rewriting the file. The disabled fake example below is a validation example; its placeholder binary must never be launched.

```json
{
  "schema_version": 1,
  "extensions": [
    {
      "extension_id": "fixture",
      "display_name": "Local test MCP",
      "provider": "fixture",
      "transport": "stdio",
      "command": "C:/absolute/path/to/python.exe",
      "arguments": ["C:/absolute/path/to/fake_mcp_server.py"],
      "working_directory": "C:/absolute/disposable/workspace",
      "enabled": false,
      "launch_approved": false,
      "trust_class": "restricted",
      "platforms": ["windows"],
      "environment": {
        "inherit": [],
        "values": {"PYTHONUNBUFFERED": "1"},
        "references": {"FIXTURE_TOKEN": {"kind": "env", "name": "FIXTURE_TOKEN"}}
      },
      "timeouts": {"startup_sec": 15, "handshake_sec": 5, "discovery_sec": 5, "call_sec": 30, "shutdown_sec": 10},
      "tools": {
        "read_value": {
          "remote_name": "read_value",
          "domain": "testing",
          "mutation_class": "read_only",
          "risk_class": "low",
          "profiles": ["testing", "full"],
          "intents": ["fixture.value.read"],
          "allow_call": true,
          "determinism": 4,
          "recoverability": "strong",
          "capability_confidence": 4,
          "cost_tier": 1,
          "latency_tier": 2,
          "required_env_refs": []
        }
      }
    }
  ]
}
```

Resolved credentials stay private to transport lifetime. Q04 freezes env-only references in M2.4; file references are deferred absent a demonstrated consumer and portable permission/link/size proof. Inherit only reviewed nonsecret launch-required variables. No wildcard inheritance, secret values in argv/nonsecret values, `PYTHONPATH`/`PYTHONHOME`, preload/injection variables or automatic PATH modifications. Missing active references yield `auth_missing`. Reference names/paths, argv/cwd and resolved values are not public health/audit data. Python does not guarantee credential-memory zeroization.

Offline publication in M13 validates the entire candidate without spawning or network I/O, checks expected old-file hash under the existing file lock, then writes a restrictive same-directory temp, fsyncs and atomically replaces. Failed validation/conflict/write preserves original bytes. No native database/state migration or hot reload is implied.

## Exact identity and classification

Public tools are `external.<extension_id>.<remote_tool_name>`, looked up by the full accepted map. The ID is lowercase ASCII `[a-z][a-z0-9_-]{0,31}`; remote names are exact case-sensitive ASCII `[A-Za-z0-9._-]+`. The composed name is at most 128 characters. Reject, never slugify/truncate/hash/remap invalid identity or shadow native names. Duplicates within/across pages reject the candidate generation. ID rename is removal plus addition; old uncertain operations keep their original identity.

Provider names, descriptions, schemas and hints are untrusted attributed data. Only operator-reviewed `ToolPolicy` selects domain, exact symbolic intents, allowed profiles, mutation class, risk/recoverability/scores and permission. Unknown classification is denied; `readOnlyHint`/idempotent hints or a successful connection do not authorize a tool.

| Trust | Consumption |
|---|---|
| untrusted | No launch or calls, even if enabled. |
| restricted | Approved, schema-valid, authenticated read-only tools only. |
| trusted | Explicit approved classifications; mutations additionally require `RUNTIME_LIFECYCLE` in `RUNNING`, an enabled recovery journal with unresolved capacity, and durable two-stage journal checkpoints. |
| built_in | Native-only; extensions cannot claim this class. |

Mutation classes are `read_only`, `local_mutation`, `remote_mutation`, `destructive_local_mutation`, `destructive_remote_mutation`, and `unknown`. Risk is a separate axis. Destructive calls need explicit destructive classification/allowance; no score or request flag elevates permission. `unknown` mutation classification is unconditionally denied across discovery, routing, and direct calls.

## Schema, catalogs and profiles

Accepted provider input schemas are advertised exactly, including names such as `file.name`; no native `extra=forbid`, coercion or default injection changes provider semantics. Core validates input/output with an explicit bounded keyword subset and no-network registry. Root input is an object. Standard bounded primitives/objects/arrays/constraints/enum/const and limited compositions are allowed; acyclic local `#/$defs/...` references require finite complexity. Unknown keywords, remote references, `$id`, dynamic/recursive references/anchors, `pattern` and `patternProperties` are rejected. Recognized `$schema` dialects do not permit network retrieval. Provider output schema is internal validation; public output schema is the Core envelope below.

Pagination has aggregate deadline, page/cursor-cycle/tool/byte limits. Complete candidates publish atomically. Invalid discovery cannot leave partially callable tools; prior stale/degraded snapshots are ineligible. Live visibility requires configured/enabled/approved/healthy/discovered/schema-valid/auth/platform/profile eligibility. Persistent discovery caches do not establish availability.

The first accepted input/output fingerprint for each public name is frozen for the whole Core runtime, including reconnects/removal/re-addition. Changed schemas are withheld with `schema_changed_restart_required` until Core restart. Identity tombstones have a quota; exhaustion denies new identity, never evicts protection. New connections must relist after Core restart. Cached schema reuse across different Core runtimes cannot be detected without a caller-generation protocol, which v0.8 does not introduce.

Native profile domains are unchanged. External tools require explicit approved active-profile membership. External `minimal` permits read-only only; native minimal mutations remain supported. `full` is not blanket external authorization. Discovery/recommendation separate native/external counts, bound attributed descriptions/results, and never recommend phantom tools.

The bridge injects a public `InMemorySubscriptionBus` for modern `ToolsListChanged` events and forwards legacy `send_tool_list_changed` notifications through public request middleware/session APIs. SDK session wrappers are transient; stable live initialize-parameter identity deduplicates retained legacy handles. At most 16 handles/deliveries are retained, with a one-second delivery deadline; overflow connections keep native tools and withhold external exposure. Failed handles are dropped and all handles are cleared at lifespan exit. Generation polling coalesces changes at 100 ms and never performs provider RPC. Real legacy stdio and modern stateless HTTP clients exercise the production entrypoint. Modern HTTP subscriptions invalidate cached catalogs on removal and restoration. Legacy stateless HTTP lacks a persistent initialized connection, so it retains all native tools and withholds external tools; use modern HTTP or legacy stdio for extensions. Production SDK response-cache hints remain TTL0. Actual transports prove notification-driven relisting; separate modern-client tests advertise a positive 60-second tools/list TTL through the public CacheHint constructor and prove stale cached contents before notification, then cache invalidation on withdrawal/restoration. No private SDK catalog/session/cache injection is used.

Provider notifications mark the old catalog degraded immediately, coalesce into one dirty slot, and run bounded refresh after 100 ms. Discovery caps pages at 128, detects repeated cursors, and checks aggregate tools/serialized bytes before retaining another page. Publication also budgets repeated Core output schemas and metadata before commit. Invalid refresh withdraws availability; changed schemas are withheld individually while runtime fingerprints remain intact. Catalog generations remain monotonic across refresh/removal/reconnect, independently of connection generations. Startup timeout honors the minimum caller/operator/Core limit, includes startup queue/transport time and ends after handshake/discovery; healthy keep-alive sessions do not expire with it. Recent state history is bounded to eight entries. The startup semaphore covers handshake/discovery only, with launch authority rechecked after queueing. STOP/RESTART interrupts the live owner or its restart backoff; process EOF withdraws the catalog and reconnects under the finite budget, never replaying an old call. Shutdown blocks new starts. Actual entrypoint import/--check never activates providers; stdio EOF reaps the owned provider.

The Router model accepts `external.mcp` with bounded provider/extension/generation attribution from operator policy. Native JSON keys and `deterministic-v1` remain unchanged; external determinism cannot exceed four, and a selected/supported external route requires a positive live catalog generation. Generation zero describes a rejected capability with no live catalog. The advisory router considers only exact operator intent mappings and uses the same live eligibility as direct calls before scoring. Configured but unavailable capabilities carry bounded rejections; credential identities are withheld. Unknown execution profiles, including diagnostic `all`, cannot grant calls or routes. Legacy native-only connections stay native-only in routing. Native workflow routing receives no extension manager. Selection and explanations share one candidate snapshot. Native determinism five ranks before external four; the existing recoverability/risk/confidence/cost/latency/tie ordering decides structured external versus semantic/raw routes without blanket native preference. External explanations identify operator estimates, do not copy provider descriptions and do not change `preferred_tool` semantics. Macro preservation remains a hard constraint.

## Transport and lifecycle gates

v0.9 outbound providers use local stdio only. Existing inbound stdio, streamable HTTP and Secure Tunnel remain supported. Outbound HTTP/SSE and external prompts/resources/sampling/elicitation/roots delegation are deferred or denied.

One manager per Core server and one connection owner task per provider enter/exit public SDK transport/session contexts in that same task. Core owns the sole native process tree: Windows suspended creation/Job assignment before resume; POSIX required groups checked before spawn. Required ownership failure cannot fall back or execute a second copy. Q01/M5 requires real three-platform child/grandchild/root-exit and bounded cancellation/write/thread cleanup proof before any activation.

The adapter bounds raw bytes before append/decode/parse, validates UTF-8 and structural depth, and uses finite channels/notification queues and sanitized stderr drains. SDK JSON-RPC/session negotiation is reused. An absolute call deadline includes admission and blocked writes; cleanup has its own measured bound. The public `send_request` API is chosen so Core owns safe schema validation rather than SDK automatic remote-ref validation. Multi-round `InputRequired`/claimed results are not accepted.

States are configured, disabled, starting, healthy, degraded, unreachable, failed and stopping. Healthy requires completed handshake and accepted discovery. Only healthy live snapshots are eligible. Bounded background startup keeps native readiness independent; slow A cannot block B. Reconnect has finite backoff/window attempts and creates a new session/generation without replaying any original call. Disable/remove closes admission, drains/cancels within bounds, persists uncertain outcomes, closes contexts and reaps owned trees. Internal test/lifecycle controls do not write operator config or expose public administration.

Calls take finite global/per-provider admission, capture immutable generation/schema/policy leases, and recheck immediately before dispatch. Local catalog locks and journal transactions never span network awaits. Refresh preserves bounded in-flight leases but cannot grant stale queued calls permission.

## Advisory Router

The existing Router may add `external.mcp` candidates for exact reviewed symbolic intents. Hard eligibility gates run before ranking and match direct-call guards. The native `deterministic-v1` lexicographic key remains unchanged; external determinism is at most four. Strong native routes can rank ahead, but universal native-first ties are not promised. Observed latency is diagnostic, not a jitter-driven score override.

For example, reviewed mapping `github.issue.create` may recommend `external.github.create_issue` after the tool is actually discovered/allowed. The caller then invokes that typed tool separately. Natural language prose cannot create authority. Recommendation does not dispatch, grant permission, execute a browser fallback, or add arbitrary external workflow actions.

`execution_recommend` and `execution_candidates` project one eligibility snapshot per request. External results attribute `source_type`, `extension_id`, `provider`, `catalog_generation` and `classification_source="operator_policy"`; their confidence/cost/latency are reviewed estimates. For a discovered approved read-only tool mapped to `operator.read`, invoke `execution_recommend` with `{"intent":"operator.read"}` and then call the returned typed tool separately. A stale, denied or unavailable candidate is explained but cannot be selected.

`recommend_tools` attaches the same operator execution metadata only while that listed generation remains eligible. Router metrics aggregate `external.mcp` under `usage.external`; provider identities never become metric labels. With no external decisions, native metric keys stay unchanged. Visual desktop routing remains unavailable until v0.9.0, and workflow execution remains limited to its existing native typed action allowlist.

## Core result and output contract

Every approved external tool advertises the Core `ExtensionResult` envelope, with:

```text
ok, source_type='external_mcp', extension_id, provider, tool,
catalog_generation, operation_id,
outcome='succeeded'|'not_dispatched'|'uncertain'|'failed',
content, structured_content, truncated,
limits={returned_bytes, original_bytes_if_known, item_limit, depth_limit},
artifact, error, message
```

Core input byte/depth/collection limits and provider-schema validation precede dispatch. Wire limits precede result parsing. Sanitized normalized text/structured/binary/resource blocks have hard inline/item/depth limits, explicit truncation and optional bounded artifacts in the existing retention system. Unsupported/malformed/oversized results fail safely. No URI auto-fetch or external resource/file opening. Native legacy-inline defaults are preserved.

Artifact metadata includes safe path, bytes, media type and content hash; active-use protection and retention remain mandatory. Authorized business data may be returned but is not logged as audit payload. Credential literals are filtered from all result/error/stderr/log/health/panel/artifact sinks, including nested innocuous-key text and split multibyte chunks.

Errors are short Core-owned codes, not provider exception repr/error.data. Examples: `config_invalid`, `ownership_unavailable`, `handshake_timeout`, `schema_unsupported`, `catalog_limit`, `stale_catalog`, `profile_denied`, `trust_denied`, `auth_missing`, `call_timeout`, `connection_closed`, `provider_error`, `output_too_large`, `recovery_unavailable`, `operation_limit`, `uncertain_side_effect`. Attribution/correlation may be returned; secret/config details may not.

## Mutation durability and manual resolution

M10/B03 enforces durable dispatch barriers, interprocess CAS resolution, unresolved quota bounds, and two-runtime restart proofs before external mutations can execute. Approved trusted mutation uses the existing schema-1 operation journal only:

```text
validate + admit + recheck lease
-> writable managed journal and atomic quota admission (max 128 unresolved external ops)
-> fsync begin(prepared)
-> fsync checkpoint(may_dispatch), before transport handoff
-> dispatch outside storage lock
-> conclusive validated success: safe receipt + durable finish
-> ambiguous possible-dispatch outcome: durable uncertain, no replay
```

Failures before possible dispatch can finish `not_dispatched` as FAILED, persisting the legacy `known_result='returned_error'` plus additive `external_outcome='not_dispatched'`. After `may_dispatch`, provider `isError`, timeout, cancellation, disconnect, malformed/oversized/invalid output or lost terminal evidence conservatively stay uncertain. A journal/checkpoint failure before send denies the effect; failure after effect retains `prepared`/`may_dispatch` evidence, marks the extension degraded, and reports uncertainty. No automatic mutation retry/reroute, even with idempotent hints.

Safe versioned metadata (`external_metadata_version=1`) includes operation/tool identity, policy/config/schema/generation and stage, never raw args/credentials/full payload. Request correlation uses runtime-only domain-separated HMAC/key ID; it is not cross-restart deduplication or proof of provider idempotency. Keep journal version 1, native workflow schema 6 and jobs schema 3.

Atomic read/check/append acknowledgement/reconciliation and quota share the existing interprocess lock. Unresolved operations are never evicted; full admission quota (`128`) denies new mutation with `operation_limit` before dispatch. Only terminal extension history is compacted within bounded limits (`1,000` resolved operations / `30` days), preserving native history and all unresolved records. Disable/remove/rename/restart do not resolve or replay old effects. Provider-specific automatic receipt probing is deferred; existing recovery inspection (`list_uncertain_operations`, `inspect_uncertain_operation`, `get_operation_history`) and explicit operator acknowledgement (`acknowledge_uncertain_operation`) provide manual resolution. Before downgrade export needed terminal evidence; preserve unresolved records and keep extension admission off.

Public read-only/destructive annotations come from operator mutation policy. Prepared metadata records a policy fingerprint without environment references. External admission and per-operation updates stream bounded journal lines and retain only their relevant state; legacy native history and compaction still have their existing total-cost limitations. Journal writes run in non-abandoned workers, with pre-dispatch file-lock waits capped by the remaining call deadline. A busy journal denies mutation capacity while read-only calls stay independent; disk I/O completion and post-effect durable uncertainty use their storage/cleanup budget.

## Observability, budgets and reproducible acceptance

The extension health projection reads cached owner/catalog authority only, with coherent discovered/approved/eligible/live counts, generation, UTC state/success/error timestamps and monotonic ages. It makes no provider RPC or spawn. Disabled/policy-denied definitions are neutral. Rows are capped at 16 and the extension projection at 32 KiB; the complete native/extension runtime snapshot is capped at 512 KiB with explicit truncation and safe summary fallback. The existing atomic heartbeat publishes every 10 seconds. Supervisor reads at most the ceiling plus one byte and requires schema 1, the current runtime generation and monotonic age between 0 and 30 seconds. Missing/corrupt/oversized snapshots are unavailable; stale or prior-generation snapshots suppress eligible/live/routable claims. Legacy native snapshots remain unverified without extension authority.

Each owner retains its last 64 call outcomes and durations: fixed succeeded/failed/uncertain/not-dispatched counters, mean and maximum duration, without per-provider dynamic labels. These are rolling observations, not authoritative unresolved-operation counts; uncertainty still comes from the existing recovery journal. External-call audit records contain only sanitized identity, generation, mutation class, finite error code and duration. The existing bounded queue, rotation and retention apply. Arguments, output, environment references, command lines, schemas and exception text are excluded. Timing records retain the existing metadata-only format.

Every normal server startup independently reads and validates opt-in extension configuration, regardless of a successful native Full Doctor fingerprint cache. Invalid/future configuration closes extension activation and exposes only `config_invalid` or `config_version_unsupported`; native tools remain available and aggregate health is degraded. Missing/empty configuration is neutral, and disabled mode does not open the configuration or launch providers. Health reads the cached diagnostic rather than reopening the configuration.

The complete native collector is not a pure memory read: its existing worker-thread path walks files, samples process resources, acquires storage locks and queries recovery/workflows. First workflow-store acquisition creates directories and initializes the writable SQLite schema. Publication size limits do not bound those existing acquisition costs or the temporary full native payload. No new health database or migration was added. Authenticated insights use the shared runtime snapshot; legacy unauthenticated summary/status expose only their existing aggregate fields. The authenticated read-only `GET /api/extensions` endpoint and English panel extension view consume the cached runtime-health projection without provider RPC.

Core extension ceilings are defined in [ExtensionLimits](../../core/extensions/models.py); the [v0.8.0 validation record](../V0.8.0-VALIDATION.md) records the completed resource-budget and performance gates. Provider/caller limits may tighten but cannot raise Core ceilings. Follow the [testing standards](TESTING-STANDARDS.md) for reproducible acceptance evidence; extension publication limits do not imply a bound on the existing native acquisition costs described above.

Use the existing [local fake provider](../../tests/fixtures/fake_mcp_server.py) with absolute `sys.executable`, isolated config/env/journal, SDK handshake/pages/read/mutation, durable effect markers, and controlled malformed/flood/crash/lost-response modes. Assertions include denied marker0, lost-result marker1 across two runtimes, schema no-network, exact advertised inputs/Core outputs, native zero-extension profiles, live generation changes, no orphan tasks/threads/trees, canary-free sinks and authenticated panel rendering. Tests and independent reviewers report actual command/SHA/results; public-provider CI is prohibited.
