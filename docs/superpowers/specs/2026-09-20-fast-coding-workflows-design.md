# Fast Coding Workflows Design

Date: 2026-09-20

Status: Proposed for implementation

## Purpose

Add four composable MCP capabilities that shorten the repository understanding, editing, and verification loop without weakening the server's existing filesystem safety, auditability, bounded-output behavior, or Windows support:

1. consolidated code-context lookup;
2. transactional unified patch application;
3. conservative affected-test discovery;
4. change-aware verification orchestration.

The design assumes large Git repositories. Operations must remain bounded, cancellable through existing request and process controls where applicable, deterministic for identical repository state, and explicit when analysis is incomplete.

## Current Verified Baseline

The server currently exposes 61 tools across filesystem, terminal, process, Windows diagnostics, project intelligence, testing, Git, browser, desktop, memory, and durable-job domains.

Relevant existing behavior:

- Python metadata is parsed through `ast` and cached with file-count and byte budgets.
- Project intelligence can find Python functions, classes, imports, and module dependency edges.
- Filesystem mutations use keyed locks, atomic replacement, backups, audit records, and optional SHA-256 preconditions.
- `safe_refactor` edits one file, optionally validates it, and rolls it back when validation fails.
- Testing tools separately run pytest, Ruff, and mypy and return bounded structured summaries.
- Git tools expose status, diff summaries, and commit summaries, but not patch contents or affected-test analysis.
- Large outputs can be paginated or delivered through artifacts.

Before this design was written, 347 tests passed, Ruff reported no violations, mypy reported no issues in 61 source files, and the quick benchmark completed successfully. These results establish the pre-implementation baseline only.

## Non-Goals

This phase will not add:

- a general Language Server Protocol client;
- Tree-sitter or non-Python semantic indexing;
- embedding-based semantic search;
- Git staging, committing, branching, or pushing tools;
- MCP resources, prompts, completions, sampling, or protocol-level tasks;
- an always-running repository watcher;
- automatic source-code generation or autonomous commits.

The new tools may operate on non-Python text patches, but semantic code relationships and affected-test inference are Python-first in this phase.

## Shared Design Principles

### Boundaries and reuse

Each capability receives its own service module and registry-facing tool function. Shared primitives belong in existing core modules only when they are genuinely cross-domain. The implementation will reuse:

- `resolve_path` for path normalization and server-root policy;
- `RESOURCE_LOCKS` for mutation serialization;
- existing backup, recovery-journal, audit, output-delivery, and executor services;
- `PYTHON_METADATA_CACHE` and project traversal exclusions;
- response pagination and compact error translation.

### Large-repository behavior

Every graph or scan operation must accept explicit work ceilings and report when any ceiling was reached. Results must distinguish:

- `complete`: all relevant candidates were examined;
- `truncated`: a configured file, result, edge, byte, or time limit was reached;
- `conservative_fallback`: precision was insufficient and a broader verification action is required.

No tool may silently claim completeness after a bounded scan.

### Repository identity and invalidation

Repository-scoped caches use the resolved Git worktree root when available and otherwise the resolved requested root. Cache entries are versioned by normalized path plus the existing file-version signal. Changed, created, deleted, copied, or moved Python files invalidate their own metadata and any derived repository index generation.

The first implementation may build derived relationships lazily from cached per-file metadata. It must not introduce a persistent database or background watcher. Concurrent identical read-only builds use the existing single-flight pattern so only one caller performs the work.

### Output contracts

All four tools return structured output with:

- `ok`;
- resolved repository or target paths;
- completeness and truncation fields;
- elapsed-duration metadata where useful;
- bounded diagnostic samples;
- stable error codes with actionable remediation.

Large result sets use offset pagination or opaque snapshots according to whether stable traversal is required. Tool descriptions and field descriptions remain concise to avoid materially increasing the tool catalog.

## Capability 1: Consolidated Code Context

### Tool contract

Add `code_context` to the project tool domain.

Primary inputs:

- repository or file path;
- symbol name, optionally qualified;
- match mode: exact or contains;
- relationship depth, initially limited to one or two;
- flags selecting definition, callers, callees, references, imports, and related tests;
- maximum files, relationships, and returned source characters;
- offset or cursor for paginated relationships.

Primary output:

- matched symbol definitions with file, line range, signature, kind, and qualified name;
- bounded source excerpts when requested;
- incoming call candidates;
- outgoing call candidates;
- reference candidates;
- imports used by the defining module;
- related test candidates;
- relationship confidence and evidence;
- parse errors and completeness metadata.

### Relationship model

The Python metadata index will be extended with compact records for:

- defined functions and classes;
- loaded names and qualified attribute chains;
- direct call expressions;
- decorators and base-class expressions when useful;
- test functions and test-file ownership signals.

Relationships are conservative candidates, not runtime call guarantees. Every relationship contains an evidence classification:

- `exact_qualified`: statically resolved module and qualified symbol;
- `import_bound`: resolved through a direct import alias;
- `same_module`: matched within the current module;
- `name_only`: unresolved lexical name match.

The response exposes these classifications instead of presenting name-only matches as certain callers.

### Scaling strategy

- Reuse cached per-file metadata and parse each unchanged file at most once per process generation.
- Build name-to-definition and module-to-import indexes once per repository generation.
- Resolve only the requested symbol neighborhood instead of materializing a whole call graph for every request.
- Sort files and relationships deterministically.
- Cap source excerpts independently from relationship counts.
- Avoid retaining full AST objects after metadata extraction.

### Ambiguity handling

An unqualified symbol may match multiple definitions. The tool returns all bounded matches with `ambiguous=true`; it does not choose one silently. Callers can repeat the request with a qualified name or file constraint.

## Capability 2: Transactional Unified Patch Application

### Tool contract

Add `apply_patch` to the filesystem tool domain.

Primary inputs:

- unified diff text;
- optional repository root;
- `dry_run`;
- optional expected SHA-256 map keyed by repository-relative path;
- backup policy;
- optional validation command and validation working directory;
- validation timeout;
- maximum patch bytes, files, and hunks.

Primary output:

- parsed file operations and hunk counts;
- created, modified, deleted, and renamed paths;
- before and after hashes;
- dry-run status;
- validation result;
- rollback status;
- backup references;
- compact conflict information with file and hunk identity.

### Supported patch subset

The parser supports standard Git-style unified diffs for:

- file creation from `/dev/null`;
- file deletion to `/dev/null`;
- text-file modification;
- rename metadata when accompanied by unambiguous source and destination paths;
- multiple hunks and multiple files.

The first version rejects binary patches, submodule changes, mode-only changes, symlink creation, combined merge diffs, path traversal, absolute patch paths, duplicate target operations, overlapping hunks, malformed line counts, and ambiguous rename combinations.

Rejected syntax returns a stable parse or unsupported-feature error before mutation begins.

### Atomicity model

Patch application is all-or-nothing within one server process:

1. parse and validate the entire patch without writing;
2. resolve every source and destination beneath the permitted root;
3. verify operation uniqueness and configured limits;
4. acquire all affected resource locks in deterministic normalized-path order;
5. reread current bytes and verify expected hashes;
6. calculate every resulting file in memory and validate Python syntax for affected `.py` and `.pyi` files;
7. create recoverable backups or preimage snapshots before the first mutation;
8. write, rename, and delete using temporary files and atomic replacement where the platform permits;
9. run the optional validation command;
10. on any failure, restore every preimage and remove newly created targets;
11. invalidate affected metadata caches and record the final audit and recovery outcome.

The mutation transaction is not crash-atomic across several filesystem paths. The existing durable recovery journal records intent and completion so an interrupted operation is visible as uncertain and is never silently replayed.

### Hunk application

Hunks apply at their declared positions against the exact preimage. No fuzzy search is permitted in the transactional tool. Exact context makes conflicts deterministic and prevents a plausible-looking patch from modifying the wrong block. The error reports the first mismatched context line without dumping sensitive file contents.

### Encoding and newline preservation

Text decoding reuses existing encoding detection. Unchanged files preserve their detected encoding and dominant newline convention. A new file defaults to UTF-8 unless a supported encoding option is supplied. Mixed or undecodable binary-like content is rejected.

## Capability 3: Affected-Test Discovery

### Tool contract

Add `affected_tests` to the testing tool domain.

Primary inputs:

- repository path;
- explicit changed paths or a Git comparison base;
- maximum files, dependency depth, and returned tests;
- whether to include direct test-file changes;
- fallback policy: report-only or select-full-suite.

Primary output:

- normalized changed paths;
- selected test files and optional pytest node IDs when reliably known;
- evidence for every selection;
- modules traversed;
- confidence summary;
- completeness and fallback recommendation.

### Change discovery

When paths are not supplied, the tool obtains changed files from Git using tracked staged and unstaged differences plus untracked files. A comparison base may be supplied for branch or commit analysis. Git errors are returned explicitly rather than treated as an empty change set.

### Selection algorithm

The algorithm is conservative and deterministic:

1. include changed files already recognized as tests;
2. map changed Python files to module names;
3. include tests that directly import changed modules;
4. traverse reverse internal-import edges up to the configured depth and include tests importing affected parents;
5. include conventional sibling or mirrored tests when names map unambiguously;
6. include tests containing exact qualified references found by the code index;
7. detect repository-wide risk triggers.

Repository-wide risk triggers include changes to test configuration, dependency manifests, shared test fixtures, package initialization with broad reach, test plugins, build configuration, or files that cannot be mapped safely. These triggers recommend the full suite.

### Confidence and fallback

The tool never equates an empty selected set with proof that no tests are needed. It returns one of:

- `focused`: evidence supports the returned subset;
- `focused_plus_full_recommended`: focused tests are useful but coverage is uncertain;
- `full_suite`: broad or unmappable changes require the complete suite;
- `none`: only when inputs are confidently outside executable or test configuration scope.

Each selected test includes evidence such as `changed_test`, `direct_import`, `reverse_dependency`, `naming_convention`, or `exact_reference`.

## Capability 4: Change-Aware Verification

### Tool contract

Add `verify_changes` to the testing tool domain.

Primary inputs:

- repository path;
- explicit changed paths or Git comparison base;
- selected checks: syntax, Ruff, mypy, pytest;
- affected-only preference;
- full-suite fallback policy;
- fail-fast and maximum failure count;
- per-stage and total timeout ceilings;
- whether Ruff may apply safe fixes, defaulting to false.

Primary output:

- detected changes;
- affected-test decision and evidence summary;
- ordered stage results;
- normalized diagnostics;
- skipped-stage reasons;
- aggregate status and duration;
- whether verification was focused or full.

### Execution plan

Stages execute from cheapest and most local to broader checks:

1. validate syntax for changed Python files;
2. run Ruff on changed supported files or the configured repository target;
3. run mypy on the narrowest sound target supported by the repository configuration;
4. run selected affected pytest targets;
5. run the full test suite when required by the affected-test decision and fallback policy.

The tool reuses the existing Python-environment selection, bounded process execution, artifact delivery, and parsers. Common diagnostic fields are normalized to file, line, column, severity, code, message, and source where the underlying tool provides them.

The orchestration service calls shared internal runners rather than invoking registered MCP tools through the MCP layer. Existing `run_pytest`, `run_ruff`, and `run_mypy` behavior remains backward compatible.

### Failure behavior

- A syntax failure prevents later Python analysis but does not erase completed stage evidence.
- Tool absence or invalid configuration is an explicit error stage, not a pass.
- Timeout terminates the owned process tree through the existing executor behavior.
- `fail_fast=true` stops after the first failed stage; otherwise independent stages may continue when doing so is sound.
- The aggregate result is successful only when every required stage passes and no required stage is unknown or timed out.

### Durable jobs

The synchronous tool keeps the existing maximum request timeout. Callers that need longer verification can submit the equivalent command through the existing durable-job subsystem. This phase will not introduce a second job abstraction.

## Registration and Compatibility

The four tools are additive. Existing tool names, parameter contracts, behavior, and response fields remain unchanged.

Registration order remains deterministic:

- `code_context` follows existing project-intelligence tools;
- `apply_patch` follows existing filesystem editing tools;
- `affected_tests` and `verify_changes` follow existing testing tools.

The server version and README capability list will be updated according to the repository's current release convention. `CHANGELOG.md` will receive one entry under the actual implementation date for each completed capability, grouped according to its existing format.

## Security and Safety

- All supplied paths pass through existing path resolution and permission behavior.
- Patch paths are treated as untrusted input and normalized before lock acquisition or file access.
- Patch and validation commands are audited without storing source content or command bodies; only bounded metadata and command hashes are recorded according to existing conventions.
- Validation output follows existing bounded-output and artifact policies.
- No test-selection result can suppress a required full-suite fallback without making that policy visible in the response.
- Mutating tools preserve backups and recovery-journal evidence.
- Generated diagnostics and source excerpts are bounded to avoid accidental context flooding.

## Testing Strategy

### Code context

- qualified and unqualified definitions;
- imported aliases;
- same-module and cross-module calls;
- methods sharing a short name;
- ambiguous and name-only matches;
- decorators, async functions, nested functions, and parse errors;
- cache reuse, invalidation, concurrent single-flight builds, and work ceilings;
- deterministic ordering and pagination.

### Patch application

- create, modify, delete, rename, and multi-file patches;
- multiple hunks and newline edge cases;
- dry-run with no filesystem changes;
- stale expected hash;
- context mismatch and malformed counts;
- traversal, absolute path, duplicate target, binary, symlink, mode-only, and unsupported diff rejection;
- Python syntax rejection before mutation;
- validation failure rollback;
- failure during an intermediate write followed by complete restoration;
- concurrent patches acquiring locks without deadlock;
- cache invalidation, audit records, backups, and recovery outcomes.

### Affected tests

- changed tests;
- direct imports and alias imports;
- reverse dependency traversal;
- conventional and mirrored test names;
- exact references;
- cycles and depth limits;
- untracked files;
- Git comparison bases;
- fixture, configuration, manifest, and unmappable-change full-suite triggers;
- empty and non-code change handling;
- deterministic evidence and result limits.

### Verification

- stage ordering and target selection;
- focused and full-suite flows;
- syntax, Ruff, mypy, and pytest failures;
- missing executable and invalid configuration;
- fail-fast and continue behavior;
- timeouts and process-tree cleanup;
- normalized diagnostics;
- aggregate status rules;
- bounded output and artifact delivery.

### Regression and integration

After each capability, run its focused tests and directly affected existing tests. Before completion, run:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy core tools scripts tests main.py local_pc_mcp.py
.\.venv\Scripts\python.exe -m compileall -q core tools scripts tests main.py local_pc_mcp.py
.\.venv\Scripts\python.exe main.py --check
.\.venv\Scripts\python.exe -m scripts.health_check --json
.\.venv\Scripts\python.exe -m scripts.perf_benchmark --profile quick
```

Performance tests will cover warm and cold repository-index construction, context lookup on a synthetic large project, multi-file patch parsing and dry-run, affected-test analysis, verification orchestration overhead, and tool-catalog size.

## Commit and Delivery Strategy

Implementation is divided into four reviewable feature commits after this design commit:

1. consolidated code-context lookup and tests;
2. transactional unified patch application and tests;
3. affected-test discovery and tests;
4. change-aware verification, documentation, integration tests, and benchmark coverage.

Commit subjects will describe the delivered behavior directly and will not contain roadmap labels or references to automated authorship. Each commit must pass its focused verification before the next feature begins.

After final regression and benchmark verification, the current branch will be pushed to its configured upstream. A push failure will be reported with its exact cause and will not be represented as successful delivery.

## Acceptance Criteria

The work is complete only when:

- all four tools are registered with structured output and correct annotations;
- each tool enforces its documented limits and reports incomplete analysis;
- `code_context` returns evidence-qualified relationships without retaining full ASTs;
- `apply_patch` proves dry-run behavior, hash conflicts, all-or-nothing rollback, and path safety through tests;
- `affected_tests` demonstrates focused selection and conservative full-suite fallback;
- `verify_changes` demonstrates focused and full verification with normalized stage results;
- existing tool contracts remain backward compatible;
- documentation and changelog match implemented behavior;
- the complete verification sequence passes, or any environmental blocker is explicitly reported;
- four feature commits plus the design commit exist with clean, conventional subjects;
- the final branch push succeeds.
