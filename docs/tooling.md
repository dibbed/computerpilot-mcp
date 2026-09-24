# Tooling and Workflows

ComputerPilot MCP exposes typed MCP tools grouped into capability domains. The default `full` profile enables every domain supported by the current platform.

## Profiles

Available profiles:

- `minimal`
- `coding`
- `git`
- `testing`
- `desktop`
- `browser`
- `operations`
- `full`

Set:

```text
MCP_TOOL_PROFILE=<profile>
```

`discover_tool_domains` reports the requested/active domains and platform capabilities. `recommend_tools` searches only the tools actually registered in the active profile.

## Filesystem and editing

Use the smallest mutation primitive that matches the task:

- `read_file` for bounded reads;
- `replace_exact` for a known fragment with an expected match count;
- anchored replacement for a uniquely bounded region;
- function/class replacement for Python symbol bodies;
- `apply_patch` for transactional multi-file unified diffs;
- `safe_refactor` when validation and rollback should be coupled to edits;
- `write_file` only for an intentional whole-file replacement.

Filesystem read-modify-write paths use scoped resource locking. Where external editors may race the MCP, use SHA-256 preconditions on supported operations.

## Code intelligence

Project tools provide:

- project summaries;
- Python function/class/import lookup;
- dependency graphs;
- consolidated `code_context`;
- version-aware cached Python metadata.

Language tools add LSP-based:

- definition;
- references;
- document/workspace symbols;
- hover;
- call hierarchy;
- diagnostics;
- transactional rename;
- code-action workspace edits.

The language layer uses a trusted discovered `pyright-langserver --stdio` executable and bounds one-shot protocol requests.

## Git

Read operations include:

- status;
- bounded diff/show;
- blame;
- merge base;
- changed files;
- branches;
- conflicts.

Guarded mutation operations can:

- create a branch;
- stage explicit paths;
- commit the current index;
- restore explicit paths from an explicit revision.

The MCP Git surface intentionally does not expose force-push, reset, clean, or arbitrary push operations.

## Testing and diagnostics

Testing tools integrate:

- pytest;
- Ruff;
- mypy;
- affected-test selection;
- merged diagnostics;
- change-aware verification;
- durable validation watches.

Recommended code-change loop:

```text
code_context
    ↓
apply_patch / rename_symbol / replace_exact
    ↓
affected_tests
    ↓
verify_changes
```

`verify_changes` can run syntax, Ruff, mypy, and the narrowest sound pytest scope. Shared/repository-wide changes can fall back to the full suite.

## Terminal and processes

Choose execution based on intent:

- `run_process`: bounded foreground executable without implicit shell expansion.
- `run_background`: long-running process managed by the current runtime session.
- `submit_job`: durable execution that should survive MCP runtime restarts.
- `run_cmd`: intentional Windows CMD expansion.
- `run_shell`: intentional POSIX shell expansion on Linux/macOS.
- PowerShell execution: available where PowerShell/pwsh exists.

## Durable jobs

Typical flow:

```text
submit_job
    ↓
job_status / job_wait
    ↓
job_output
    ↓
cancel_job when needed
```

Durable jobs use persistent state, independent workers, bounded admission control, idempotency keys, status versions, and disk-backed output.

Do not place secrets in `idempotency_key` values.

## Recovery

Standalone mutation recovery tools support:

```text
list_uncertain_operations
    ↓
inspect_uncertain_operation
    ↓
reconcile_operation
```

If persisted evidence cannot decide the result, an operator can explicitly acknowledge it.

The recovery layer does not automatically replay arbitrary uncertain side effects.

## Durable workflows

Workflows provide persisted multi-step orchestration with version guards, leases, retries, cancellation, operation checkpoints, and reconciliation.

Typical flow:

```text
workflow_plan
    ↓
workflow_start
    ↓
workflow_execute
    ↓
workflow_status / workflow_operations
```

If an operation becomes uncertain, reconcile it before resuming.

Built-in plans:

- `implement_and_verify`
- `safe_git_commit`
- `prepare_release`

`deploy_and_healthcheck` requires an explicit project adapter and otherwise fails closed.

## Browser

Optional Playwright tools provide:

- open/reuse session;
- click;
- fill;
- screenshot;
- close.

Sessions use isolated browser contexts. Compatible sessions can share a browser process while remaining isolated at the context level.

## Windows desktop

On supported Windows hosts:

- `desktop_screenshot`;
- active-window metadata;
- mouse click;
- Unicode keyboard typing;
- hotkeys;
- semantic UI Automation discovery/invoke/value/select/wait.

Prefer semantic UI Automation over screen coordinates when an application exposes a stable accessibility tree.

## Memory

Project memory stores compact versioned facts with provenance and optimistic revision checks.

It is intended for bounded project context such as architecture decisions, important paths, user preferences, and previous fixes. It is not a raw transcript store and does not override runtime safety/policy decisions.

## Health and discovery

`server_health` reports runtime/platform capability state plus resource/job/browser/workflow/recovery summaries.

Use health metadata for diagnostics, not as a substitute for validating the external effect you care about.
