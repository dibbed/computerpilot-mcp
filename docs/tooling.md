# Tooling and Workflows

ComputerPilot MCP exposes typed MCP tools grouped into capability domains. The default `full` profile enables every domain supported by the current platform.

## Profiles

Available profiles:

- `minimal`
- `coding`
- `git`
- `testing`
- `documents`
- `desktop`
- `browser`
- `operations`
- `full`

Set:

```text
MCP_TOOL_PROFILE=<profile>
```

`discover_tool_domains` reports the requested/active domains and platform capabilities. `recommend_tools` searches only the tools actually registered in the active profile and attaches shared execution metadata when known.

## Adaptive execution routing

Use the routing diagnostics when an agent or workflow needs to decide *how* an intent should be executed without executing it yet:

- `execution_candidates`: returns a bounded set of valid and rejected candidate routes with capability checks and machine-readable rejection reasons;
- `execution_recommend`: returns one deterministic, explainable recommendation plus bounded candidate details when explanation is enabled.

Supported initial structured intents are:

`filesystem.read`, `filesystem.write`, `code.rename`, `git.read`, `git.write`, `process.run`, `system.inspect`, `document.excel.read`, `document.excel.write`, `document.docx.read`, `document.docx.write`, `document.pdf.read`, `document.pdf.create`, `browser.interact`, `desktop.semantic.interact`, and `desktop.raw.interact`.

The planning client maps a request such as "write cells in Excel" to a canonical intent before calling `execution_recommend`:

```json
{"intent":"document.excel.write","allow_raw_desktop":false}
```

When available in the active profile, this recommends `native.excel` and `excel_write_range`; the client then calls that typed tool with its validated arguments. The recommendation performs no write and grants no execution permission. Free-form requests are not canonical intents: an unsupported intent returns `unknown_execution_intent` with the existing supported-name hint. Profile choice is described in the [profile guide](../README.md#tool-profiles).

The initial route classes are `native.filesystem`, `native.code`, `native.git`, `native.process`, `native.system`, `native.excel`, `native.docx`, `native.pdf`, `semantic.browser`, `semantic.windows_uia`, reserved `visual.desktop`, and `raw.desktop`.

`deterministic-v1` is rule/metadata based; it does not use an opaque model score. Exact structured routes are preferred over semantic UI routes and raw coordinates when all are valid. Unsupported profile/platform routes are removed before ranking.

Hard safety rules include:

- raw desktop must be explicitly permitted and cannot be auto-selected for destructive work;
- stale semantic references cannot fall back to coordinates;
- destructive ambiguous semantic targets fail closed;
- macro-preservation requirements exclude routes that cannot prove fidelity;
- `visual.desktop` is unavailable in v0.8.

There is intentionally no `execution_run` tool in v0.8. The selected route still resolves to existing typed tools and their existing validation, lifecycle, audit, recovery, and permission behavior.

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

Recursive project discovery skips generated child directories `.worktrees`, `.playwright-cli`, `.pytest-tmp`, and `.pytest-tmp-*`, alongside the established exclusions. Explicitly targeting an excluded directory or file still allows inspection. `project_summary.files_scanned` describes the filtered scan; `top_level` remains a bounded raw inventory and may include excluded names. Raw filesystem listing retains its own behavior. An explicitly changed Python file that exists outside the scanned index makes `affected_tests` report `changed_python_outside_scan`, `complete=false`, and a full-suite fallback; it is not silently dropped.

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

`verify_changes` can run syntax, Ruff, mypy, and the narrowest sound pytest scope. Shared/repository-wide changes can fall back to the full suite. Foreground verification stays serial by default with `max_parallel_checks=1`. Read-only analyzer work can opt into capacity 2–3 only with `fail_fast=false` and `ruff_fix=false`; incompatible combinations are rejected before planning or analyzer dispatch. Parallel foreground verification is synchronous and non-durable, so client disconnects do not turn it into a background job. `stage_timeout_sec` remains a per-stage ceiling while `total_timeout_sec` bounds the verification run but is not a hard thread-kill deadline, and independent invocations can still oversubscribe the host because this option is a per-call cap rather than a global admission limit.

`collect_diagnostics` also stays serial by default. Set `max_parallel_checks` to 2 or 3 to run independent Ruff, mypy, and pytest backends concurrently while preserving request-order stages and the existing deterministic merged diagnostic ordering. `timeout_sec` remains a per-backend ceiling. Optional `total_timeout_sec` (up to 10800 seconds) includes queue time and clamps each backend's effective timeout; a backend that has not started before the total deadline is reported timed out and `required_unknown=true`. Backend exceptions are isolated as unavailable stages so successful sibling diagnostics are retained. Thread cleanup can outlive the exact deadline while an already-running owned analyzer is terminating, so the total deadline is a scheduling/result bound rather than a promise to kill Python threads at that instant.

For long checks, `start_verification` accepts the verification inputs plus a required `idempotency_key`, optional
`queue_timeout_sec`, and `max_parallel_checks` (1–3, default 1). It is available when both testing and jobs domains are
active, including `coding` and `full`; existing profiles and foreground response contracts are unchanged.

Start the job, retain its ID, and continue independent work. Collect `job_wait`/`job_output` when the result is needed.
`accepted=true` means submission succeeded. Require job status `succeeded`, exit code zero, and a final JSONL `result`
with `ok=true`, `required_unknown=false`, and `freshness=current` before a dependent decision. Keep partial JSONL lines
between progress chunks. Missing or incomplete final records are unknown, even if output was received.

Output uses schema version 1 events: `started`, `stage_started`, `stage_finished`, `result`. Each record is bounded to
64 KiB, the controlled stream to 4 MiB, and diagnostics explicitly report truncation. The worker runs under ComputerPilot's
interpreter; analyzers select the target project's interpreter. Test caches and temporary directories are isolated per run.

Input snapshots hash a conservative tree excluding standard dependency/runtime/cache directories, bounded to 10,000 files,
64 MiB and 10 seconds per snapshot. Explicit changed files are included even inside an excluded directory; missing explicit paths are also bound so creating them invalidates the snapshot. The same limits apply to these inputs. Inaccessible inputs, symlinks, changing files and exhausted bounds fail closed.
Requests are limited to 64 KiB and the native Windows command-line limit. Use a new idempotency key after changing the
request or input revision. Deduplication lasts only as long as the existing job record is retained.

Do not edit tested inputs while their job runs. Changes before or during execution invalidate the result; unrelated input
changes may conservatively do so too. Start/end hashes cannot detect a transient change that is reverted during a run.
Use `cancel_job` to cancel the owned process tree. Disconnecting a client does not cancel a durable job.
If ownership cleanup cannot be confirmed, the job reports failure with a bounded cleanup error instead of successful cancellation.

For opt-in parallel checks, set `max_parallel_checks=2` or `3` and `fail_fast=false`, with `ruff_fix=false`.
Syntax is a preflight; Ruff checks, mypy and pytest may then overlap, with deterministic result ordering and one total
deadline. Background verification rejects `ruff_fix=true`; fix source with the foreground tool first, then submit verification. Fail-fast remains serial only. A syntax stage skipped because there are no Python targets does not suppress full-suite analyzers. Four active jobs with three checks each can run up to twelve analyzers;
the job limit is not a limit on all descendant processes or threads.

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

If an operation becomes uncertain, reconcile it before resuming. A step may also declare a bounded `execution_intent`; the workflow store persists the selected route/policy/decision before execution and preserves bounded fallback history. Uncertain operations are not rerouted or replayed by the router.

Custom workflows can opt into `execution_mode="dag"`. Every DAG step requires a case-sensitive `step_id` of 1–100 ASCII letters/digits/`_`/`-`; `depends_on` contains step IDs, permits explicit roots with an empty list, and rejects duplicate, self, unknown and cyclic dependencies before persistence. `max_parallel_steps` is bounded to 1–16 and `total_timeout_sec` to 1–86400 seconds; DAG omission normalizes the total deadline to 7200 seconds, while sequential workflows retain cap 1 and no new total deadline. Status preserves original step order and adds derived `depends_on`/`readiness`, stable topological order, requested/effective parallel caps, ready/active/blocked projections, and active admission count. Internally the scheduler claims only dependency-ready operations in stable index order, persists an `attempt_token` plus active admission before dispatch, and crosses an explicit durable `may_dispatch` barrier before calling an action. Schema 9 additionally persists fair all-or-none resource requests with read/write exact/subtree claims and shared runtime-capacity requests. Workflow actions derive conservative footprints: repository verification is write-coarse because pytest can execute repository code, arbitrary durable commands take an exclusive managed-host coordination claim plus their working-tree subtree, and Git mutation footprints include the actual Git common-dir so linked worktrees conflict correctly. Participating direct patch/Git/terminal/job boundaries use the same claim authority; external programs do not. Active claims are quarantined on workflow lease loss and must be explicitly reconciled before conflicting work can proceed. Foreground verification, background verification workers, diagnostics and workflow verification share the configured analyzer capacity instead of multiplying their local concurrency caps. Late results cannot settle an operation after their attempt loses version/token ownership. The scheduler remains deliberately disabled for the public MCP/worker construction path until graph-wide cancellation/restart recovery hardening is complete; plan/start/status and dry-run continue to work while public non-dry-run execute fails before lease acquisition.

Nested managed commands reuse only an active, matching internal owner capability within the parent's resource scope. The runtime propagates this private context to owned child processes and analyzer threads; the reserved environment value cannot be replaced through a tool's `env` argument. DAG `verify_changes` with `ruff_fix=true` is rejected before workflow persistence with `workflow_parallel_fix_unavailable`; sequential workflows and foreground serial fix behavior remain supported.

Built-in plans:

- `implement_and_verify`
- `safe_git_commit`
- `prepare_release`

`deploy_and_healthcheck` requires an explicit project adapter and otherwise fails closed.

## Native documents

The portable `documents` profile/domain provides structured file-native operations without launching Office applications.

Excel:
- `excel_inspect` and `excel_list_sheets` for workbook/sheet metadata;
- `excel_read_range`, `excel_find`, `excel_get_formula`, and `excel_table_info` for bounded reads;
- `excel_write_range`, `excel_clear_range`, sheet add/rename/delete, formula assignment, and table creation for guarded mutations.

Excel mutations accept optional SHA-256 preconditions and use staged validation, backup, and atomic publication. `.xlsm` mutations preserve the VBA project only when the staged archive contains byte-identical VBA content. A macro-bearing `.xlsx` is rejected. Workbook ZIP structure and expansion are bounded before OpenPyXL parsing.

DOCX:
- `docx_inspect`, `docx_read`, and `docx_find`;
- `docx_replace_text`, `docx_insert_paragraph`, `docx_replace_table_cell`, and `docx_create`.

DOCX mutation is deliberately conservative. `.docm`, comments, tracked changes, headers/footers, arbitrary WordprocessingML mutation, and replacements that span differently formatted runs fail closed rather than guessing a formatting result. DOCX archives are validated for traversal, duplicate members, encryption, CRC failure, expansion, and required package members.

PDF:
- `pdf_inspect` and `pdf_metadata`;
- `pdf_page_text` and `pdf_extract_text` with explicit page/character bounds and optional file-backed delivery;
- `pdf_create_from_text` and `pdf_create_from_markdown` for deterministic new PDFs.

Encrypted PDFs expose encryption state but are rejected for text extraction. PDF creation supports A4/Letter, built-in PDF fonts, and a documented Markdown subset; it is not an arbitrary editor for existing PDFs.

All document mutations use per-target process/inter-process locking. Same-file writes serialize while unrelated document paths can proceed independently. Before atomic publication, the expected final SHA-256 is durably checkpointed into operation recovery so a crash after replacement can be reconciled without blind replay.

## Browser

Optional Playwright tools provide both legacy selector actions and a structure-first semantic browser surface:

- `browser_open_page` for session navigation/reuse;
- `browser_snapshot` for bounded semantic page structure with generation-scoped node refs;
- `browser_query` for targeted role/name/label/text/test-id lookup without returning the full tree;
- `browser_click_semantic`, `browser_fill_semantic`, and `browser_select` for semantic mutations;
- `browser_wait_for` for bounded state, URL, text, generation, and download waits instead of fixed sleeps;
- `browser_extract` for bounded text, links, tables, form fields, or a selected subtree;
- `browser_tabs` for explicit list/activate/close behavior;
- `browser_upload` for explicit local-file attachment;
- `browser_download` for controlled, sanitized, byte-limited downloads;
- selector-based click/fill and screenshots for compatibility and fallback;
- close.

Semantic refs are valid only for the snapshot generation that produced them. Navigation, semantic mutations, and tab changes invalidate previous refs. Ambiguous semantic mutation targets fail closed unless the caller explicitly selects a match.

Sessions use isolated browser contexts. Compatible sessions can share a browser process while remaining isolated at the context level. Semantic snapshot/action counters and download bytes are exposed through runtime health.

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

## Local operations console

The loopback Control Panel at `http://127.0.0.1:8766/` is the operator-facing view of Supervisor/runtime state.

Use it to inspect:

- incident-first runtime and Secure Tunnel health;
- resource/storage budgets and performance summaries;
- Recent Jobs, including status, command/cwd metadata, bounded stdout/stderr, and guarded cancellation;
- Recent Operations, including search, outcome filters, paging, target inspection, and safe audit metadata;
- transport history, workflow/recovery state, execution-routing distribution/recent decisions, tool activity, Doctor checks, and redacted diagnostics export.

Detailed panel endpoints are control-token guarded. The panel is intended for loopback use, not direct untrusted-network exposure.

## Health and discovery

`server_health` reports runtime/platform capability state plus resource/job/browser/workflow/recovery summaries and a bounded in-memory `execution_router` snapshot with policy, route distribution, fallback/no-route/rejection counts, native/semantic/raw usage, latency, and recent decisions.

Use health metadata for diagnostics, not as a substitute for validating the external effect you care about.

## External MCP extensions (v0.8)

When enabled, configured external MCP tools are registered directly on the server surface alongside native tools:

- **Naming convention**: Tools use the namespaced pattern `external.<extension_id>.<remote_tool_name>` (e.g., `external.fixture.read_value`).
- **Profile eligibility**: External tools must belong to the active profile (`MCP_TOOL_PROFILE`). In `minimal`, only approved read-only external tools are allowed. In `full`, tools require explicit profile assignment in their operator configuration.
- **Output format**: External tool invocations return the standard Core `ExtensionResult` structure with `ok`, `source_type="external_mcp"`, `extension_id`, `provider`, `tool`, `outcome`, `content`, `structured_content`, `truncated`, `limits`, `artifact`, `error`, and `message`.
- **Advisory routing**: The execution router integrates external tools via `external.mcp` candidates when operator intent mappings match. Determinism is capped at 4, preserving native priority on ties. Router recommendations are advisory and do not execute external tools automatically.

`MCP_MAX_RUNTIME_ADMISSION_RECORDS` bounds each durable resource/capacity request table (default 10000, range 1–100000). New admissions prune only the oldest released records; when unresolved records fill the quota, admission fails before dispatch and existing fences remain. A resource request permits at most 256 claims and 64 KiB of UTF-8 keys. Workflow capabilities additionally require the current live lease, matching attempt/execution admission, and no cancellation request.
