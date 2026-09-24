# Troubleshooting

## Start with the built-in diagnostics

Server construction and tool registration:

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

Tunnel Doctor:

```sh
python -m scripts.doctor --mode tunnel --profile <profile>
```

## The launcher runs the full Doctor again

Startup validation uses a fingerprint of relevant runtime inputs. Source, dependency, environment, tunnel-runtime, or configuration changes can invalidate the cached successful check.

A full Doctor after a meaningful change is expected.

## Tunnel mode says the API key is unavailable

Provide:

```text
.secrets/control_plane_api_key.txt
```

or set `CONTROL_PLANE_API_KEY` in the environment.

Local HTTP mode does not require a tunnel key.

## Tunnel runtime cannot be installed

Check:

1. network access to the official `openai/tunnel-client` release;
2. the requested `MCP_TUNNEL_VERSION` if pinned;
3. whether `MCP_TUNNEL_UPDATE_REQUIRED=1` is intentionally enforcing freshness;
4. `.agent_state/tunnel-runtime/` metadata/log evidence;
5. `python -m scripts.doctor --mode tunnel --profile <profile>`.

A fresh install without a verified managed cache fails closed if the runtime cannot be downloaded and verified.

## Port 8765 or 8766 is already in use

These are the default loopback ports for local HTTP MCP and the control panel.

Check whether another ComputerPilot MCP supervisor/runtime is already active before terminating anything.

## Tunnel mode reports port 8080 in use

The managed tunnel runtime can use a local admin/readiness service on port 8080.

On Windows, inspect before terminating:

```powershell
Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
Get-Process -Id <PID>
```

## Browser tools are registered but Chromium does not launch

Install the optional dependency and browser runtime:

```sh
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
python -m scripts.doctor --mode local-http --browser
```

## LSP tools cannot start

Confirm `pyright-langserver` is already available on `PATH`.

The MCP intentionally does not install an LSP server or execute arbitrary caller-supplied language-server commands.

## A new tool does not appear in the client

Some MCP clients cache the tool catalog for a connection lifetime.

Restart/reconnect the MCP client after a server tool-schema change.

Also check `MCP_TOOL_PROFILE`: a smaller profile may intentionally omit the tool's domain.

Use `discover_tool_domains` to inspect the active profile and platform capability set.

## An operation is `uncertain`

Do not rerun it blindly.

Use:

1. `list_uncertain_operations`;
2. `inspect_uncertain_operation`;
3. `reconcile_operation` with an applicable postcondition.

If the persisted evidence still cannot determine the result, use explicit acknowledgement and document the reason.

## A durable job is silent

Check:

- `job_status`;
- `job_wait` after the latest known version;
- `job_output` using the returned byte cursors.

Long-running jobs are designed to be observed incrementally rather than requiring one giant output response.

## Windows reports `WinError 1455`

Windows can return this while process-table APIs are under commit/pagefile pressure.

The supervisor treats process inspection as best-effort and keeps managed root-process cleanup separate from recursive process enumeration. If the error also occurs outside ComputerPilot MCP, inspect system memory/commit pressure and the Windows paging-file configuration.

## Local state looks large

Use `server_health` and the control panel to inspect resource/storage budgets.

Most generated stores have age/count/byte/TTL limits. Do not delete `.agent_state/` blindly when durable jobs, workflows, recovery evidence, or managed tunnel state matter.

## Before opening an issue

Include:

- project version/commit;
- OS and architecture;
- Python version;
- start mode;
- exact command or MCP tool involved;
- minimal reproduction;
- sanitized logs.

Never attach `.secrets/` or raw local state containing sensitive data.
