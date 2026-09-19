# Fast Coding Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add consolidated Python code context, transactional unified patching, conservative affected-test selection, and change-aware verification to the Windows developer MCP.

**Architecture:** Extend the existing project, filesystem, and testing domains with focused service modules and thin MCP registrations. Reuse current path resolution, file metadata cache, resource locks, backups, recovery journal, audit records, bounded execution, pagination, and structured response conventions; avoid new runtime dependencies and persistent indexes.

**Tech Stack:** Python 3.10+, MCP Python SDK 2.0, Pydantic 2, stdlib `ast`, `difflib`, `pathlib`, `subprocess`, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-20-fast-coding-workflows-design.md`

## Global Constraints

- Preserve all existing tool names, parameters, and response fields.
- Keep semantic analysis Python-first and report bounded-scan incompleteness explicitly.
- Add no runtime dependency and no persistent database or repository watcher.
- Resolve and validate every filesystem path before mutation.
- Acquire multi-path locks in deterministic normalized-path order.
- Treat multi-file patch application as all-or-nothing within one process and expose crash-interrupted work through the existing recovery journal.
- Do not silently suppress full-suite verification when affected-test confidence is insufficient.
- Keep tool descriptions and schemas concise because the current catalog already serializes to roughly 56 KiB.
- Support Windows PowerShell and Python 3.10 through 3.12.

## Review Focus

- Ambiguous short symbol names must return evidence-ranked candidates without claiming exact resolution; pin in Task 1.
- Patch paths that become unsafe only after rename normalization must fail before any write; pin in Task 2.
- A write failure after earlier files changed must restore modified/deleted files and remove created files; pin in Task 2.
- Changes to shared fixtures, test configuration, or dependency manifests must select the full suite; pin in Task 3.
- A timed-out or unavailable required verifier must make aggregate verification fail rather than appear skipped-successfully; pin in Task 4.

---

### Task 1: Consolidated Python Code Context

**Files:**
- Modify: `tools/project/index.py`
- Create: `tools/project/context.py`
- Modify: `tools/project/registry.py`
- Test: `tests/test_code_context.py`
- Modify: `tests/test_mcp_integration.py`
- Modify: `scripts/perf_benchmark.py`

**Interfaces:**
- Consumes: `service.iter_python_files(root, max_files)`, `service.python_metadata(path)`, `service.module_name(root, path)`, `page(...)`, `resolve_path(...)`.
- Produces: `CodeReference`, `CodeCall`, and extended `PythonFileMetadata`; `build_context_index(root: Path, max_files: int) -> ContextIndex`; `lookup_code_context(...) -> dict[str, Any]`; MCP tool `code_context(...) -> dict[str, Any]`.

- [ ] **Step 1: Add failing metadata and relationship tests**

Create fixtures containing imported aliases, qualified calls, same-module calls, duplicate short method names, async/nested functions, decorators, parse errors, and test modules. Assert compact metadata records names and calls without retaining AST nodes:

```python
metadata = build_python_metadata(source)
assert ("svc.create", 4) in {(item.name, item.line) for item in metadata.calls}
assert not any(isinstance(value, ast.AST) for value in vars(metadata).values())
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_code_context.py -q
```

Expected: failure because call/reference metadata and the context module do not exist.

- [ ] **Step 2: Extend compact Python metadata**

Add immutable metadata records:

```python
@dataclass(frozen=True, slots=True)
class NameReference:
    name: str
    line: int
    column: int
    context: Literal["load", "store"]

@dataclass(frozen=True, slots=True)
class CallMetadata:
    name: str
    line: int
    column: int
```

Extend `PythonFileMetadata` with `references: tuple[NameReference, ...]` and `calls: tuple[CallMetadata, ...]`. Extract dotted `Name`/`Attribute` chains deterministically, deduplicate by location, cap pathological chains, and include new records in cache size estimation.

- [ ] **Step 3: Build the lazy repository context index**

Implement `tools/project/context.py` with immutable definition, import-binding, and relationship records. Index modules and definitions once per repository generation, use `SingleFlight` for concurrent identical builds, key the cache by root plus observed file-version summary, and retain compact metadata rather than ASTs.

Resolution order:

```python
EVIDENCE_RANK = {
    "exact_qualified": 0,
    "import_bound": 1,
    "same_module": 2,
    "name_only": 3,
}
```

Return definitions, callers, callees, references, imports, and related tests with evidence fields. Mark multiple definition matches as ambiguous. Report scanned files, parse errors, file-limit truncation, relationship-limit truncation, and source-excerpt truncation.

- [ ] **Step 4: Register `code_context`**

Add a read-only structured tool with bounded Pydantic inputs:

```python
def code_context(
    path: PathArg,
    symbol: Annotated[str, Field(min_length=1, max_length=500)],
    match: Literal["exact", "contains"] = "exact",
    file_filter: Annotated[str | None, Field(max_length=32_767)] = None,
    include_source: bool = True,
    include_callers: bool = True,
    include_callees: bool = True,
    include_references: bool = True,
    include_imports: bool = True,
    include_tests: bool = True,
    max_depth: Annotated[int, Field(ge=1, le=2)] = 1,
    max_files: Annotated[int, Field(ge=1, le=50_000)] = 5_000,
    max_relationships: Annotated[int, Field(ge=1, le=1_000)] = 100,
    source_max_chars: Annotated[int, Field(ge=0, le=200_000)] = 20_000,
    offset: Annotated[int, Field(ge=0, le=1_000_000)] = 0,
) -> dict[str, Any]:
```

Invalidate the derived context generation from all filesystem mutations already invalidating `PYTHON_METADATA_CACHE`.

- [ ] **Step 5: Pin ambiguity, cache, concurrency, limits, and integration behavior**

Add tests proving:

```python
assert response["ambiguous"] is True
assert {item["evidence"] for item in response["definitions"]} <= {
    "exact_qualified", "import_bound", "same_module", "name_only"
}
assert response["complete"] is False when response["scan_truncated"]
```

Exercise twelve concurrent lookups and assert one index build. Modify one source file and assert a refreshed relationship result. Confirm tool registration and JSON-serializable structured output through the MCP client.

- [ ] **Step 6: Add a synthetic large-project benchmark and verify**

Benchmark cold build, warm lookup, and one invalidated lookup on at least 1,000 generated modules without including setup time in warm samples.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_code_context.py tests/test_project_cache.py tests/test_mcp_integration.py -q
.\.venv\Scripts\python.exe -m ruff check tools/project tests/test_code_context.py scripts/perf_benchmark.py
.\.venv\Scripts\python.exe -m mypy tools/project tests/test_code_context.py scripts/perf_benchmark.py
```

Expected: all checks pass.

- [ ] **Step 7: Commit the feature**

```powershell
git add tools/project tests/test_code_context.py tests/test_mcp_integration.py scripts/perf_benchmark.py
git commit -m "feat: add consolidated code context lookup"
```

### Task 2: Transactional Unified Patch Application

**Files:**
- Create: `tools/filesystem/patches.py`
- Modify: `tools/filesystem/registry.py`
- Modify: `core/resource_locks.py`
- Test: `tests/test_apply_patch.py`
- Modify: `tests/test_filesystem_registry.py`
- Modify: `tests/test_mcp_integration.py`
- Modify: `scripts/perf_benchmark.py`

**Interfaces:**
- Consumes: `resolve_path`, `RESOURCE_LOCKS`, filesystem encoding/atomic-write helpers, backup service, audit service, recovery journal, `run_bounded`, and project-cache invalidation.
- Produces: `PatchSet`, `FilePatch`, `PatchHunk`, `parse_unified_patch(text, limits) -> PatchSet`, `apply_patch_transaction(...) -> dict[str, Any]`, MCP tool `apply_patch(...)`.

- [ ] **Step 1: Add failing parser and dry-run tests**

Cover create, update, delete, rename, multiple files/hunks, CRLF input, no-newline markers, malformed counts, overlapping hunks, duplicate targets, binary markers, mode-only changes, combined diffs, absolute paths, `..` traversal, drive-qualified paths, and rename-normalization traversal.

Representative assertion:

```python
plan = parse_unified_patch(PATCH, PatchLimits(max_bytes=100_000, max_files=10, max_hunks=20))
assert [operation.kind for operation in plan.files] == ["modify", "create"]
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_apply_patch.py -q
```

Expected: failure because the patch service does not exist.

- [ ] **Step 2: Implement strict unified-diff parsing**

Use a line-oriented parser with explicit state transitions. Validate declared old/new line counts while parsing. Normalize `a/` and `b/` prefixes only after rejecting absolute, UNC, drive-qualified, empty, NUL-containing, and parent-segment paths. Reject unsupported headers before producing a `PatchSet`.

Use immutable models:

```python
@dataclass(frozen=True, slots=True)
class PatchHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class FilePatch:
    kind: Literal["create", "modify", "delete", "rename"]
    old_path: str | None
    new_path: str | None
    hunks: tuple[PatchHunk, ...]
```

- [ ] **Step 3: Implement exact in-memory hunk application**

Apply hunks in declared order against the exact preimage. Context or deletion mismatch raises `patch_context_mismatch` with path and hunk number but not source content. Preserve the detected encoding and dominant newline style; new files default to UTF-8. Reject undecodable/binary-like targets. Validate resulting Python text before any write.

- [ ] **Step 4: Add deterministic multi-lock support**

Extend `ResourceLocks` with synchronous multi-path acquisition:

```python
@contextmanager
def sync_many(self, paths: Iterable[str | Path]) -> Iterator[None]:
    keys = sorted({self._key(path) for path in paths})
    with ExitStack() as stack:
        for key in keys:
            stack.enter_context(self.sync(key))
        yield
```

Pin reverse-order concurrent callers and assert completion without deadlock.

- [ ] **Step 5: Implement the transaction and rollback**

`apply_patch_transaction` must parse and resolve all operations before locking, reread and hash under lock, calculate all postimages, take preimage backups, journal the operation, then mutate. Record enough in-memory preimage data to restore modified/deleted paths and remove newly created paths when any write, rename, delete, or validation step fails.

Inject a failing atomic-write function in a test where file one succeeds and file two fails:

```python
with pytest.raises(ToolError):
    apply_patch_transaction(...)
assert first.read_text() == FIRST_BEFORE
assert second.read_text() == SECOND_BEFORE
assert not created.exists()
```

Invalidate project/context caches after success and after rollback. Audit only metadata, counts, paths, command executable/count/hash, outcome, and rollback status.

- [ ] **Step 6: Register `apply_patch` and integration tests**

Add a mutating structured tool with explicit limits, `dry_run`, expected-hash mapping, backup, optional validation command/cwd, timeout, and encoding for new files. Use existing compact error translation and return per-file before/after hashes.

Test SHA conflicts, dry-run immutability, validation rollback, Python syntax preflight, backup references, recovery-journal completion, catalog registration, and MCP serialization.

- [ ] **Step 7: Benchmark and verify**

Benchmark parsing and dry-run for 1, 10, and 100 files within configured limits.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_apply_patch.py tests/test_filesystem_registry.py tests/test_resource_locks.py tests/test_recovery_journal.py tests/test_mcp_integration.py -q
.\.venv\Scripts\python.exe -m ruff check tools/filesystem core/resource_locks.py tests/test_apply_patch.py scripts/perf_benchmark.py
.\.venv\Scripts\python.exe -m mypy tools/filesystem core/resource_locks.py tests/test_apply_patch.py scripts/perf_benchmark.py
```

Expected: all checks pass.

- [ ] **Step 8: Commit the feature**

```powershell
git add core/resource_locks.py tools/filesystem tests/test_apply_patch.py tests/test_filesystem_registry.py tests/test_resource_locks.py tests/test_mcp_integration.py scripts/perf_benchmark.py
git commit -m "feat: support transactional unified patches"
```

### Task 3: Conservative Affected-Test Discovery

**Files:**
- Create: `tools/testing/impact.py`
- Modify: `tools/testing/registry.py`
- Modify: `tools/git/registry.py`
- Test: `tests/test_affected_tests.py`
- Modify: `tests/test_git_tools.py`
- Modify: `tests/test_mcp_integration.py`
- Modify: `scripts/perf_benchmark.py`

**Interfaces:**
- Consumes: project context/index metadata, `resolve_path`, bounded Git execution patterns, response pagination.
- Produces: `discover_changed_paths(repo, base) -> ChangedPaths`; `select_affected_tests(...) -> AffectedTestResult`; MCP tool `affected_tests(...) -> dict[str, Any]`.

- [ ] **Step 1: Add failing changed-file and selection tests**

Create temporary Git repositories and cover staged, unstaged, untracked, deleted, renamed, explicit-path, and comparison-base changes. Build project fixtures for direct imports, alias imports, reverse dependencies, cycles, mirrored names, exact references, changed tests, non-code docs, and unmappable Python files.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_affected_tests.py -q
```

Expected: failure because impact analysis does not exist.

- [ ] **Step 2: Extract reusable changed-path discovery**

Implement NUL-delimited Git parsing that combines:

```text
git diff --name-status -z [base]
git diff --cached --name-status -z [base when valid]
git ls-files --others --exclude-standard -z
```

Return normalized repository-relative path records with status and old path for renames. Do not swallow Git errors or treat them as no changes.

- [ ] **Step 3: Implement conservative selection**

Build reverse import edges from cached project metadata. Select tests by evidence in this order: changed test, direct import, reverse dependency, exact reference, naming convention. Deduplicate while retaining all evidence and shortest dependency depth. Sort deterministically.

Classify:

```python
Decision = Literal["focused", "focused_plus_full_recommended", "full_suite", "none"]
```

Return `full_suite` for `conftest.py`, pytest configuration, dependency manifests, packaging/build configuration, shared test plugins, repository-root package initialization with broad reach, scan truncation affecting changed modules, or unmappable executable Python changes. Return `none` only for confidently non-executable documentation/assets outside test configuration.

- [ ] **Step 4: Pin review-focus and limit behavior**

Add tests asserting shared fixture, `pyproject.toml`, requirements, and test-plugin changes choose `full_suite`. Add depth/file/result limit tests and assert `complete=false` plus at least `focused_plus_full_recommended` when analysis is truncated.

- [ ] **Step 5: Register `affected_tests` and integration tests**

Add a read-only structured tool accepting repository, explicit paths or base, maximum files/depth/results, changed-test inclusion, and fallback policy. Return test paths, optional reliable pytest node IDs, evidence, traversal counts, decision, recommendation, parse errors, and truncation fields.

- [ ] **Step 6: Benchmark and verify**

Benchmark a synthetic 1,000-module reverse graph with at least 200 test modules and changed nodes at different depths.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_affected_tests.py tests/test_git_tools.py tests/test_code_context.py tests/test_mcp_integration.py -q
.\.venv\Scripts\python.exe -m ruff check tools/testing tools/git tests/test_affected_tests.py scripts/perf_benchmark.py
.\.venv\Scripts\python.exe -m mypy tools/testing tools/git tests/test_affected_tests.py scripts/perf_benchmark.py
```

Expected: all checks pass.

- [ ] **Step 7: Commit the feature**

```powershell
git add tools/testing tools/git/registry.py tests/test_affected_tests.py tests/test_git_tools.py tests/test_mcp_integration.py scripts/perf_benchmark.py
git commit -m "feat: detect tests affected by source changes"
```

### Task 4: Change-Aware Verification Workflow

**Files:**
- Create: `tools/testing/runners.py`
- Create: `tools/testing/verification.py`
- Modify: `tools/testing/registry.py`
- Test: `tests/test_verify_changes.py`
- Modify: `tests/test_mcp_integration.py`
- Modify: `scripts/perf_benchmark.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `core/config.py` only if new immutable default ceilings are required

**Interfaces:**
- Consumes: `select_affected_tests`, changed-path discovery, `_python_for`, `run_bounded`, artifact delivery, audit service.
- Produces: reusable `run_pytest_summary`, `run_ruff_summary`, and `run_mypy_summary`; `verify_changed_repository(...) -> VerificationResult`; MCP tool `verify_changes(...) -> dict[str, Any]`.

- [ ] **Step 1: Add failing runner-parity and orchestration tests**

Extract expected response fixtures from existing testing-tool tests. Add tests for changed-file syntax failure, focused selection, full-suite fallback, explicit full mode, fail-fast, continue-safe stages, missing Ruff/mypy/pytest module, invalid config, timeout, diagnostic normalization, artifact delivery, and aggregate status.

Required timeout assertion:

```python
result = verify_changed_repository(..., stage_timeout_sec=0.05)
assert result["ok"] is False
assert result["stages"][-1]["status"] == "timed_out"
assert result["required_unknown"] is True
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_verify_changes.py -q
```

Expected: failure because shared runners and verification orchestration do not exist.

- [ ] **Step 2: Extract shared testing runners without contract drift**

Move command construction and parsing from `tools/testing/registry.py` into `runners.py`. Keep registered `run_pytest`, `run_ruff`, and `run_mypy` signatures and response fields unchanged. Add normalized diagnostics alongside legacy fields only where additive.

Normalized diagnostic shape:

```python
class Diagnostic(TypedDict, total=False):
    file: str
    line: int
    column: int
    severity: Literal["error", "warning", "note"]
    code: str
    message: str
    source: Literal["python", "ruff", "mypy", "pytest"]
```

- [ ] **Step 3: Implement verification planning and execution**

Build an ordered `VerificationPlan` from changed paths and affected-test decision. Syntax-check changed `.py`/`.pyi` files first, then Ruff, mypy, focused pytest, and conditional full pytest. Use the narrowest sound targets; when repository configuration makes narrow mypy execution unsafe, choose the configured repository target and state why.

Track each stage as `passed`, `failed`, `timed_out`, `unavailable`, or `skipped` with start/end duration, targets, bounded diagnostics, and reason. Required timed-out/unavailable stages make aggregate `ok=false`.

- [ ] **Step 4: Register `verify_changes`**

Add an open-world-write structured tool because verifiers execute repository code and optional Ruff fixes mutate files. Inputs include repository, paths/base, selected checks, affected-only, fallback policy, fail-fast, maxfail, stage/total timeouts, Ruff-fix default false, diagnostic cap, and output delivery.

Audit executable names, argument counts, target counts, selected mode, and outcome without command bodies or source contents.

- [ ] **Step 5: Pin backward compatibility and MCP integration**

Run all existing testing registry tests unchanged. Through an MCP client, verify the new tool schema, error serialization, focused success, full fallback, and bounded failed diagnostics. Assert existing three tools return their prior top-level keys.

- [ ] **Step 6: Add benchmark coverage and user documentation**

Benchmark plan construction separately from process execution and add an optional real focused verification sample. Document all four tools, safety semantics, fallback behavior, examples, and large-repository limits. Add dated changelog entries describing delivered behavior, not internal mechanics.

- [ ] **Step 7: Run feature verification**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_verify_changes.py tests/test_affected_tests.py tests/test_mcp_integration.py tests/test_output_policy.py tests/test_executor.py -q
.\.venv\Scripts\python.exe -m ruff check tools/testing tests/test_verify_changes.py scripts/perf_benchmark.py
.\.venv\Scripts\python.exe -m mypy tools/testing tests/test_verify_changes.py scripts/perf_benchmark.py
```

Expected: all checks pass.

- [ ] **Step 8: Commit the feature**

```powershell
git add tools/testing tests/test_verify_changes.py tests/test_mcp_integration.py scripts/perf_benchmark.py README.md CHANGELOG.md core/config.py
git commit -m "feat: add change-aware verification workflow"
```

### Task 5: Final Regression, Performance, and Delivery

**Files:**
- Modify only files required to fix verified regressions in the four implemented capabilities.
- Update: `graphify-out/*` through `graphify update .` (ignored generated intelligence output).

**Interfaces:**
- Consumes: the four completed tools and all existing server entrypoints.
- Produces: verified commits on the current branch and a successful upstream push.

- [ ] **Step 1: Verify the complete tool catalog and source tree**

```powershell
.\.venv\Scripts\python.exe -m compileall -q core tools scripts tests main.py local_pc_mcp.py
.\.venv\Scripts\python.exe main.py --check
.\.venv\Scripts\python.exe -m scripts.health_check --json
```

Expected: compilation, registration, and smoke checks pass with 65 unique tools.

- [ ] **Step 2: Run complete static and automated validation**

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy core tools scripts tests main.py local_pc_mcp.py
.\.venv\Scripts\python.exe -m pytest
```

Expected: all checks pass. If a broad test fails, isolate and fix the smallest failing scope before rerunning the full suite.

- [ ] **Step 3: Run and inspect performance benchmarks**

```powershell
.\.venv\Scripts\python.exe -m scripts.perf_benchmark --profile quick
```

Expected: every non-optional benchmark is `ok`; compare catalog bytes, warm/cold code-context timing, patch dry-run, affected-test analysis, and verification planning against documented budgets. Report regressions rather than hiding them.

- [ ] **Step 4: Refresh Graphify and inspect the final diff**

```powershell
graphify update .
git diff origin/main...HEAD --check
git diff origin/main...HEAD --stat
git status --short --branch
```

Expected: graph update succeeds, diff check is clean, and only intentional tracked files differ from the upstream base.

- [ ] **Step 5: Confirm commit boundaries and push**

```powershell
git log --oneline origin/main..HEAD
git push origin main
```

Expected: design/plan documentation and four conventional feature commits are visible, and `origin/main` advances to the final verified commit. If the remote rejects the push, report the exact rejection and do not force-push.
