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

## Development contracts

- [Agent and contributor contract](../AGENTS.md) — scope, authority, safety and execution evidence.
- [Native tool authoring](development/MCP-TOOL-AUTHORING.md) — current registration, input/result, lifecycle and recovery conventions.
- [Extension authoring](development/MCP-EXTENSION-AUTHORING.md) — proposed v0.8 operator/provider contract; runtime gates remain pending.
- [Testing standards](development/TESTING-STANDARDS.md) — isolation, behavior checks, platform and release evidence.

## Release evidence

Current release evidence:

- [v0.4.0 release notes](V0.4.0-RELEASE-NOTES.md)
- [v0.4.0 validation](V0.4.0-VALIDATION.md)

Versioned files such as `V0.4.0-RELEASE-NOTES.md` and `V0.4.0-VALIDATION.md` are immutable evidence for a specific release. They may contain version-specific counts or platform claims that should not be copied forward without re-validation.

## Internal development material

Local planning directories such as `docs/roadmap/` and `docs/superpowers/` remain ignored by default. The v0.8 master plan is kept only in the operator's Windows Downloads directory, outside all checkouts and release packages. The [agent contract](../AGENTS.md) contains a brief resumable handoff; do not recreate a repository copy of the master plan.
