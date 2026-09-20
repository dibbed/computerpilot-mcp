# Workflow Operation Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn durable workflows into an executable, recoverable operation lifecycle with strict state transitions, leases, operation-level idempotency, typed actions, broad postcondition reconciliation, and matching MCP/worker behavior.

**Architecture:** Keep `core.workflows` as the compatibility facade while extracting state rules, durable operation storage, action descriptors, and execution into focused modules. SQLite remains the authoritative workflow ledger; jobs and external systems retain their own authoritative runtime state and are referenced by durable identifiers. Both MCP execution and the background worker call the same lease-aware executor, and uncertain side effects can advance only through evidence-backed reconciliation or an explicit audited acknowledgement.

**Tech Stack:** Python 3.10+, SQLite/WAL, FastMCP-compatible `MCPServer`, Pydantic v2, pytest, Ruff, mypy, psutil, stdlib subprocess/urllib/hashlib.

**Spec:** `docs/superpowers/specs/2026-09-20-workflow-operation-lifecycle-design.md`

## Global Constraints

- Preserve the existing `core.workflows` public imports and all current workflow MCP contracts.
- Use additive SQLite migrations through `PRAGMA user_version`; opening an existing v0.2.4 database must preserve every workflow and step.
- Never replay an uncertain mutating operation automatically.
- Require compare-and-swap versions for user-visible workflow mutations and lease ownership for executor mutations.
- Persist only redacted arguments/results/evidence; do not write secrets into operation or event rows.
- Keep action dispatch closed and typed. Arbitrary shell command execution is out of scope.
- Make operation idempotency deterministic per workflow, step, and attempt; a key collision with different input must fail closed.
- Keep tool profiles enforced through registrar selection. New workflow tools belong only to profiles that already include the workflow registrar.
- Keep semantic desktop evidence based on UI Automation identity (`automation_id`, `control_type`, patterns), not coordinates.
- Every behavior change starts with a failing test. Run focused tests before broader regression.
- Do not publish a release or push a tag as part of implementation unless separately authorized.

## Review Focus

- Migration safety for existing databases and legacy step materialization.
- Transaction boundaries between workflow, step, operation, lease, and event writes.
- Crash windows immediately before/after side effects and checkpoints.
- Terminal-state immutability and legal recovery transitions.
- Lease expiry, stale-owner rejection, and concurrent MCP/worker execution.
- Redaction and bounded persistence of arguments, results, errors, and evidence.
- Conclusive versus inconclusive postcondition semantics.
- Compatibility of existing built-ins, imports, tool profiles, and worker invocation.

---

## Task 1: Introduce explicit lifecycle models and transition guards

**Files:**

- Create: `core/workflow_models.py`
- Modify: `core/workflows.py`
- Create: `tests/test_workflow_transitions.py`
- Modify: `tests/test_workflow_store.py`

- [ ] **Step 1: Write failing workflow, step, and operation transition tests**

Cover every allowed edge and representative forbidden edges, including `completed -> running`, `cancelled -> queued`, `failed -> completed`, and `uncertain -> running`. Also prove same-state transitions are rejected unless the caller explicitly performs a no-op before reaching the transition method.

```python
@pytest.mark.parametrize(
    ("source", "target"),
    [
        (WorkflowState.COMPLETED, WorkflowState.RUNNING),
        (WorkflowState.CANCELLED, WorkflowState.QUEUED),
        (WorkflowState.UNCERTAIN, WorkflowState.RUNNING),
    ],
)
def test_illegal_workflow_transition_is_rejected(
    workflow_store: WorkflowStore,
    source: WorkflowState,
    target: WorkflowState,
) -> None:
    workflow = create_workflow_in_state(workflow_store, source)
    with pytest.raises(ToolError, match="transition"):
        workflow_store.transition(workflow["workflow_id"], workflow["version"], target)
```

- [ ] **Step 2: Run the focused tests and confirm the unsafe behavior**

Run: `python -m pytest tests/test_workflow_transitions.py tests/test_workflow_store.py -q`

Expected: new forbidden-transition assertions fail because `WorkflowStore.transition()` currently validates only the version.

- [ ] **Step 3: Add lifecycle enums and exhaustive transition matrices**

Define `WorkflowState`, `StepState`, and `OperationState` in `core/workflow_models.py`, plus a shared validator:

```python
def require_transition(
    entity: str,
    current: Enum,
    target: Enum,
    allowed: Mapping[Enum, frozenset[Enum]],
) -> None:
    if target not in allowed[current]:
        raise ToolError(
            f"invalid_{entity}_transition",
            f"Cannot transition {entity} from {current.value!r} to {target.value!r}.",
        )
```

Use the exact matrices approved in the spec. Re-export `WorkflowState` from `core.workflows` to keep existing imports stable.

- [ ] **Step 4: Enforce workflow transitions at the store boundary**

Within the same `BEGIN IMMEDIATE` transaction, read the current state and version, validate the transition, then perform the CAS update. Do not validate from a stale object loaded outside the transaction.

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest tests/test_workflow_transitions.py tests/test_workflow_store.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add core/workflow_models.py core/workflows.py tests/test_workflow_transitions.py tests/test_workflow_store.py
git commit -m "feat: enforce workflow lifecycle transitions"
```

## Task 2: Add versioned operation, lease, and event persistence

**Files:**

- Create: `core/workflow_store.py`
- Modify: `core/workflows.py`
- Create: `tests/test_workflow_migrations.py`
- Create: `tests/test_workflow_operations.py`

- [ ] **Step 1: Write a legacy-database migration test**

Build a database using the current v0.2.4 schema, insert queued/completed/uncertain examples, open it with the new store, and assert:

- existing workflow/step data is unchanged;
- `PRAGMA user_version` reaches the new schema version;
- one deterministic legacy operation exists per step;
- reopening is idempotent and creates no duplicate operations/events.

```python
def test_existing_database_is_migrated_without_data_loss(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    seed_v024_database(path)
    store = WorkflowStore(path)
    migrated = store.get("legacy-workflow")
    assert migrated["steps"][0]["state"] == "completed"
    assert store.list_operations("legacy-workflow")["total_count"] == 1
    WorkflowStore(path)
    assert store.list_operations("legacy-workflow")["total_count"] == 1
```

- [ ] **Step 2: Run the migration tests and confirm missing tables/versioning**

Run: `python -m pytest tests/test_workflow_migrations.py tests/test_workflow_operations.py -q`

Expected: FAIL because operation/lease/event storage does not exist.

- [ ] **Step 3: Extract `WorkflowStore` and add ordered schema migrations**

Create migrations that add:

```sql
CREATE TABLE workflow_operations (
    operation_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    operation_index INTEGER NOT NULL,
    action TEXT NOT NULL,
    state TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    input_hash TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    result_json TEXT,
    postcondition_json TEXT,
    evidence_json TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    error TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(workflow_id, step_index, operation_index)
);
CREATE TABLE workflow_leases (
    workflow_id TEXT PRIMARY KEY REFERENCES workflows(workflow_id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    lease_token TEXT NOT NULL UNIQUE,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL
);
CREATE TABLE workflow_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
    operation_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

Add indexes for workflow/state ordering, operation state, and event lookup. Each migration runs inside `BEGIN IMMEDIATE` and advances `user_version` only after success.

- [ ] **Step 4: Materialize operations transactionally**

New workflows receive operation rows during `create()`. Existing steps receive deterministic IDs during migration, derived from workflow ID and step index. Store a SHA-256 hash of canonical redacted arguments and reject incompatible reuse of an idempotency key.

- [ ] **Step 5: Keep `core.workflows` as a compatibility facade**

Re-export `WorkflowStore`, `workflow_store`, models, and later `WorkflowExecutor`; existing imports must not change.

- [ ] **Step 6: Run migration and compatibility tests**

Run: `python -m pytest tests/test_workflow_migrations.py tests/test_workflow_operations.py tests/test_workflow_store.py tests/test_workflow_execution.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add core/workflow_store.py core/workflows.py tests/test_workflow_migrations.py tests/test_workflow_operations.py tests/test_workflow_store.py
git commit -m "feat: persist workflow operations and events"
```

## Task 3: Implement workflow execution leases

**Files:**

- Modify: `core/workflow_store.py`
- Create: `tests/test_workflow_leases.py`

- [ ] **Step 1: Write failing lease concurrency and expiry tests**

Prove that:

- only one owner acquires a live workflow lease;
- renewal requires the exact lease token;
- stale owners cannot checkpoint or release another owner's lease;
- expired leases can be replaced;
- releasing a lease is idempotent for its current owner.

- [ ] **Step 2: Run the focused test**

Run: `python -m pytest tests/test_workflow_leases.py -q`

Expected: FAIL because lease APIs do not exist.

- [ ] **Step 3: Implement lease APIs with database time comparisons**

Add:

```python
def acquire_lease(self, workflow_id: str, owner_id: str, ttl_sec: float) -> WorkflowLease: ...
def renew_lease(self, workflow_id: str, lease_token: str, ttl_sec: float) -> WorkflowLease: ...
def require_lease(self, workflow_id: str, lease_token: str) -> WorkflowLease: ...
def release_lease(self, workflow_id: str, lease_token: str) -> None: ...
```

All acquisition/replacement decisions happen under `BEGIN IMMEDIATE`. Bound TTL to the configured safe range and use UTC timestamps produced by one helper.

- [ ] **Step 4: Require lease tokens for executor-owned mutations**

Operation start/checkpoint and executor workflow transitions must call `require_lease()` within their mutation transaction. User actions such as cancel continue to use workflow CAS and do not forge executor ownership.

- [ ] **Step 5: Run lease and store tests**

Run: `python -m pytest tests/test_workflow_leases.py tests/test_workflow_store.py tests/test_workflow_operations.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add core/workflow_store.py tests/test_workflow_leases.py tests/test_workflow_operations.py
git commit -m "feat: serialize workflow execution with leases"
```

## Task 4: Replace the action map with typed action descriptors

**Files:**

- Modify: `core/workflow_actions.py`
- Modify: `tools/workflows/builtins.py`
- Create: `tests/test_workflow_actions.py`
- Modify: `tests/test_builtin_workflows.py`

- [ ] **Step 1: Write failing descriptor validation tests**

Test unknown fields, missing required fields, secret redaction, mutating actions without postconditions, timeout policy, retry classification, and descriptor lookup. Preserve rejection of arbitrary `shell` actions.

```python
def test_mutating_action_requires_supported_postcondition() -> None:
    with pytest.raises(ToolError, match="postcondition"):
        validate_operation("apply_patch", {"cwd": ".", "patch": "..."}, None)
```

- [ ] **Step 2: Run focused tests**

Run: `python -m pytest tests/test_workflow_actions.py tests/test_builtin_workflows.py -q`

Expected: FAIL because `ACTION_HANDLERS` contains handlers only, without schemas or lifecycle metadata.

- [ ] **Step 3: Introduce `ActionDescriptor`**

```python
@dataclass(frozen=True, slots=True)
class ActionDescriptor:
    name: str
    input_model: type[BaseModel]
    handler: ActionHandler
    mutates: bool
    retry_policy: Literal["never", "transient"]
    allowed_postconditions: frozenset[str]
    secret_fields: frozenset[str] = frozenset()
```

Expose `get_action_descriptor()`, `validate_operation()`, and `execute_action()`. Pydantic models use `extra="forbid"`; returned validated arguments are what get hashed and persisted after redaction.

- [ ] **Step 4: Add the approved action surface**

Register descriptors for existing actions and for `apply_patch`, `affected_tests`, `git_stage`, `git_commit`, `run_durable_job`, `verify_changes`, `check_file`, `check_http`, semantic desktop action, and browser action. Reuse existing tool/service implementation functions; do not call MCP wrappers or duplicate business logic.

- [ ] **Step 5: Update built-ins to pass descriptor validation**

Give every mutating built-in an explicit supported postcondition. Validate the complete workflow definition before persistence so invalid workflows never enter the queue.

- [ ] **Step 6: Run focused tests**

Run: `python -m pytest tests/test_workflow_actions.py tests/test_builtin_workflows.py tests/test_workflow_execution.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add core/workflow_actions.py tools/workflows/builtins.py tests/test_workflow_actions.py tests/test_builtin_workflows.py tests/test_workflow_execution.py
git commit -m "feat: validate typed workflow actions"
```

## Task 5: Expand deterministic postcondition strategies

**Files:**

- Modify: `core/reconcilers.py`
- Modify: `core/recovery_models.py`
- Create: `tests/test_workflow_postconditions.py`
- Modify: `tests/test_recovery_reconciliation.py`

- [ ] **Step 1: Write failing evaluator contract tests**

Cover filesystem existence/absence/hash, Git head/index, process identity/exit, durable job state, installed package/version, HTTP status/body hash, semantic UI element state, and browser URL/element state. For inaccessible or unavailable external evidence assert `conclusive=False`, never a guessed success.

- [ ] **Step 2: Run focused tests**

Run: `python -m pytest tests/test_workflow_postconditions.py tests/test_recovery_reconciliation.py -q`

Expected: FAIL for the new evaluator kinds.

- [ ] **Step 3: Add an explicit evaluator descriptor registry**

```python
@dataclass(frozen=True, slots=True)
class PostconditionDescriptor:
    kind: str
    evaluator: PostconditionEvaluator
    source: str
    safe_for_reconciliation: bool = True
```

Evaluators must be side-effect-free, validate typed expected input, return bounded evidence, and distinguish `satisfied=False` from `conclusive=False`.

- [ ] **Step 4: Implement non-UI strategies**

Implement filesystem, Git, process, durable job, package, and HTTP strategies. Process evidence must match PID plus creation time and executable. Package evidence must use the active Python environment via `importlib.metadata`, not invoke an installer.

- [ ] **Step 5: Implement UI/browser adapters without coordinate fallback**

UI evidence requires stable semantic locators and reads UI Automation patterns/properties only. Browser evidence uses the existing browser manager/session and semantic selector/URL state. A missing session or inaccessible UI returns inconclusive evidence.

- [ ] **Step 6: Run focused tests**

Run: `python -m pytest tests/test_workflow_postconditions.py tests/test_recovery_reconciliation.py tests/test_desktop_uia.py tests/test_browser_registry.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add core/reconcilers.py core/recovery_models.py tests/test_workflow_postconditions.py tests/test_recovery_reconciliation.py tests/test_desktop_uia.py tests/test_browser_registry.py
git commit -m "feat: broaden workflow postcondition evidence"
```

## Task 6: Build the operation-aware executor

**Files:**

- Create: `core/workflow_executor.py`
- Modify: `core/workflows.py`
- Modify: `core/workflow_store.py`
- Modify: `tests/test_workflow_execution.py`
- Create: `tests/test_workflow_crash_recovery.py`

- [ ] **Step 1: Write failing execution and crash-window tests**

Test:

- MCP/worker-safe acquisition of one lease;
- operation creation before execution;
- operation-level idempotency reuse with matching input hash;
- conflicting idempotency input rejection;
- transient retry creates a new attempt without duplicating confirmed effects;
- crash before action keeps the operation safely retryable;
- crash after action but before checkpoint marks the operation uncertain on recovery;
- completed operations are never replayed;
- cancellation stops before the next operation.

- [ ] **Step 2: Run focused executor tests**

Run: `python -m pytest tests/test_workflow_execution.py tests/test_workflow_crash_recovery.py -q`

Expected: FAIL because execution is step-only and lease-unaware.

- [ ] **Step 3: Implement one shared `WorkflowExecutor`**

```python
class WorkflowExecutor:
    def execute(
        self,
        workflow_id: str,
        *,
        owner_id: str,
        dry_run: bool = False,
        lease_ttl_sec: float = 30.0,
    ) -> dict[str, Any]: ...
```

Execution order:

1. Load and validate the queued workflow.
2. Acquire the lease.
3. Transition workflow/step/operation under guarded transactions.
4. Commit the `running` operation before invoking the action.
5. Execute the typed handler outside the SQLite transaction.
6. Evaluate the declared postcondition.
7. Persist bounded redacted result/evidence and aggregate states.
8. Renew the lease between operations and bounded retry delays.
9. Release the lease in `finally` only when the token still belongs to this executor.

- [ ] **Step 4: Implement deterministic operation idempotency**

Use `<workflow_id>:<step_index>:<operation_index>:<attempt>` as the internal key unless the definition supplies a stable external key. Reusing a completed matching operation returns stored evidence; a mismatched input hash raises `operation_idempotency_conflict`.

- [ ] **Step 5: Recover interrupted execution conservatively**

At startup, only operations left `running` after lease expiry become `uncertain`. Their workflow/step aggregates become `uncertain`; queued workflows and live leases are untouched. Record the recovery as an event.

- [ ] **Step 6: Run focused tests**

Run: `python -m pytest tests/test_workflow_execution.py tests/test_workflow_crash_recovery.py tests/test_workflow_leases.py tests/test_workflow_operations.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add core/workflow_executor.py core/workflows.py core/workflow_store.py tests/test_workflow_execution.py tests/test_workflow_crash_recovery.py tests/test_workflow_leases.py tests/test_workflow_operations.py
git commit -m "feat: execute durable workflow operations"
```

## Task 7: Expose execution and operation inspection through MCP

**Files:**

- Modify: `tools/workflows/registry.py`
- Modify: `scripts/workflow_worker.py`
- Modify: `tests/test_workflow_tools.py`
- Modify: `tests/test_tool_profiles.py`
- Modify: `tests/test_mcp_integration.py`

- [ ] **Step 1: Write failing MCP parity tests**

Add tests for:

- `workflow_execute` dry-run and real execution;
- `workflow_operations` pagination and redacted output;
- MCP and worker producing the same terminal state/evidence;
- a concurrent second executor receiving a lease conflict;
- new tools appearing only in profiles that register workflows;
- exact unique registered-tool count updated from 106 to 108 at this stage.

- [ ] **Step 2: Run focused MCP tests**

Run: `python -m pytest tests/test_workflow_tools.py tests/test_tool_profiles.py tests/test_mcp_integration.py -q`

Expected: FAIL because the tools are not registered and the worker calls the old method.

- [ ] **Step 3: Register `workflow_execute` and `workflow_operations`**

`workflow_execute` is mutating and accepts workflow ID, expected version, dry-run, and bounded lease TTL. `workflow_operations` is read-only with offset/limit and optional state filtering. Both return structured, bounded output.

- [ ] **Step 4: Route the worker through the same executor API**

Give each worker process a stable runtime owner ID and call `execute()`. A lease conflict is a skip, not a workflow failure. Keep `--once` semantics.

- [ ] **Step 5: Run focused MCP and worker tests**

Run: `python -m pytest tests/test_workflow_tools.py tests/test_tool_profiles.py tests/test_mcp_integration.py tests/test_workflow_execution.py -q`

Expected: PASS with 108 unique tools in the full profile; Task 8 adds the final two reconciliation tools.

- [ ] **Step 6: Commit**

```powershell
git add tools/workflows/registry.py scripts/workflow_worker.py tests/test_workflow_tools.py tests/test_tool_profiles.py tests/test_mcp_integration.py tests/test_workflow_execution.py
git commit -m "feat: expose workflow execution through mcp"
```

## Task 8: Complete reconciliation and explicit acknowledgement

**Files:**

- Create: `core/workflow_reconciliation.py`
- Modify: `core/workflow_store.py`
- Modify: `tools/workflows/registry.py`
- Create: `tests/test_workflow_reconciliation.py`
- Modify: `tests/test_workflow_tools.py`

- [ ] **Step 1: Write failing reconciliation lifecycle tests**

Prove all three outcomes:

- conclusive+satisfied evidence completes the operation and advances the aggregate;
- conclusive+unsatisfied evidence marks the operation failed and permits policy-controlled resume;
- inconclusive evidence leaves the operation/workflow uncertain;
- explicit acknowledgement requires expected version, resolution, non-empty reason, and records actor/audit event;
- acknowledgement cannot overwrite a terminal operation or bypass an unsupported transition.

- [ ] **Step 2: Run focused tests**

Run: `python -m pytest tests/test_workflow_reconciliation.py tests/test_workflow_tools.py -q`

Expected: FAIL because uncertain workflows have no exit path.

- [ ] **Step 3: Implement side-effect-free reconciliation**

```python
def reconcile_operation(
    store: WorkflowStore,
    operation_id: str,
    *,
    expected_version: int,
) -> dict[str, Any]: ...
```

Transition operation/workflow to `reconciling`, evaluate only the persisted postcondition, then atomically persist evidence, operation state, step aggregate, workflow aggregate, current step, and event.

- [ ] **Step 4: Implement audited manual acknowledgement**

Allow only `resolved_completed`, `resolved_failed`, or `remain_uncertain`. Require a reason and actor string, redact/bound both, and write an immutable event. This is an explicit operator assertion, not automated proof, and the response must label its evidence source accordingly.

- [ ] **Step 5: Register MCP tools**

Add `workflow_reconcile` and `workflow_acknowledge_operation`. The former is mutating because it changes lifecycle state even though its evidence collection is read-only. Both require CAS versions.

- [ ] **Step 6: Make resume operate on reconciled aggregates**

`workflow_resume` may queue only `created`, `paused`, or policy-retryable `failed` workflows with no unresolved uncertain operation. It must reject direct `uncertain -> queued` transitions.

- [ ] **Step 7: Run focused tests**

Run: `python -m pytest tests/test_workflow_reconciliation.py tests/test_workflow_tools.py tests/test_workflow_transitions.py tests/test_recovery_reconciliation.py -q`

Expected: PASS.

Also update the registry-count assertion from 108 to 110 now that both reconciliation tools are registered.

- [ ] **Step 8: Commit**

```powershell
git add core/workflow_reconciliation.py core/workflow_store.py tools/workflows/registry.py tests/test_workflow_reconciliation.py tests/test_workflow_tools.py tests/test_workflow_transitions.py
git commit -m "feat: reconcile uncertain workflow operations"
```

## Task 9: Formalize ownership boundaries, health, and semantic automation proof

**Files:**

- Modify: `scripts/health_check.py`
- Modify: `tests/test_desktop_uia.py`
- Modify: `tests/test_tool_profiles.py`
- Modify: `tests/test_diagnostics.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add failing regression assertions**

Assert that health output separately reports workflows, unresolved workflow operations, leases, and existing recovery-journal operations. Add explicit semantic UI tests for `automation_id`, `control_type`, and Invoke/Value/SelectionItem pattern selection with no coordinate fallback. Prove minimal profiles do not register workflow tools.

- [ ] **Step 2: Run the focused checks**

Run: `python -m pytest tests/test_desktop_uia.py tests/test_tool_profiles.py tests/test_diagnostics.py -q`

Expected: FAIL for the new health fields and any missing semantic assertions.

- [ ] **Step 3: Add bounded health summaries**

Expose counts and the oldest bounded unresolved records, not unbounded payloads. Distinguish:

- workflow definitions/current aggregate state: workflow database;
- workflow side effects: operation rows;
- process completion/output: job store;
- legacy standalone recovery entries: recovery journal;
- advisory notes: memory store.

- [ ] **Step 4: Document source-of-truth and operator recovery**

Update README workflow examples for plan/start/execute/status/operations/reconcile/acknowledge/resume and describe leases, idempotency, and the no-blind-replay rule. Add a dated `CHANGELOG.md` entry using `2026-09-20` and the repository's existing format.

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest tests/test_desktop_uia.py tests/test_tool_profiles.py tests/test_diagnostics.py tests/test_workflow_tools.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add scripts/health_check.py tests/test_desktop_uia.py tests/test_tool_profiles.py tests/test_diagnostics.py README.md CHANGELOG.md
git commit -m "docs: document durable workflow recovery"
```

## Task 10: Verify migration, quality, runtime registration, and performance

**Files:**

- Modify if required by measured regression only: `scripts/perf_benchmark.py`
- Modify if required by measured regression only: `tests/test_perf_benchmark.py`
- Modify: `graphify-out/*` through `graphify update .`

- [ ] **Step 1: Run the workflow subsystem suite**

```powershell
python -m pytest tests/test_workflow_store.py tests/test_workflow_migrations.py tests/test_workflow_operations.py tests/test_workflow_transitions.py tests/test_workflow_leases.py tests/test_workflow_actions.py tests/test_workflow_postconditions.py tests/test_workflow_execution.py tests/test_workflow_crash_recovery.py tests/test_workflow_reconciliation.py tests/test_workflow_tools.py tests/test_builtin_workflows.py -q
```

Expected: all pass.

- [ ] **Step 2: Run static validation**

```powershell
python -m ruff check .
python -m mypy .
python -m compileall -q core tools scripts tests
```

Expected: exit code 0 for every command.

- [ ] **Step 3: Run the complete regression suite**

Run: `python -m pytest -q`

Expected: all tests pass with no errors or skipped failures hidden by retries.

- [ ] **Step 4: Validate real MCP startup and registry uniqueness**

Run the repository's existing startup/health commands, then assert the full profile exposes 110 unique tools and the workflow tools carry the intended read-only/mutating annotations.

- [ ] **Step 5: Run quick and full performance benchmarks**

```powershell
python scripts/perf_benchmark.py --profile quick
python scripts/perf_benchmark.py --profile full
```

Expected: zero benchmark errors/skips and no comparison above the repository's accepted regression threshold. If a regression appears, isolate it before changing thresholds; never weaken the threshold to make the run green.

- [ ] **Step 6: Validate upgrade from a preserved pre-change database copy**

Create a disposable copy of a v0.2.4-format workflow database, open it with the new runtime, and verify row counts, workflow/step states, operation materialization, idempotent reopen, and `PRAGMA integrity_check = ok`. Do not mutate the user's live state database for this check.

- [ ] **Step 7: Refresh Graphify and inspect the final diff**

```powershell
graphify update .
git diff --check
git status --short
git log --oneline --decorate -12
```

Expected: Graphify update succeeds, `git diff --check` is clean, and only intended generated graph changes remain.

- [ ] **Step 8: Commit measured benchmark or graph updates**

Only include files that actually changed:

```powershell
git add scripts/perf_benchmark.py tests/test_perf_benchmark.py graphify-out
git commit -m "chore: refresh workflow validation artifacts"
```

If none changed, skip this commit.

## Task 11: Independent completion review and release-ready handoff

**Files:**

- Review all files changed since `162b10d`
- Update only if defects are found: relevant source/tests/docs

- [ ] **Step 1: Review the complete branch diff against the approved spec**

Check every acceptance criterion, public contract, migration, failure path, and tool registration. Specifically search for placeholders and accidental bypasses:

```powershell
rg -n "TODO|TBD|NotImplemented|pass$|except Exception:\s*pass" core tools scripts tests README.md CHANGELOG.md
git diff 162b10d..HEAD --stat
git diff 162b10d..HEAD -- core tools scripts tests README.md CHANGELOG.md
```

- [ ] **Step 2: Fix findings with targeted regression tests**

For each real defect, first add or tighten one focused test, reproduce the failure, implement the narrow fix, rerun the focused test, and commit with an accurate Conventional Commit title.

- [ ] **Step 3: Re-run completion evidence after the last code change**

At minimum rerun Ruff, mypy, compileall, the workflow subsystem suite, full pytest, MCP startup/health, registry uniqueness, both benchmark profiles, and `git diff --check`. Evidence recorded before the last code change is stale and must not be reported as final.

- [ ] **Step 4: Produce a factual handoff**

Report exact commit IDs, test count, mypy file count, unique MCP tool count, benchmark case/error counts, migration result, Graphify refresh, working-tree status, and any unverified runtime area. Do not claim push, tag, GitHub release, or production deployment unless separately performed and evidenced.
