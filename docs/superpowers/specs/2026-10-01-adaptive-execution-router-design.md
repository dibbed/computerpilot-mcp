# Adaptive Execution Router Design

Date: 2026-10-01

Status: Approved for implementation

## Purpose

Introduce the v0.7.0 Adaptive Execution Router as a deterministic, explainable decision layer that recommends the safest and strongest available execution mechanism for a structured intent without becoming a universal action dispatcher.

The router must prefer structured native tools over semantic UI paths, semantic paths over raw coordinate automation, and fail closed when a route is unsupported, ambiguous, stale, or incompatible with the requested fidelity/safety constraints.

## Current Verified Baseline

The v0.6.0 main branch provides:

- typed MCP tools across filesystem, code/LSP, Git, process/system, browser, Windows UIA, raw desktop, document, recovery, job, and workflow domains;
- semantic browser operations introduced in v0.5.0;
- native Excel, DOCX, and PDF operations introduced in v0.6.0;
- explicit platform capability detection;
- mutation recovery and postcondition journaling;
- durable workflow operations with exact execution definitions, operation checkpoints, reconciliation, and fail-closed uncertain states;
- `recommend_tools` keyword scoring over the registered catalog and `PREFERRED_USE` guidance;
- server health and a local control panel.

The router extends these systems. It does not replace typed tools, workflow execution, or recovery.

## Non-Goals

v0.7.0 will not add:

- a public `execution_run` or arbitrary `execute_anything` tool;
- opaque ML routing or model-based route scoring;
- screenshot/vision routing, which belongs to v0.8.0;
- automatic destructive raw-coordinate fallback;
- a second independent tool catalog;
- hidden retries of uncertain side effects;
- a new background service or external dependency.

## Design Principles

### One catalog, multiple consumers

Tool selection metadata must live in one shared execution catalog used by:

- router candidate generation;
- `recommend_tools` enrichment;
- workflow route assertions where applicable;
- health/observability labels.

Registered MCP tools remain the authority for whether a tool is actually available in the current profile. Catalog metadata cannot make an unregistered tool available.

### Pure decision layer

Candidate generation and selection are side-effect free. They must not:

- take screenshots;
- launch applications;
- open browser pages;
- mutate files;
- execute commands;
- probe arbitrary external systems.

Inputs are structured request facts plus registered tool names and platform capabilities. Outputs are bounded route candidates and an explainable decision.

### Deterministic policy

The initial policy is `deterministic-v1`.

Hard constraints run before ranking. Valid candidates are then ordered lexicographically by:

1. higher determinism;
2. stronger recoverability;
3. lower side-effect risk;
4. higher capability confidence;
5. lower cost tier;
6. lower latency tier;
7. stable route id and representative tool tie-break.

This avoids permanent opaque weights while keeping the policy testable and benchmark-tunable later.

## Route Model

Initial route classes:

- `native.filesystem`
- `native.code`
- `native.git`
- `native.process`
- `native.system`
- `native.excel`
- `native.docx`
- `native.pdf`
- `semantic.browser`
- `semantic.windows_uia`
- `visual.desktop` reserved as unavailable until v0.8.0
- `raw.desktop`

Each candidate contains bounded metadata:

- route id;
- representative tool;
- determinism class 1-5;
- risk class;
- recoverability class;
- capability confidence;
- cost tier;
- latency tier;
- fallback level;
- capability checks;
- supported/invalid state and reason.

## Intent Contract

The public router accepts a structured intent name, not free-form natural language classification.

Initial intent families include:

- `filesystem.read`
- `filesystem.write`
- `code.rename`
- `git.read`
- `git.write`
- `process.run`
- `system.inspect`
- `document.excel.read`
- `document.excel.write`
- `document.docx.read`
- `document.docx.write`
- `document.pdf.read`
- `document.pdf.create`
- `browser.interact`
- `desktop.semantic.interact`
- `desktop.raw.interact`

Request constraints may include:

- `destructive`;
- `semantic_ambiguous`;
- `semantic_stale`;
- `requires_macro_preservation`;
- `allow_raw_desktop`;
- `preferred_tool`.

Unknown intents fail explicitly rather than silently mapping to a broad route.

## Shared Execution Catalog

Add a focused catalog module that defines:

- metadata for important registered tools;
- intent-to-route candidate rules;
- route capability requirements;
- route quality properties.

Examples:

- `read_file` -> `native.filesystem`;
- `rename_symbol` -> `native.code`;
- `git_status` -> `native.git`;
- `run_process` -> `native.process`;
- `excel_read_range` / `excel_write_range` -> `native.excel`;
- `docx_read` / `docx_replace_text` -> `native.docx`;
- `pdf_extract_text` / `pdf_create_from_text` -> `native.pdf`;
- semantic browser tools -> `semantic.browser`;
- UIA tools -> `semantic.windows_uia`;
- `mouse_click`, `keyboard_type`, `hotkey` -> `raw.desktop`.

Fallback candidates are only valid when the enabling tools and platform capabilities are present.

## Safety Constraints

Hard constraints override ranking.

The router must reject or exclude:

- routes whose required tools are not registered;
- platform-incompatible routes;
- `visual.desktop` in v0.7.0;
- raw desktop routes unless `allow_raw_desktop=true`;
- raw desktop fallback for destructive requests;
- semantic routes for destructive requests when `semantic_ambiguous=true`;
- stale semantic references instead of silently falling back to coordinates;
- document mutation routes that cannot satisfy required macro preservation.

If no valid route remains, `execution_recommend` returns a structured no-route result or a stable tool error without executing anything.

## Public API

### `execution_candidates`

Read-only diagnostic tool.

Returns:

- normalized intent;
- policy version;
- bounded candidate list;
- valid candidate count;
- rejected candidate count;
- capability checks;
- truncation information.

Default maximum candidates is small and hard-capped.

### `execution_recommend`

Read-only recommendation tool.

Returns:

- `selected_route`;
- representative tool;
- candidate count;
- `selection_reason`;
- `fallback_level`;
- capability checks;
- risk class;
- `router_policy_version`;
- optional bounded candidate details when explain mode is enabled.

It never executes the selected route.

## `recommend_tools` Integration

Keep the existing keyword recommendation behavior and ordering for compatibility, but enrich matched tools with execution metadata when available:

- route;
- determinism;
- recoverability;
- risk class.

This avoids a second model-facing discovery system and keeps route guidance attached to the actual registered catalog.

## Configuration

Add:

- `MCP_EXECUTION_ROUTER_ENABLED`, default true;
- `MCP_EXECUTION_ROUTER_POLICY`, default `deterministic-v1`;
- `MCP_EXECUTION_ROUTER_EXPLAIN`, default true.

Unsupported policy names fail closed when router APIs are invoked.

The router can be disabled without preventing the MCP server from starting.

## Workflow Integration

Extend `StepDefinition` with an optional bounded `execution_intent` dictionary.

When a workflow step declares an execution intent:

1. before the operation side effect begins, the executor requests a route decision;
2. the selected route and policy version are persisted on the materialized workflow operation;
3. the selected route must match the fixed allowlisted workflow action's execution route;
4. if capability state changes before a later retry and the selected route changes safely, the change is appended to bounded fallback history;
5. if the operation becomes uncertain, existing reconciliation rules remain authoritative and the operation is never rerouted and replayed blindly.

Workflows without `execution_intent` remain behaviorally unchanged.

## Workflow Persistence

Bump the workflow schema by one migration and add bounded route fields to `workflow_operations`:

- execution intent JSON;
- selected route;
- router policy version;
- route decision JSON;
- fallback history JSON.

Public workflow operation responses expose redacted/bounded route metadata.

No raw prompt text, document body, screenshot, credential, or secret is stored in route metadata.

## Metrics and Health

Add a lightweight in-process router metrics collector with:

- decision count;
- no-route/rejection count;
- fallback count;
- selected route distribution;
- native/semantic/raw usage;
- policy version;
- bounded recent decisions;
- decision latency summary.

`server_health` exposes a bounded `execution_router` section.

Metrics collection must use constant/bounded memory and must not block the event loop with I/O.

## Control Panel

Add an English-only Execution Routing section showing:

- enabled/disabled status;
- policy version;
- total decisions;
- fallback rate/count;
- no-route/rejection count;
- native vs semantic vs raw usage;
- selected-route distribution;
- bounded recent route decisions.

The panel must not expose sensitive intent payloads.

## Failure Semantics

Stable router errors/results include:

- router disabled;
- unknown intent;
- unsupported policy;
- no valid route;
- destructive ambiguous semantic target;
- stale semantic reference;
- unsupported fidelity requirement.

Candidate rejection reasons remain bounded and machine-readable.

## Testing Strategy

Use TDD for every behavior change.

Deterministic selection tests must prove:

- native Excel beats Windows UI/raw desktop;
- LSP rename beats editor/UI typing;
- direct filesystem read beats desktop fallback;
- semantic browser beats raw desktop;
- unsupported platform routes are excluded;
- stable inputs produce byte-for-byte stable decision fields where timestamps are not involved.

Safety tests must prove:

- destructive ambiguous semantic actions do not auto-select a semantic target;
- stale semantic references do not fall back to coordinates;
- raw desktop requires explicit permission and is never auto-selected for destructive requests;
- macro-preservation constraints reject unsupported routes;
- no candidate generation takes a screenshot or performs a side effect.

Workflow tests must prove:

- route decision persists before execution;
- capability-loss route changes append fallback history;
- selected route must match the fixed action route;
- uncertain operations are not rerouted/replayed.

Performance/health tests must prove:

- candidate generation is bounded;
- health collection remains non-blocking;
- router metrics are bounded;
- current tool/profile health remains intact.

## Acceptance Criteria

v0.7.0 implementation is complete when:

- recommendations are deterministic and explainable;
- common intents prefer structured native tools over GUI paths;
- platform and profile capability changes safely remove invalid routes;
- workflow route metadata is durable and versioned;
- uncertain side effects still require reconciliation;
- no universal unsafe dispatcher exists;
- router policy version and fallback metadata are observable;
- the control panel exposes bounded routing diagnostics;
- existing typed tools and workflows remain compatible;
- full local and hosted CI/platform validation pass.
