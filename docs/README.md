# ComputerPilot MCP Documentation

This directory contains the public technical documentation and release evidence for ComputerPilot MCP.

## Start here

- [Installation](installation.md) — source/release setup, local HTTP, Secure Tunnel, browser prerequisites.
- [Architecture](architecture.md) — runtime components, state ownership, durability, and recovery boundaries.
- [Platform support](platform-support.md) — operating-system and architecture capabilities.
- [Tooling and workflows](tooling.md) — tool domains, profiles, and practical agent workflows.
- [Configuration](configuration.md) — common environment variables and resource budgets.
- [Troubleshooting](troubleshooting.md) — startup, tunnel, browser, catalog, recovery, and local-state diagnostics.
- [Release process](release-process.md) — CI/release gates, deterministic packaging, and publication rules.
- [Security policy](../SECURITY.md) — vulnerability reporting and operational security.
- [Contributing](../CONTRIBUTING.md) — development and pull request workflow.
- [Changelog](../CHANGELOG.md) — published release history.
- [Binary provenance](../BINARY_PROVENANCE.md) — managed tunnel-runtime verification model.

## Release evidence

Versioned files such as `V0.3.0-RELEASE-NOTES.md` and `V0.3.0-VALIDATION.md` are immutable evidence for a specific release. They may contain version-specific counts or platform claims that should not be copied forward without re-validation.

## Internal development material

Local planning directories such as `docs/roadmap/` and `docs/superpowers/` are ignored by Git. They are not part of the public documentation contract.
