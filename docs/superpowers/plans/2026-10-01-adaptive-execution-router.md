# Adaptive Execution Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, explainable execution router that prefers structured native capabilities, rejects unsafe fallbacks, persists route decisions in durable workflows, and exposes bounded routing observability without adding a universal dispatcher.

**Architecture:** Add focused execution models, a shared tool/route catalog, and a pure deterministic router in `core`. Register only read-only diagnostic router tools, enrich existing `recommend_tools`, integrate optional structured execution intents into the existing workflow engine and schema, and expose bounded in-memory metrics through health and the existing control panel.

**Tech Stack:** Python 3.10+, MCP Python SDK 2.0, Pydantic 2, stdlib dataclasses/enums/threading/collections, SQLite workflow migrations, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-10-01-adaptive-execution-router-design.md`

## Global Constraints

- Keep `deterministic-v1` as the only initial routing policy.
- Do not add `execution_run`, arbitrary execution, model inference, screenshots, or external dependencies.
- Registered MCP tools and current platform/profile capabilities remain the authority for route availability.
- Hard safety constraints run before ranking and cannot be overridden by scores.
- Raw desktop requires explicit permission and cannot be selected automatically for destructive requests.
- Stale or ambiguous semantic targets must fail closed rather than fall back to coordinates.
- Workflow uncertainty remains reconciliation-only; route selection must never blindly replay an uncertain side effect.
- Route telemetry must be bounded and must not contain raw prompts, document bodies, screenshots, credentials, or secrets.
- Existing workflow definitions without execution intents must remain behaviorally compatible.
- Existing `recommend_tools` keyword ordering must remain compatible.
- Support Python 3.10 through 3.12 and current Windows/Linux/macOS release gates.

## Review Focus

- A profile that omits a native tool must not receive a candidate for that tool merely because metadata exists; pin in Task 2.
- Destructive semantic ambiguity and stale semantic references must not degrade to raw coordinates; pin in Task 3.
- An unknown or disabled router must fail with stable structured errors without affecting server startup; pin in Task 3.
- Workflow route metadata migration must preserve legacy rows and keep old workflows readable/executable according to existing compatibility rules; pin in Task 4.
- A workflow operation that becomes uncertain must not invoke the router again during reconciliation or resume; pin in Task 5.

---

### Task 1: Execution Models and Shared Catalog

**Files:**
- Create: `core/execution_models.py`
- Create: `core/tool_catalog.py`
- Test: `tests/test_execution_catalog.py`

**Interfaces:**
- Produces `RouteClass`, `RiskClass`, `RecoverabilityClass`, `ExecutionIntent`, `RouteCandidate`, and `RouteDecision`.
- Produces `tool_execution_metadata(name: str) -> ToolExecutionMetadata | None`.
- Produces `intent_route_rules(intent: str) -> tuple[IntentRouteRule, ...]`.
- Produces `route_for_tool(name: str) -> str | None` for workflow action compatibility checks.

- [ ] **Step 1: Write failing catalog/model tests**

Assert stable enum values, immutable serializable models, exact metadata for representative tools, and explicit unknown intent behavior.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_execution_catalog.py -q
```

Expected: FAIL because the execution model and catalog modules do not exist.

- [ ] **Step 2: Implement minimal execution models**

Create frozen dataclasses/enums with bounded primitive fields. Keep public conversion helpers deterministic and free of timestamps.

- [ ] **Step 3: Implement shared tool metadata**

Add representative metadata for filesystem, LSP/code, Git, process, system, Excel, DOCX, PDF, semantic browser, Windows UIA, and raw desktop tools. Keep visual desktop reserved but unavailable.

- [ ] **Step 4: Implement intent route rules**

Map each approved structured intent to ordered route possibilities and representative tool requirements. Rules may reference multiple enabling tools but never invent availability.

- [ ] **Step 5: Run focused tests and static checks**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_execution_catalog.py -q
.\.venv\Scripts\python.exe -m ruff check core/execution_models.py core/tool_catalog.py tests/test_execution_catalog.py
.\.venv\Scripts\python.exe -m mypy core/execution_models.py core/tool_catalog.py tests/test_execution_catalog.py
```

- [ ] **Step 6: Commit**

```text
feat(router): add execution route model and shared catalog
```

### Task 2: Candidate Generation and Deterministic Policy

**Files:**
- Create: `core/execution_router.py`
- Create: `core/router_metrics.py`
- Test: `tests/test_execution_router.py`

**Interfaces:**
- Consumes `ExecutionIntent`, catalog rules, registered tool names, and `PlatformCapabilities`.
- Produces `ExecutionRouter.candidates(intent, max_candidates=...) -> dict[str, Any]`.
- Produces `ExecutionRouter.recommend(intent, explain=...) -> dict[str, Any]`.
- Produces `ROUTER_POLICY_VERSION = "deterministic-v1"`.
- Produces bounded in-memory `ROUTER_METRICS.snapshot()`.

- [ ] **Step 1: Write failing preference tests**

Pin:
- Excel native > UIA > raw;
- rename_symbol/native.code > UI/raw typing;
- read_file/native.filesystem > desktop fallback;
- semantic browser > raw desktop;
- stable identical inputs -> identical decision fields.

Expected initial failure: router module missing.

- [ ] **Step 2: Write failing capability/profile tests**

Pass explicit registered-tool sets and synthetic Windows/Linux/macOS capabilities. Assert unregistered and platform-invalid candidates are rejected and candidate counts are bounded.

- [ ] **Step 3: Implement pure candidate generation**

No I/O, screenshots, subprocesses, browser calls, or filesystem probes. Availability comes only from supplied registered names and supplied capability object.

- [ ] **Step 4: Implement deterministic-v1 ranking**

Use lexicographic ordering:
`-determinism, -recoverability, risk, -confidence, cost, latency, route, representative_tool`.

Document exact numeric rank maps in the module so ordering is inspectable.

- [ ] **Step 5: Implement bounded router metrics**

Use a lock and bounded deque. Record only intent name, selected route, fallback level, policy version, candidate count, rejection/no-route status, and elapsed microseconds/milliseconds. Never retain request payloads.

- [ ] **Step 6: Run focused tests/static checks and commit**

```text
feat(router): derive and rank execution candidates deterministically
```

### Task 3: Hard Safety Constraints, Configuration, and Public Diagnostics

**Files:**
- Modify: `core/config.py`
- Modify: `core/registry.py`
- Modify: `core/tool_profiles.py`
- Modify: `scripts/panel_data.py` config snapshot only
- Test: `tests/test_execution_router.py`
- Modify: `tests/test_tool_profiles.py`
- Modify: `tests/test_mcp_integration.py`

**Interfaces:**
- Adds settings `execution_router_enabled`, `execution_router_policy`, `execution_router_explain`.
- Adds read-only MCP tools `execution_candidates` and `execution_recommend`.
- Enriches `recommend_tools` items with optional execution metadata without changing keyword scoring order.

- [ ] **Step 1: Write failing safety tests**

Pin:
- raw desktop rejected unless explicitly allowed;
- destructive raw desktop always rejected;
- destructive + semantic ambiguity rejects semantic candidates;
- stale semantic refs reject semantic and raw fallback;
- macro-preservation request excludes routes that cannot preserve macros;
- visual.desktop unavailable in v0.7.

- [ ] **Step 2: Implement hard constraints before ranking**

Candidate objects retain machine-readable rejection reasons. Recommendation considers only valid candidates.

- [ ] **Step 3: Write failing configuration/API tests**

Test enabled/disabled behavior, unsupported policy behavior, explain on/off shape, candidate hard cap, unknown intent, and registration across named profiles.

- [ ] **Step 4: Register diagnostic tools**

Instantiate router from current server tool names/capabilities inside the tool call or through a small helper that refreshes registered names. Do not cache stale tool lists across profile/server instances.

- [ ] **Step 5: Enrich `recommend_tools`**

Keep current score and sort logic unchanged. Add `execution` metadata only when the shared catalog recognizes the tool.

- [ ] **Step 6: Update hardcoded catalog counts**

Windows full profile count increases from 148 to 150. Update only assertions representing the live catalog, not historical fixture counts.

- [ ] **Step 7: Verify and commit**

```text
feat(router): enforce safety and expose route diagnostics
```

### Task 4: Workflow Schema and Durable Route Metadata

**Files:**
- Modify: `core/workflow_models.py`
- Modify: `core/workflow_store.py`
- Modify: `tools/workflows/registry.py`
- Modify: `tests/test_workflow_migrations.py`
- Create: `tests/test_workflow_routing.py`

**Interfaces:**
- Extends `StepDefinition` and workflow `StepInput` with optional bounded `execution_intent: dict[str, Any] | None`.
- Bumps workflow schema from 5 to 6.
- Adds operation columns for execution intent, selected route, policy version, route decision, and fallback history.
- Adds `WorkflowStore.record_route_decision(...)`.
- Public operation responses expose bounded/redacted route fields.

- [ ] **Step 1: Write failing schema migration tests**

Create a schema-v5 fixture, migrate to v6, assert old workflow/operation data survives and new columns are null/empty by default.

- [ ] **Step 2: Write failing persistence tests**

Create a workflow with execution intent, materialize operations, persist a first route decision, persist a changed route, and assert bounded fallback history preserves the previous route/policy metadata.

- [ ] **Step 3: Extend step/input models**

Bound intent dictionaries by validating supported keys/types in the workflow registry or a shared intent parser before persistence. Public projections remain redacted.

- [ ] **Step 4: Implement v6 migration and materialization**

Persist execution intent at operation materialization. Do not rewrite legacy exact execution definitions.

- [ ] **Step 5: Implement route decision persistence**

Use `BEGIN IMMEDIATE`, operation version updates/events, deterministic JSON, bounded fallback history, and current workflow lease checks where an execution lease exists.

- [ ] **Step 6: Verify and commit**

```text
feat(workflows): persist execution route metadata
```

### Task 5: Workflow Executor Route Integration and Uncertain Safety

**Files:**
- Modify: `core/workflow_actions.py`
- Modify: `core/workflow_executor.py`
- Modify: `tools/workflows/registry.py`
- Modify: `tests/test_workflow_routing.py`
- Modify: `tests/test_workflow_reconciliation.py` if needed

**Interfaces:**
- Extends `ActionDescriptor` with `execution_route: str | None`.
- `WorkflowExecutor` accepts an optional route planner callable/object; default behavior remains unchanged when steps have no execution intent.
- Route selection happens before checkpointing an operation RUNNING and before side effects.
- Selected route must equal the fixed action descriptor route for routed workflow steps.

- [ ] **Step 1: Write failing execution persistence test**

Run a routed read-only workflow action and assert route decision exists before the action handler records its result.

- [ ] **Step 2: Write failing route mismatch test**

Provide an intent whose recommendation disagrees with the fixed allowlisted action route and assert execution fails before the side effect.

- [ ] **Step 3: Write failing capability-loss/fallback test**

Use a deterministic planner fixture that returns one valid route on the first transient attempt and another on the safe retry. Assert fallback history records the change.

- [ ] **Step 4: Write failing uncertain no-replay test**

Force `SideEffectUncertain`, reconcile/attempt resume, and assert the route planner invocation count does not increase until the uncertain operation is conclusively resolved.

- [ ] **Step 5: Integrate the router**

Wire the production workflow registry to create a router from current profile/platform capability information and pass it into `WorkflowExecutor`.

- [ ] **Step 6: Preserve existing behavior**

All existing workflows without `execution_intent` must execute exactly as before and must not require router availability.

- [ ] **Step 7: Verify and commit**

```text
feat(workflows): route typed steps with durable policy metadata
```

### Task 6: Health and Router Observability

**Files:**
- Modify: `core/health_snapshot.py`
- Modify: `scripts/health_check.py` only if current output assertions require it
- Modify: `scripts/panel_data.py`
- Test: `tests/test_health_snapshot_runtime.py`
- Modify: `tests/test_panel_data.py`

**Interfaces:**
- `server_health` gains `execution_router`.
- Snapshot contains enabled state, policy version, counts, selected route distribution, native/semantic/raw usage, fallback count/rate, no-route count, bounded recent decisions, and latency summary.

- [ ] **Step 1: Write failing bounded metrics tests**

Generate more decisions than the recent-history cap and assert the snapshot remains bounded and route counts remain correct.

- [ ] **Step 2: Write failing health non-blocking test**

Inject router snapshot data and prove concurrent health calls do not introduce blocking I/O or unbounded serialization.

- [ ] **Step 3: Add router snapshot to health**

Read in-memory metrics only. Do not walk files or add another SQLite query.

- [ ] **Step 4: Expose config/diagnostic fields to panel data**

Keep redaction rules intact.

- [ ] **Step 5: Verify and commit**

```text
feat(health): expose execution routing metrics
```

### Task 7: Control Panel Execution Routing UI

**Files:**
- Modify: `tools/panel/index.html`
- Modify: `tests/test_supervisor.py` or panel-specific HTML assertions if current tests cover panel content
- Modify: `tests/test_panel_data.py`

**Interfaces:**
- Adds one English-only panel section fed from existing health JSON.
- Shows summary counters, route distribution, native/semantic/raw usage, policy version, and bounded recent decisions.

- [ ] **Step 1: Write failing panel assertions**

Assert the panel contains an `Execution Routing` section and expected data keys without embedding sensitive intent payloads.

- [ ] **Step 2: Implement rendering**

Reuse current cards/tables/event rendering conventions. Handle missing router data as `pending/unavailable` without breaking older snapshots.

- [ ] **Step 3: Verify and commit**

```text
feat(panel): add execution routing observability
```

### Task 8: Documentation, Acceptance Tests, and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/tooling.md`
- Modify: `docs/configuration.md`
- Modify: `docs/platform-support.md`
- Modify: `CHANGELOG.md`
- Create: `docs/V0.7.0-VALIDATION.md`
- Modify: relevant integration/performance tests

**Interfaces:**
- Documents exact supported intent classes, route classes, policy, hard safety constraints, workflow persistence, config, and absence of `execution_run`.

- [ ] **Step 1: Add/complete acceptance tests**

Cover every acceptance criterion and ensure router APIs never call screenshot or mutation tools.

- [ ] **Step 2: Update documentation**

Describe deterministic-v1 selection, safety constraints, workflow metadata, observability, limitations, and future `visual.desktop` reservation.

- [ ] **Step 3: Run focused router/workflow/panel suite**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_execution_catalog.py tests/test_execution_router.py tests/test_workflow_routing.py tests/test_workflow_migrations.py tests/test_tool_profiles.py tests/test_mcp_integration.py tests/test_health_snapshot_runtime.py tests/test_panel_data.py -q
```

- [ ] **Step 4: Run full project verification**

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy core tools scripts tests
.\.venv\Scripts\python.exe -m compileall -q core tools scripts tests main.py
.\.venv\Scripts\python.exe main.py --check
.\.venv\Scripts\python.exe -m scripts.health_check --json
.\.venv\Scripts\python.exe -m scripts.doctor --mode local-http --browser
git diff --check
```

- [ ] **Step 5: Record exact validation evidence**

Populate `docs/V0.7.0-VALIDATION.md` with commands, counts, platform assumptions, catalog count, known limitations, and commit SHA.

- [ ] **Step 6: Commit documentation/validation**

```text
docs(router): document adaptive execution policy and validation
```

- [ ] **Step 7: Push feature branch and open PR**

Push `feat/v0.7-execution-router`, create a PR against `main`, and watch CI, Platform Release Validation, and Release Packaging Smoke to terminal success. Do not merge if any required gate is red.

- [ ] **Step 8: Merge only after hosted verification**

After green hosted checks, merge using repository convention, fetch/prune, verify local `main == origin/main`, and rerun a final startup/health smoke on the merge commit.
