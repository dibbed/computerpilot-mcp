# External MCP extension authoring

Status: **PROPOSED v0.8 public contract; public exposure and mutation activation remain gated.** Follow the [agent contract](../../AGENTS.md) for the brief verified handoff and next task, and inspect current source and CI for evidence. The full master plan remains only in the operator's Windows Downloads directory. This guide is the operator/provider contract, not a claim of shipping support. [Architecture](../architecture.md), [configuration](../configuration.md), [testing standards](TESTING-STANDARDS.md), and [release process](../release-process.md) remain their existing authorities.

## Opt-in and local administrator authority

`MCP_EXTENSIONS_ENABLED` defaults false. Missing config means zero extensions. With opt-in and invalid config, extension activation fails closed with safe diagnostics while native tools remain available. Import, `--check`, listing, route planning, health and panel polling never launch/discover providers.

The proposed default config is `config_home() / 'computerpilot-mcp' / 'extensions.json'`. Reuse the platform helper's XDG/APPDATA/macOS precedence; `MCP_EXTENSIONS_CONFIG` may name only an absolute local file. The definition snapshot applies until runtime restart. There is no watcher, installer, OAuth flow, arbitrary-command MCP tool, remote trust editor or panel control API.

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
| trusted | Explicit approved classifications; mutations additionally require verified M10 and managed durable recovery. |
| built_in | Native-only; extensions cannot claim this class. |

Mutation classes are `read_only`, `local_mutation`, `remote_mutation`, `destructive_local_mutation`, `destructive_remote_mutation`, and `unknown`. Risk is a separate axis. Destructive calls need explicit destructive classification/allowance; no score or request flag elevates permission. Before M10 all external mutations are withheld from discovery/routing and rejected on direct calls.

## Schema, catalogs and profiles

Accepted provider input schemas are advertised exactly, including names such as `file.name`; no native `extra=forbid`, coercion or default injection changes provider semantics. Core validates input/output with an explicit bounded keyword subset and no-network registry. Root input is an object. Standard bounded primitives/objects/arrays/constraints/enum/const and limited compositions are allowed; acyclic local `#/$defs/...` references require finite complexity. Unknown keywords, remote references, `$id`, dynamic/recursive references/anchors, `pattern` and `patternProperties` are rejected. Recognized `$schema` dialects do not permit network retrieval. Provider output schema is internal validation; public output schema is the Core envelope below.

Pagination has aggregate deadline, page/cursor-cycle/tool/byte limits. Complete candidates publish atomically. Invalid discovery cannot leave partially callable tools; prior stale/degraded snapshots are ineligible. Live visibility requires configured/enabled/approved/healthy/discovered/schema-valid/auth/platform/profile eligibility. Persistent discovery caches do not establish availability.

The first accepted input/output fingerprint for each public name is frozen for the whole Core runtime, including reconnects/removal/re-addition. Changed schemas are withheld with `schema_changed_restart_required` until Core restart. Identity tombstones have a quota; exhaustion denies new identity, never evicts protection. New connections must relist after Core restart. Cached schema reuse across different Core runtimes cannot be detected without a caller-generation protocol, which v0.8 does not introduce.

Native profile domains are unchanged. External tools require explicit approved active-profile membership. External `minimal` permits read-only only; native minimal mutations remain supported. `full` is not blanket external authorization. Discovery/recommendation separate native/external counts, bound attributed descriptions/results, and never recommend phantom tools.

Q03 remains OPEN to M8.3: modern `ToolsListChanged` through public subscriptions and legacy list-change notification require wire tests. If a public legacy notification seam is unavailable, freeze that connection's catalog and document reconnect/relist, while preserving live pre-send guards. No private SDK catalog/session injection is permitted.

## Transport and lifecycle gates

v0.8 outbound providers use local stdio only. Existing inbound stdio, streamable HTTP and Secure Tunnel remain supported. Outbound HTTP/SSE and external prompts/resources/sampling/elicitation/roots delegation are deferred or denied.

One manager per Core server and one connection owner task per provider enter/exit public SDK transport/session contexts in that same task. Core owns the sole native process tree: Windows suspended creation/Job assignment before resume; POSIX required groups checked before spawn. Required ownership failure cannot fall back or execute a second copy. Q01/M5 requires real three-platform child/grandchild/root-exit and bounded cancellation/write/thread cleanup proof before any activation.

The adapter bounds raw bytes before append/decode/parse, validates UTF-8 and structural depth, and uses finite channels/notification queues and sanitized stderr drains. SDK JSON-RPC/session negotiation is reused. An absolute call deadline includes admission and blocked writes; cleanup has its own measured bound. The public `send_request` API is chosen so Core owns safe schema validation rather than SDK automatic remote-ref validation. Multi-round `InputRequired`/claimed results are not accepted.

States are configured, disabled, starting, healthy, degraded, unreachable, failed and stopping. Healthy requires completed handshake and accepted discovery. Only healthy live snapshots are eligible. Bounded background startup keeps native readiness independent; slow A cannot block B. Reconnect has finite backoff/window attempts and creates a new session/generation without replaying any original call. Disable/remove closes admission, drains/cancels within bounds, persists uncertain outcomes, closes contexts and reaps owned trees. Internal test/lifecycle controls do not write operator config or expose public administration.

Calls take finite global/per-provider admission, capture immutable generation/schema/policy leases, and recheck immediately before dispatch. Local catalog locks and journal transactions never span network awaits. Refresh preserves bounded in-flight leases but cannot grant stale queued calls permission.

## Advisory Router

The existing Router may add `external.mcp` candidates for exact reviewed symbolic intents. Hard eligibility gates run before ranking and match direct-call guards. The native `deterministic-v1` lexicographic key remains unchanged; external determinism is at most four. Strong native routes can rank ahead, but universal native-first ties are not promised. Observed latency is diagnostic, not a jitter-driven score override.

For example, reviewed mapping `github.issue.create` may recommend `external.github.create_issue` after the tool is actually discovered/allowed. The caller then invokes that typed tool separately. Natural language prose cannot create authority. Recommendation does not dispatch, grant permission, execute a browser fallback, or add arbitrary external workflow actions.

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

M10/B03 requires independent lost-response/restart/CAS review before activation. Approved trusted mutation uses the existing schema-1 operation journal only:

```text
validate + admit + recheck lease
-> writable managed journal and atomic quota admission
-> fsync begin(prepared)
-> fsync checkpoint(may_dispatch), before transport handoff
-> dispatch outside storage lock
-> conclusive validated success: safe receipt + durable finish
-> ambiguous possible-dispatch outcome: durable uncertain, no replay
```

Failures before possible dispatch can finish `not_dispatched` as FAILED, persisting the legacy `known_result='returned_error'` plus additive `external_outcome='not_dispatched'`. After may_dispatch, provider `isError`, timeout, cancellation, disconnect, malformed/oversized/invalid output or lost terminal evidence conservatively stay uncertain. A journal/checkpoint failure before send denies the effect; failure after effect retains prepared/may_dispatch evidence and reports uncertainty. No automatic mutation retry/reroute, even with idempotent hints.

Safe versioned metadata includes operation/tool identity, policy/config/schema/generation and stage, never raw args/credentials/full payload. Request correlation uses runtime-only domain-separated HMAC/key ID; it is not cross-restart deduplication or proof of provider idempotency. Keep journal version 1, native workflow schema 6 and jobs schema 3.

Atomic read/check/append acknowledgement/reconciliation and quota share the existing interprocess lock. Unresolved operations are never evicted; full admission quota denies new mutation. Only terminal extension history may be compacted within measured bounds, preserving native history. Disable/remove/rename/restart do not resolve or replay old effects. Provider-specific automatic receipt probing is deferred; existing recovery inspection and explicit operator acknowledgement provide manual resolution. Before downgrade export needed terminal evidence; preserve unresolved records and keep extension admission off.

## Observability, budgets and reproducible acceptance

Health/panel read one bounded in-memory snapshot with counts/generation/observation age and explicit stale/unavailable state; they make no provider RPC/spawn. Disabled/policy-denied is ordinary state, not synthetic failure. Native availability stays independent. Authenticated `GET /api/extensions` supplies safe details; legacy unauthenticated summary/status never gain IDs, errors, config/ref/path/schema/generation detail. The English panel is observation-only, using textContent and existing bounded polling/hidden-tab pause. No new persistent health store.

The roadmap section35 is the single provisional budget table. Q02/M14 freezes measured config/schema/frame/input/catalog/output/artifact/call/startup/notification/restart/health/journal limits using 0/1/N and sustained flood/cleanup evidence. Provider/caller limits may tighten but cannot raise Core ceilings. No theoretical boundedness or three-platform pass is claimed here.

Use the planned local fake provider with absolute `sys.executable`, isolated config/env/journal, SDK handshake/pages/read/mutation, durable effect markers, and controlled malformed/flood/crash/lost-response modes. Assertions include denied marker0, lost-result marker1 across two runtimes, schema no-network, exact advertised inputs/Core outputs, native zero-extension profiles, live generation changes, no orphan tasks/threads/trees, canary-free sinks and authenticated panel rendering. Tests and independent reviewers report actual command/SHA/results; public-provider CI is prohibited.
