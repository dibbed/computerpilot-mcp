# Security Policy

## Reporting a Vulnerability

We take the security of this project seriously. If you discover a potential security vulnerability, please report it responsibly.

### How to Report

- **Do NOT disclose vulnerabilities publicly**: Please avoid opening public issues, discussions, or pull requests for security-sensitive bugs or suspected vulnerabilities.
- **GitHub Private Vulnerability Reporting**: If available on this repository, please submit your report via [GitHub Security Advisories](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-communicating-vulnerabilities/privately-reporting-a-security-vulnerability).
- **Direct Author Contact**: Alternatively, contact the maintainer through the GitHub profile: [https://github.com/dibbed/](https://github.com/dibbed/).

### What to Include in Your Report

To help us triage and resolve the issue quickly, please provide:
1. A clear description of the vulnerability and its potential impact.
2. Step-by-step reproduction instructions or a minimal proof of concept.
3. Relevant environment details (Windows version, Python version).

### Important: Never Include Secrets

**Never include real credentials, API keys, bearer tokens, or sensitive production data in vulnerability reports or logs.** Always sanitize reproduction steps using generic placeholders like `<REDACTED_API_KEY>`.

---

## Security Practices for Users and Deployers

- **Keep Secrets Out of Version Control**: Always supply credentials via `.secrets/control_plane_api_key.txt` or environment variables. This path is ignored by `.gitignore`.
- **Verify Bundled Binaries**: Verify that `tunnel-client.exe` and `cloudflared.exe` match the hashes documented in [BINARY_PROVENANCE.md](BINARY_PROVENANCE.md).
- **Loopback Protection**: The internal MCP HTTP endpoint and control panel bind strictly to loopback (`127.0.0.1`). Do not expose these ports to public or untrusted networks without adequate authorization proxies.
