# Developer Intelligence P1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver multi-language LSP intelligence, semantic edits, operational Git, normalized diagnostics, and durable incremental validation.

**Architecture:** Add a focused language domain and reuse existing executor, locks, backups, audit, testing runners, and durable jobs. Keep every new operation bounded and expose incomplete or unavailable analysis explicitly.

**Tech Stack:** Python 3.10+, MCP Python SDK, JSON-RPC/LSP 3.x, Git CLI, SQLite durable jobs, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-20-developer-intelligence-p1-design.md`

## Global Constraints

- Preserve all existing 65 tool contracts additively.
- Never use shell interpolation, force Git operations, implicit server commands, or paths outside the selected workspace.
- Bound message bytes, files, edits, diagnostics, results, subprocesses, and elapsed time.
- Mutations require deterministic locks, preconditions, backups, rollback, and audit metadata.
- Commit subjects describe behavior and contain no roadmap labels or automated-authorship references.

## Review Focus

- UTF-16 LSP positions containing astral Unicode must map to correct Python offsets.
- A language server that hangs, emits malformed framing, or exits early must fail boundedly and leave no process.
- Overlapping, stale, or out-of-workspace workspace edits must fail before any write.
- Git pathspecs and revisions beginning with options must never become Git options.
- Validation watches must debounce bursts, surface scan truncation, and stop through durable cancellation.

---

### Task 1: Multi-language LSP intelligence

**Files:**
- Create: `tools/language/lsp.py`, `tools/language/registry.py`, `tools/language/__init__.py`
- Modify: `core/registry.py`, `scripts/health_check.py`, `tests/test_mcp_integration.py`
- Test: `tests/test_lsp_client.py`, `tests/fixtures/fake_lsp_server.py`

**Interfaces:**
- Produces: `LspClient.request(method, params)`, UTF-16 converters, and MCP tools `symbol_definition`, `symbol_references`, `document_symbols`, `workspace_symbols`, `symbol_hover`, `call_hierarchy`, `language_diagnostics`.

- [ ] Write framing, Unicode, timeout, malformed-response, capability, path-boundary, and MCP schema tests; run them and confirm missing-module failures.
- [ ] Implement Content-Length JSON-RPC transport, lifecycle, bounded notification collection, server discovery, normalized locations/symbols, and conservative structural fallback.
- [ ] Run `pytest tests/test_lsp_client.py tests/test_mcp_integration.py -q`, Ruff, and mypy for the language domain.
- [ ] Commit with `feat: add language server code intelligence`.

### Task 2: Semantic workspace edits

**Files:**
- Create: `tools/language/edits.py`
- Modify: `tools/language/registry.py`, `core/tooling.py`
- Test: `tests/test_semantic_edits.py`, `tests/test_lsp_client.py`

**Interfaces:**
- Produces: `prepare_workspace_edit(...)`, `apply_workspace_edit(...)`, `rename_symbol`, and `apply_code_action`.

- [ ] Write failing tests for rename, UTF-16 edits, multi-file edit ordering, document versions, stale hashes, overlaps, external URIs, rollback, validation failure, and command-only code actions.
- [ ] Normalize `changes` and `documentChanges`; calculate post-images before writing; apply under `RESOURCE_LOCKS.sync(*paths)` with backups and `_restore`-equivalent rollback.
- [ ] Register bounded audited tools; never execute LSP commands returned by code actions.
- [ ] Run focused tests/static checks and commit with `feat: apply semantic language server edits`.

### Task 3: Safe operational Git

**Files:**
- Create: `tools/git/service.py`
- Modify: `tools/git/registry.py`, `core/tooling.py`, `scripts/health_check.py`, `tests/test_mcp_integration.py`
- Test: `tests/test_git_operations.py`, `tests/test_git_tools.py`

**Interfaces:**
- Produces: `git_diff`, `git_show`, `git_blame`, `git_merge_base`, `git_changed_files`, `git_branch_list`, `git_conflicts`, `git_create_branch`, `git_stage`, `git_commit`, `git_restore_file`.

- [ ] Write disposable-repository tests for pagination, binary data, rename/conflict parsing, invalid revisions, pathspec option injection, dirty/staged states, empty commits, branch collisions, restore preconditions, and audit annotations.
- [ ] Extract shared repository/root/runner validation into `tools/git/service.py` and keep existing summary contracts unchanged.
- [ ] Implement read operations first, then narrowly scoped mutations without reset/clean/force/push.
- [ ] Run focused tests/static checks and commit with `feat: add guarded git workflows`.

### Task 4: Unified diagnostics

**Files:**
- Create: `tools/testing/diagnostics.py`
- Modify: `tools/testing/runners.py`, `tools/testing/verification.py`, `tools/testing/registry.py`, `tools/language/lsp.py`
- Test: `tests/test_diagnostics.py`, `tests/test_verify_changes.py`

**Interfaces:**
- Produces: `Diagnostic`, `normalize_*`, `merge_diagnostics`, and MCP tool `collect_diagnostics` while retaining legacy runner keys.

- [ ] Write failing normalization tests for Ruff, mypy, pytest, LSP severities/codes/ranges, Windows paths, duplicates, caps, unavailable backends, and stable ordering.
- [ ] Move the diagnostic TypedDict and parsers into one module; aggregate selected backends with explicit stage status and `required_unknown`.
- [ ] Run existing runner and verification tests unchanged plus new diagnostics tests.
- [ ] Commit with `feat: unify developer diagnostics`.

### Task 5: Durable incremental validation watch

**Files:**
- Create: `tools/testing/watch.py`, `scripts/validation_watch.py`
- Modify: `tools/testing/registry.py`, `core/tooling.py`, `scripts/health_check.py`, `tests/test_mcp_integration.py`
- Test: `tests/test_validation_watch.py`

**Interfaces:**
- Produces: snapshot/diff/debounce helpers and MCP tool `start_validation_watch`; consumes existing `JobStore` and `verify_changed_repository`.

- [ ] Write failing tests for initial snapshot, burst debounce, create/change/delete batches, ignores, scan truncation, JSONL generations, validation failure continuation, maximum runtime, durable submission idempotency, and cancellation.
- [ ] Implement bounded polling worker and submit it through `JobStore` using the repository interpreter and a stable request fingerprint.
- [ ] Run focused job/watch/integration tests and static checks.
- [ ] Commit with `feat: add incremental validation watches`.

### Task 6: Documentation, performance, and delivery

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `scripts/perf_benchmark.py`, `tests/test_perf_benchmark.py`

**Interfaces:**
- Consumes every P1 tool and pins the final catalog count.

- [ ] Document server discovery, explicit commands, safety, Git semantics, diagnostic schema, watch lifecycle, and examples; add dated changelog behavior entries.
- [ ] Benchmark LSP framing/normalization, workspace-edit planning, Git parsing, diagnostic merge, and watch snapshot diff without requiring optional external servers.
- [ ] Run compileall, startup, health, Ruff, mypy, full pytest, and quick benchmark; fix only verified regressions and rerun affected gates.
- [ ] Run `graphify update .`, inspect `origin/main...HEAD`, verify natural commit boundaries, and push `main` without force.
