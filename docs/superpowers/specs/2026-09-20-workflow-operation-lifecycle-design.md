# Workflow Operation Lifecycle Design

**Status:** Approved design, implementation pending  
**Date:** 2026-09-20  
**Target:** Post-v0.2.4 additive architecture hardening

## 1. Purpose

The current workflow subsystem durably stores definitions and step checkpoints, recovers interrupted running steps as `uncertain`, and executes a fixed action allowlist. Its primary gap is not missing persistence; it is that a workflow step is currently also the smallest durable execution unit. That makes multi-effect steps, step-level idempotency, operation-specific evidence, and recovery from uncertain effects difficult to model precisely.

This change introduces an explicit operation layer beneath workflow steps, exposes the shared executor lifecycle through MCP, enforces legal state transitions, and provides a complete reconciliation path. It remains additive: existing workflow records, MCP tools, recovery records, catalog profiles, semantic UI Automation tools, durable jobs, and project memory formats stay compatible.

## 2. Verified Current State

The following capabilities already exist and must be preserved:

- `WorkflowStore` persists immutable definitions, workflow state, step state, evidence, and optimistic versions in SQLite.
- `WorkflowExecutor` executes queued workflows through an allowlisted action registry.
- `scripts.workflow_worker` consumes queued workflows, but no MCP tool directly invokes the shared executor.
- `workflow_resume` refuses blind replay of an uncertain workflow, but no workflow-specific reconciliation tool can resolve that state.
- `OperationRecoveryJournal` is an append-only generic mutation journal with evidence-based reconciliation.
- Catalog profiles restrict actual registrar execution in `create_server`; they are not presentation-only filters.
- Semantic Windows UI Automation includes window/element locators, `automation_id`, `control_type`, UIA patterns, exact-one mutation resolution, and bounded traversal.
- Project memory, durable jobs, recovery journal, and workflows persist different kinds of state but their source-of-truth boundaries are not documented as a formal contract.

## 3. Goals

1. Make an operation, not a step, the smallest durable side-effect unit.
2. Allow one step to contain one or more ordered operations.
3. Expose bounded workflow execution through MCP while sharing one executor with the worker script.
4. Prevent illegal workflow, step, and operation state transitions.
5. Make uncertain workflow operations inspectable, reconcilable, and explicitly acknowledgeable.
6. Add stable operation-level idempotency across retry, reconnect, and runtime restart.
7. Expand actions and postconditions without allowing arbitrary shell execution.
8. Establish one authoritative store for each state category.
9. Preserve all published MCP tool contracts and existing v0.2.4 data.

## 4. Non-Goals

- No general-purpose workflow language or arbitrary shell action.
- No automatic replay of an uncertain operation.
- No blind deploy implementation.
- No replacement of the durable job scheduler.
- No migration of project memory into the workflow database.
- No removal of coordinate desktop tools.
- No incompatible rewrite of schema-v1 recovery journal records.
- No external orchestration dependency such as Temporal.

## 5. Domain Model

```text
WorkflowDefinition
  └── StepDefinition[]
        └── OperationDefinition[]

WorkflowRun
  └── StepRun[]
        └── OperationRun[]
```

`StepDefinition.action` remains readable for legacy definitions. During materialization, each legacy step becomes a single `OperationDefinition`. New definitions may supply an explicit `operations` list. A definition may not contain both a legacy action and an operations list for the same step.

### 5.1 OperationDefinition

```text
name
action
arguments
timeout_sec
max_retries
postcondition
compensation (optional metadata only in this increment)
```

Compensation is recorded but never inferred or automatically executed. A future release may add explicitly approved compensation execution.

### 5.2 OperationRun

Each durable operation stores:

- `operation_id`
- `workflow_id`
- `step_index`
- `operation_index`
- `action`
- `definition_hash`
- `idempotency_key`
- `state`
- `attempts`
- `version`
- `arguments_fingerprint`
- redacted action result
- postcondition definition
- reconciliation evidence
- linked generic recovery-journal operation ID, when applicable
- start, update, and finish timestamps
- bounded error metadata

The database never stores unredacted secrets. `arguments_fingerprint` is a SHA-256 digest of a canonical, redacted operation definition and is used for conflict detection, not for reconstructing arguments.

## 6. Persistence and Migration

The workflow database receives an additive schema migration tracked with `PRAGMA user_version`.

### 6.1 New tables

```sql
workflow_operations(
    operation_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    operation_index INTEGER NOT NULL,
    action TEXT NOT NULL,
    definition_hash TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    version INTEGER NOT NULL,
    arguments_fingerprint TEXT NOT NULL,
    postcondition_json TEXT,
    result_json TEXT,
    evidence_json TEXT,
    recovery_operation_id TEXT,
    started_at TEXT,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT,
    UNIQUE(workflow_id, step_index, operation_index)
);

workflow_leases(
    workflow_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    version INTEGER NOT NULL
);

workflow_events(
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL,
    step_index INTEGER,
    operation_id TEXT,
    event_type TEXT NOT NULL,
    state TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL
);
```

### 6.2 Legacy materialization

- Existing workflow and step rows are never rewritten destructively.
- On first plan, execution, status expansion, or reconciliation, missing operation rows are created transactionally from the immutable stored definition.
- A legacy step produces operation index `0`.
- Materialization is idempotent through `(workflow_id, step_index, operation_index)` and the stable operation idempotency key.
- If an existing row has a different definition hash, execution fails with `workflow_definition_conflict`.

### 6.3 Transaction boundaries

- Claiming a lease, validating workflow version/state, and transitioning `queued -> running` occur in one `BEGIN IMMEDIATE` transaction.
- Creating or loading an operation attempt and checkpointing `running` occur before invoking an external side effect.
- Publishing action result, evidence, operation state, step aggregation, workflow position, and the workflow event occurs in one transaction after the external call.
- External I/O never occurs while a SQLite transaction is open.

## 7. State Machines

### 7.1 Workflow states

```text
created      -> queued | cancelled
queued       -> running | cancelled
running      -> waiting | paused | failed | uncertain | completed | cancelled
waiting      -> running | paused | failed | uncertain | cancelled
paused       -> queued | cancelled
failed       -> queued | cancelled
uncertain    -> reconciling | cancelled
reconciling  -> paused | failed | uncertain | completed | cancelled
completed    -> terminal
cancelled    -> terminal
```

Every transition is checked by a single `validate_workflow_transition(current, target)` function before the CAS update. Terminal states have no outgoing transitions.

### 7.2 Step states

```text
created -> running | cancelled
running -> waiting | failed | uncertain | completed | cancelled
waiting -> running | failed | uncertain | cancelled
uncertain -> reconciling | cancelled
reconciling -> failed | uncertain | completed
failed -> running | cancelled
completed -> terminal
cancelled -> terminal
```

### 7.3 Operation states

```text
created -> running | cancelled
running -> waiting | succeeded | failed | uncertain | cancelled
waiting -> running | succeeded | failed | uncertain | cancelled
uncertain -> reconciling | acknowledged | unresolvable | cancelled
reconciling -> succeeded | failed | uncertain | unresolvable
succeeded -> terminal
failed -> running | cancelled (only after explicit workflow resume and a known-safe retry classification)
acknowledged -> terminal
unresolvable -> terminal
cancelled -> terminal
```

Step and workflow aggregation use operation states; callers may not set aggregate states arbitrarily.

## 8. Executor Lifecycle

The worker script and MCP tool use the same `WorkflowExecutor.execute()` entry point.

### 8.1 MCP tool

`workflow_execute` accepts:

- `workflow_id`
- `expected_version`
- `max_steps` bounded from 1 to 100
- `lease_sec` bounded from 5 to 300
- `dry_run`

It returns the latest workflow state, executed operation summaries, lease metadata, and whether more queued work remains. It never accepts raw shell text.

### 8.2 Lease behavior

- A workflow can have one active lease.
- A non-expired lease owned by another executor produces `workflow_already_claimed`.
- Lease ownership is a random runtime-scoped identifier, never only a PID.
- The executor renews the lease between operations.
- An expired lease does not prove the side effect failed; if the last operation was `running`, recovery marks it and the workflow `uncertain`.
- Lease release after a known checkpoint is transactional.

### 8.3 Worker behavior

`scripts.workflow_worker` lists queued workflows and calls the shared executor. It contains no separate action or transition logic. `--once` remains supported; continuous mode uses bounded polling.

## 9. Operation-Level Idempotency

The stable key is:

```text
sha256(workflow_id | step_index | operation_index | definition_hash)
```

Rules:

- A terminal successful operation is returned from persisted evidence and never executed again.
- A matching running/uncertain operation cannot be retried until reconciliation.
- A known failed operation may reuse its row only after explicit resume, when its action descriptor classifies retry as safe; its attempt counter and event history remain monotonic.
- A different definition under the same positional key is a conflict.
- Durable jobs receive the operation key as their job idempotency key.
- Patch/file actions require caller-provided or materialized hash preconditions.
- Git commit actions first inspect the persisted operation result and then the repository HEAD before considering a retry.
- Handler-specific idempotency adapters may strengthen, but never weaken, this contract.

## 10. Action Registry

The registry stays closed and typed. Initial supported actions are:

```text
apply_patch
verify_changes
run_pytest
run_ruff
run_mypy
git_status
git_stage
git_commit
run_durable_job
check_file
check_process
check_package
check_http
ui_invoke
ui_set_value
browser_navigate
```

Each action descriptor declares:

- read-only or mutating
- retry class
- required postcondition families
- argument validator
- result redactor
- execution handler
- optional idempotency adapter

Registration fails at import/test time when a mutating action lacks a postcondition contract or result redactor. No `shell`, `powershell`, `cmd`, or arbitrary executable action is introduced. `run_durable_job` remains the only controlled process-launch action and requires a structured executable/argument vector.

## 11. Postcondition Strategies

The shared evaluator gains the following families:

### Filesystem

- `file_exists`
- `file_absent`
- `file_sha256`
- `file_contains` with bounded non-secret text

### Git

- `git_head`
- `git_index_contains`
- `git_clean`
- `git_tag_exists`
- `git_tag_absent`

### Process and jobs

- `process_identity` using PID, creation time, and executable
- `process_absent` using the same identity tuple
- `job_terminal`
- `job_succeeded`

### Package/runtime

- `python_package_version`
- `python_importable`

### Deployment/HTTP

- `http_status`
- `http_json_field`
- `http_body_sha256`

### Desktop and browser

- `ui_element_exists`
- `ui_element_enabled`
- `ui_element_selected`
- `browser_url`
- `browser_title`
- `browser_element_exists`

Desktop/browser evaluators use injected read-only adapters so unit tests remain deterministic. Credentials, typed values, response authorization headers, and unbounded bodies are never persisted as evidence.

## 12. Reconciliation Lifecycle

New MCP tools:

### `workflow_operations`

Lists operation runs for a workflow or one step with pagination and optional state filtering.

### `workflow_reconcile`

Inputs:

- workflow ID and expected workflow version
- operation ID and expected operation version
- an allowlisted postcondition

Behavior:

1. Validates that workflow and operation are uncertain.
2. Transitions both into reconciling under CAS.
3. Evaluates the postcondition without replaying the action.
4. Persists evidence and resolves the operation only when conclusive.
5. Re-aggregates step and workflow state transactionally.
6. Leaves inconclusive work uncertain.

### `workflow_acknowledge_operation`

Allows an operator to mark an uncertain operation `acknowledged` or `unresolvable` with bounded non-empty evidence. This does not claim the action succeeded. Aggregate behavior is explicit:

- `acknowledged`: step becomes paused and requires a deliberate next decision.
- `unresolvable`: step and workflow become failed unless the immutable definition explicitly marks the operation optional.

### Resume rule

`workflow_resume` succeeds only when:

- no unresolved uncertain/reconciling operation exists;
- the workflow aggregate state is `paused`, `failed`, or `created`;
- the requested transition is legal;
- the expected workflow version matches.

## 13. Source-of-Truth Boundaries

| Subsystem | Authoritative for | Not authoritative for |
| --- | --- | --- |
| Workflow DB | workflow/step/operation lifecycle, executor lease, operation evidence | raw process execution output, project facts |
| JobStore | durable process scheduling, process status, stdout/stderr | workflow progress |
| Recovery journal | generic MCP mutation uncertainty outside workflow; linked mutation evidence | workflow aggregation |
| Project memory | curated advisory project facts and decisions | whether an action executed or succeeded |
| Audit log | append-only metadata trail | current state |

Workflow status may reference JobStore or recovery IDs but never duplicates their raw authoritative payloads. Reconciliation copies only bounded derived evidence needed for the workflow decision.

## 14. Catalog Profiles and UI Automation

These capabilities already meet the primary requirement and receive regression hardening rather than architectural replacement.

### Profiles

- Tests must prove that each profile registers only its selected domains plus always-on discovery/health tools.
- Health must report active profile, active domains, registered tool count, and executor availability.
- Recommendation results must contain only actually registered tools.

### Semantic UIA

- Tests must prove matching by automation ID and control type, exact-one mutation resolution, UIA Invoke/Value/SelectionItem patterns, and redaction.
- Health reports whether the native UIA backend is importable.
- Coordinate tools remain documented as fallback-only for non-UIA surfaces.

## 15. MCP Surface

Existing tools remain unchanged:

```text
workflow_plan
workflow_start
workflow_status
workflow_resume
workflow_cancel
```

Additive tools:

```text
workflow_execute
workflow_operations
workflow_reconcile
workflow_acknowledge_operation
```

All new tools use strict Pydantic inputs, structured output, compact actionable errors, correct read/mutation annotations, bounded pagination, and metadata-only audit records.

## 16. Failure and Recovery Rules

- Validation failure before side effect: operation fails deterministically.
- Transient read failure: bounded retry according to immutable policy.
- Known action failure: operation fails with bounded error evidence.
- Runtime loss or timeout after a possible side effect: operation and workflow become uncertain.
- Database publication failure after external success: generic recovery linkage and operation state preserve uncertainty; never replay automatically.
- Postcondition inconclusive: remain uncertain.
- Illegal state transition or stale version: no mutation.
- Lease conflict: no mutation.
- Cancellation: stops before the next operation; an in-flight external effect is reconciled rather than assumed cancelled.

## 17. Security and Privacy

- Action names remain allowlisted.
- All paths use existing path resolution and workspace-boundary rules of the underlying service.
- Secrets are redacted before persistence, audit, or MCP response.
- Literal secret-bearing action arguments are rejected; actions needing credentials reference externally configured runtime secrets and never persist their resolved values.
- Hashes/fingerprints are not treated as secret recovery material.
- HTTP postconditions never persist authorization headers or unbounded response bodies.
- UI value actions store character counts and locator metadata, not typed values.
- Deployment adapters are explicit project code; no generic deploy action is added.

## 18. Testing Strategy

Implementation follows red-green-refactor in these slices:

1. Schema migration and legacy operation materialization.
2. Workflow/step/operation transition matrices.
3. Lease claim, renewal, expiry, and concurrent executor exclusion.
4. MCP `workflow_execute` lifecycle and worker parity.
5. Operation idempotency and restart recovery.
6. Reconciliation, acknowledgment, aggregation, and resume.
7. Expanded action descriptors and postcondition evaluators.
8. Profile enforcement and semantic UIA regression proof.
9. Source-of-truth documentation and health output.

Required final evidence:

- focused migration, concurrency, crash/restart, and reconciliation tests;
- full pytest suite;
- Ruff and mypy;
- compileall, startup, and health checks;
- benchmark cases for operation materialization, lease contention, reconciliation, and executor checkpoints;
- Graphify update and source verification;
- clean Git status.

## 19. Compatibility and Release

- This is an additive minor release after v0.2.4.
- Existing tool names and schemas remain valid.
- New fields in workflow status are additive.
- Existing queued, completed, failed, cancelled, and uncertain workflows remain readable.
- Existing uncertain workflows are not automatically advanced during migration.
- `full` remains the default profile.
- Release notes must distinguish pre-existing profile/UIA capability from newly added lifecycle hardening.

## 20. Acceptance Criteria

The work is complete only when:

1. One step can durably own multiple operation runs.
2. The MCP can execute a bounded workflow through the shared executor.
3. Concurrent executors cannot run the same workflow operation.
4. Every state transition is validated.
5. Operation-level idempotency prevents completed side effects from rerunning.
6. An uncertain operation can be inspected, reconciled or acknowledged, aggregated, and then safely resumed when allowed.
7. Mutating actions require registered postcondition and redaction contracts.
8. Expanded postcondition families have deterministic tests.
9. Profiles demonstrably restrict registration and recommendations.
10. Semantic UIA is covered for locators, patterns, uniqueness, and redaction.
11. Workflow DB, JobStore, recovery journal, memory, and audit have documented non-overlapping authority.
12. Migration, crash/restart, and full regression gates pass without replaying legacy uncertainty.
