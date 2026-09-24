# Installation

ComputerPilot MCP can be run from a Git checkout or a published release archive.

## Requirements

- Python 3.10 or newer.
- Windows, Linux, or macOS.
- Git when using a source checkout.
- `sh` on Linux/macOS.
- Internet access on the first Secure Tunnel startup unless a verified managed runtime cache already exists.
- A control-plane API key for Secure Tunnel mode.

Optional:

- Playwright + browser runtime for browser automation.
- `pyright-langserver` on `PATH` for LSP tools.
- PowerShell/pwsh for PowerShell-specific execution on hosts where it is available.

## Source checkout

```sh
git clone https://github.com/dibbed/computerpilot-mcp.git
cd computerpilot-mcp
```

The recommended launchers create/reuse `.venv` and install/validate project requirements.

## Try local HTTP first

Local HTTP mode does not require tunnel credentials and binds to loopback.

### Windows

```powershell
$env:MCP_START_MODE = "local-http"
.\START_MCP.bat
```

### Linux/macOS

```sh
MCP_START_MODE=local-http ./start_mcp.sh
```

If executable mode was lost while copying files on POSIX:

```sh
chmod +x start_mcp.sh
```

Endpoints:

- MCP: `http://127.0.0.1:8765/mcp`
- control panel: `http://127.0.0.1:8766/`

## Secure Tunnel mode

Tunnel mode is the launcher default.

Supply the control-plane API key through the environment:

```powershell
$env:CONTROL_PLANE_API_KEY = "<your-key>"
.\START_MCP.bat
```

or:

```sh
export CONTROL_PLANE_API_KEY="<your-key>"
./start_mcp.sh
```

A local secret file is also supported:

```text
.secrets/control_plane_api_key.txt
```

An explicit tunnel profile can be supplied with:

```text
MCP_TUNNEL_PROFILE=<profile>
```

The launcher resolves a verified upstream runtime into `.agent_state/tunnel-runtime/`. The project does not ship the upstream tunnel executable inside its release archives.

## Release archives

Published releases provide:

- Windows amd64 ZIP
- Linux amd64 tar.gz
- Linux arm64 tar.gz
- macOS amd64 tar.gz
- macOS arm64 tar.gz
- `SHA256SUMS.txt`

Download them from the [GitHub Releases](https://github.com/dibbed/computerpilot-mcp/releases) page and verify the checksum manifest before use.

Each archive includes `RELEASE-MANIFEST.json` describing the project version, source commit, target, and managed-runtime policy.

## Browser automation

Install optional browser dependencies:

```sh
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
```

Then validate a real disposable browser launch:

```sh
python -m scripts.doctor --mode local-http --browser
```

Firefox and WebKit can also be used when their Playwright runtimes are installed.

## LSP/code intelligence

LSP-backed tools expect a trusted discovered:

```text
pyright-langserver --stdio
```

on `PATH`.

ComputerPilot MCP does not install a language server implicitly and does not accept an arbitrary language-server executable from an MCP caller.

## Validate the installation

Startup/tool construction:

```sh
python main.py --check
```

Health smoke:

```sh
python -m scripts.health_check --json
```

Full local Doctor:

```sh
python -m scripts.doctor --mode local-http
```

Cross-platform release validation:

```sh
python -m scripts.platform_validation --json
```

## Next steps

- [Configuration](configuration.md)
- [Platform support](platform-support.md)
- [Tooling and workflows](tooling.md)
- [Troubleshooting](troubleshooting.md)
