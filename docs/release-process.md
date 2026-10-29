# Release Process

ComputerPilot MCP uses guarded, same-SHA release publication.

Contributors follow the [agent contract](../AGENTS.md) and [testing standards](development/TESTING-STANDARDS.md). Master plans remain local to the operator and outside release checkouts; extension acceptance and independent review requirements do not waive the existing release gates below.

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

Use a short-lived branch for each milestone, push tested checkpoints, and create a PR against `main`; branch pushes alone do not trigger the current workflows. Merge after the current head's six CI jobs, three Platform Release Validation jobs and Release Packaging Smoke all succeed. Verify those workflows again on the resulting `main` SHA before starting the next milestone from current `origin/main`. Local, FreeBuff and Cloud work resume from that baseline and fetch the small active branch when needed. The full master plan must never be copied into a checkout; use the agent contract's brief handoff, source and current CI.

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

## Version preparation and evidence

Update both `pyproject.toml` and `core/config.py`, the active guides/changelog, versioned release notes and validation record. Preserve historical reports and state the tested SHA/configuration for reused measurements. Packaging validates both versions against the explicit Git ref, never dirty working files. Confirm migration/rollback notes before upgrading a live installation.

Use a PR titled exactly `release: vX.Y.Z` and squash-merge with that exact subject only after all ten head checks pass; a conventional merge subject does not trigger publication. Record release-main SHA and all ten same-SHA checks, then verify the publication workflow, immutable tag target, six assets, independently recomputed archive checksums and manifest source/version/targets. Post-publication evidence may land in a separate docs PR without moving the tag.

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
