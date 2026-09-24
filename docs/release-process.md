# Release Process

ComputerPilot MCP uses guarded, same-SHA release publication.

## Normal change flow

```text
focused branch
  ↓
tests / lint / typing / health
  ↓
pull request
  ↓
CI + applicable release gates
  ↓
merge to main
```

Do not rewrite published release history, force-push release branches, or modify existing release tags.

## Required GitHub gates

### CI

Runs on:

- Windows, Python 3.10 and 3.12
- Linux, Python 3.10 and 3.12
- macOS, Python 3.10 and 3.12

Each job runs dependency install, compileall, Ruff, mypy, startup check, health check, and pytest.

### Platform Release Validation

Runs on Windows, Linux, and macOS with Python 3.12 and executes `scripts.platform_validation`.

The validation runner covers the real host launcher path plus deeper release checks such as Doctor, benchmark, durable jobs, and workflow restart/reconciliation behavior.

### Release Packaging Smoke

Runs deterministic packaging for all supported targets and validates `SHA256SUMS.txt`.

Targets:

- `windows-amd64`
- `linux-amd64`
- `linux-arm64`
- `macos-amd64`
- `macos-arm64`

## Packaging contract

`scripts/package_release.py` reads tracked Git blobs from an explicit ref.

It rejects forbidden tracked runtime/build-state paths, including local state, secrets, virtual environments, test caches, and generated output.

It also rejects upstream tunnel-runtime executable/archive names at repository root.

Each archive includes `RELEASE-MANIFEST.json` with the project, version, target, source commit, file count, and managed-runtime policy.

## Release trigger

The publication workflow recognizes a main-branch commit whose subject is exactly:

```text
release: vX.Y.Z
```

Before publication it waits for the same SHA to have successful CI and Release Packaging Smoke in addition to the Platform Release Validation event that triggered the workflow.

The release is built again from that exact SHA, checksums are re-verified, and a versioned release-notes file must exist.

## Publication

A successful publication creates:

- tag `vX.Y.Z`;
- release title `ComputerPilot MCP vX.Y.Z - Cross-Platform Preview`;
- five platform archives;
- `SHA256SUMS.txt`.

If the tag/release already exists, the workflow leaves it unchanged instead of silently overwriting it.

## Release documentation

For a release `X.Y.Z`, keep:

- `docs/VX.Y.Z-RELEASE-NOTES.md`
- `docs/VX.Y.Z-VALIDATION.md`

Release evidence should state what was actually validated and avoid carrying architecture/platform claims forward without a new gate.

## Current naming compatibility

The public project/package/release identity is ComputerPilot MCP / `computerpilot-mcp`.

The protocol-level server identifier remains `ali_windows_agent_mcp` for backward compatibility unless a future migration explicitly changes that contract.
