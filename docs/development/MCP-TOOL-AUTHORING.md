# Native MCP tool authoring

This guide describes the current native implementation at the stable v0.8.0 boundary. The [agent contract](../../AGENTS.md) owns execution rules; [architecture](../architecture.md) owns runtime boundaries, and [release process](../release-process.md) owns publication.

## Registration and caller contract

`core/registry.py:create_server` detects capabilities, resolves one of the nine profiles, filters domains, and invokes `REGISTRARS`. A domain exports its `register(MCPServer)` through `tools/<domain>/__init__.py`. Add a tool in its existing domain, with the same decorator order as its neighbours:

```python
@mcp.tool(annotations=READ_ONLY, structured_output=True)
@compact_errors("read_file")
def read_file(path: PathArg, ...) -> dict[str, Any]:
    ...
```

This excerpt shows the existing `tools/filesystem/registry.py` pattern, not a runnable replacement. `functools.wraps` in `compact_errors` preserves the typed signature. `create_server` sets the installed SDK's native `ArgModelBase` to `extra="forbid"`; unknown arguments must be rejected before the body runs. Preserve caller-visible names, defaults, schema, result shape, and concise descriptions. Reuse bounded aliases in `core/tooling.py` and Pydantic `Annotated[..., Field(...)]` for actual input limits. Avoid default coercion or broad dictionaries where a typed contract already exists.

The live registry owns availability. Add required deterministic metadata to `core/tool_catalog.py` for discovery and routing, using the existing native risk/recoverability taxonomy. Metadata does not register the tool. `PROFILE_DOMAINS` in `core/tool_profiles.py` filters native domains; it is not authorization. Platform-specific imports and registration follow `core/platform` capability gates.

## Classification, lifecycle and recovery

Reuse `READ_ONLY`, `MUTATING`, `DESTRUCTIVE`, or the existing open-world variants from `core/tooling.py`. For a new native mutation, also update the fixed `MUTATING_TOOL_OPERATIONS` allowlist. An annotation alone does not install mutation admission. `tests/test_lifecycle.py::test_mutation_guard_set_matches_mcp_annotations` checks the registration/allowlist contract.

The wrapper tracks tool activity and timing. For allowlisted native mutations it enters `RUNTIME_LIFECYCLE.mutation`, begins the existing operation journal, runs the body under `operation_recovery_context`, and records the returned result or raised outcome. Managed drain rejects new mutations while permitting reads; standalone native behavior remains supported. Preserve resource locks and postcondition checkpoints owned by the tool.

The native wrapper's raised/returned-error terminal semantics do not establish whether a remote effect happened. External tools require their separate durable dispatch/uncertainty boundary; do not insert dynamic external names into the native allowlist. A timeout or cancellation is not proof of rollback. Preserve unresolved evidence and never blindly replay it.

## Validation and safe persistence

Validate at the public boundary and again where state can race. Reuse `resolve_path`, target locks, expected hashes, backups, staged validation, and atomic publication rather than duplicating them. Existing `write_file` checks `expected_sha256` inside `RESOURCE_LOCKS.sync`, calls the shared atomic writer, and invalidates the Python metadata cache only after a change. Document edits additionally preserve archive validation, locks, macro bytes, and durable file-hash postconditions.

Keep network or process I/O outside database/file-lock transactions. Classify partial success and cancellation explicitly. Audit only allowlisted identity/outcome/size fields; `audit_action` does not automatically remove secrets from arbitrary details. Native `failure`/`ToolError` conversions are appropriate only for errors already safe to expose; do not feed untrusted provider exceptions into str-based native conversion.

## Results, output and cleanup

Preserve the domain's existing `ok`, `page`, `failure`, and artifact shapes. `read_file` defaults to complete inline text; its explicit `delivery="file"`/`"auto"` path reuses `deliver_text`. Native legacy-inline defaults are a compatibility contract, not a universal hard output cap. New state or outputs need documented count/byte/time ceilings and explicit truncation or pagination semantics.

Reuse `core/artifacts.py`, retention and active-use protection for durable overflow. Do not auto-fetch arbitrary result URIs or open external files. Use existing process ownership for spawned trees, finite deadlines, and measured cleanup. A response timeout with a live child/reader is not successful cleanup. Document timeout scope, retry rules, cancellation effects and platform requirements for each tool.

## Review and verification

Trace the affected callers, registration/export, metadata, permissions, storage and tests before changing them. Start with the closest existing fixtures and a meaningful behavior test. Exercise SDK input validation and structured results through `mcp.Client(create_server())`, not only the Python body. Denied mutations must leave the target unchanged. For concurrency, failure, restart or money-like effects, assert actual effect counts and durable evidence.

Use the [testing standards](TESTING-STANDARDS.md) for commands and isolation. Relevant existing suites include `test_mcp_integration.py`, `test_tool_profiles.py`, `test_lifecycle.py`, `test_recovery_journal.py`, and `test_output_policy.py`. Update only the affected user documentation, current changelog and discovery metadata; retain immutable historical release evidence. No tool authoring change authorizes publication.
