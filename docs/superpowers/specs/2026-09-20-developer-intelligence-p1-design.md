# Developer Intelligence P1 Design

Date: 2026-09-20

Status: Approved for implementation by the user's instruction to complete P1 without intermediate approval pauses.

## Purpose

Complete the five P1 capabilities identified in the source-backed MCP audit:

1. multi-language intelligence through Language Server Protocol (LSP), with a bounded structural fallback;
2. semantic symbol rename and compiler-backed refactoring actions;
3. safe operational Git workflows;
4. one normalized diagnostic contract across language servers, linters, type checkers, and tests;
5. durable incremental validation watch jobs.

The implementation extends the existing Python/Windows MCP without weakening path handling, bounded output, audit logging, recovery, or backwards compatibility.

## Architecture

### Language intelligence

Add a `tools/language` domain containing a synchronous LSP 3.x client. Each request launches an explicitly configured language-server command, performs `initialize`/`initialized`, opens only the requested document, sends one bounded request, then performs `shutdown`/`exit`. Per-request processes avoid leaked servers and cross-repository state. Commands are passed as arrays and never through a shell.

The server automatically discovers `pyright-langserver` for Python. Other languages accept an explicit command, enabling `typescript-language-server`, `gopls`, `rust-analyzer`, or any compatible server without hard-coding installations. Requests expose definition, references, document/workspace symbols, hover/type information, call hierarchy, diagnostics, rename preparation, rename, and code actions.

When no language server is available, `document_symbols` uses a bounded structural fallback. Python continues to use the native AST index. Common brace-based languages use a conservative lexical symbol scanner with explicit `backend=structural_fallback` and `complete=false`; it never presents lexical guesses as semantic certainty.

### Semantic edits

LSP `WorkspaceEdit` responses are normalized into exact text edits grouped by file. Before mutation, every source file is read and hashed. Edits are validated for overlap and applied from the end of each document. The final byte changes are committed under deterministic multi-path resource locks with backups and rollback on any write or validation failure.

`rename_symbol` uses `textDocument/prepareRename` followed by `textDocument/rename`. `apply_code_action` requests a named refactor/source action (including organize imports or extract actions exposed by the active language server) and applies only returned workspace edits. Commands returned by a server are reported but never executed implicitly.

### Operational Git

Extend `tools/git` with bounded tools for:

- full diff hunks, show, blame, merge-base, changed files, branches, and conflicts;
- branch creation, staging, committing, and restoring explicitly named paths.

All operations first resolve and verify the Git worktree root. Revisions reject option-like values. Mutations are argument-array subprocesses, audited, bounded, and annotated accurately. `git_restore_file` requires an explicit source revision and paths; it cannot reset the repository, clean untracked files, or use `--hard`. Commit refuses an empty staged index and does not push.

### Diagnostics

Use one additive diagnostic schema:

```text
file, line, column, end_line, end_column,
severity, code, message, source, fix_available
```

Existing `run_pytest`, `run_ruff`, and `run_mypy` keep their legacy response keys while returning normalized diagnostics. LSP publish-diagnostics messages use the same schema. `collect_diagnostics` combines requested backends, deduplicates stable identities, caps results, and marks timeouts/unavailable backends as required unknowns rather than success.

### Incremental validation watch

`start_validation_watch` submits a durable Python worker through the existing `JobStore`. The worker polls a bounded repository snapshot, debounces changes, batches modified paths, calls the same verification planner, and appends one JSON line per generation. It supports maximum runtime, poll interval, debounce interval, ignore globs, and the existing job cancellation/output APIs.

The worker persists only output and normal durable-job state. It does not maintain a second database or silently retry failed validations. Scan truncation triggers a full-suite verification decision and is visible in output.

## Safety and Limits

- No shell interpolation for LSP, Git, or validation commands.
- Explicit maximum message bytes, files, edits, diagnostics, results, and timeouts.
- LSP paths must resolve inside the requested workspace before edits are applied.
- UTF-16 LSP positions are converted correctly to Python string offsets.
- Overlapping or stale edits fail before mutation.
- Git mutators never force, reset hard, clean, push, or rewrite history.
- Watch jobs reuse durable admission, cancellation, retention, and process-tree cleanup.
- Every mutating tool writes bounded audit metadata without source content.

## Registration and Compatibility

Existing 65 tools retain their signatures and top-level response fields. New tools are added in the language, Git, testing, and jobs domains. Health checks and catalog tests pin exact unique registration. README and changelog describe availability requirements and safety boundaries.

## Verification

Feature tests cover JSON-RPC framing, UTF-16 coordinates, process timeout/error behavior, workspace-edit rollback, Git repository guards and mutations, diagnostic normalization/deduplication, watch debounce/incremental batches/cancellation, MCP schemas, and catalog uniqueness.

Final verification runs compileall, startup and health checks, Ruff, mypy, the complete pytest suite, the quick benchmark, Graphify refresh, diff inspection, and a non-force push to `origin/main`.

## Commit Strategy

Use reviewable commits with natural Conventional Commit subjects:

1. language-server intelligence and structural fallback;
2. semantic refactoring edits;
3. operational Git workflows;
4. unified diagnostics;
5. incremental validation watch;
6. documentation and final integration adjustments when required.

Commit subjects must not contain roadmap priority labels or references to automated authorship.
