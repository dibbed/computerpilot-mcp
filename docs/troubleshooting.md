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

## A long ChatGPT turn looks stuck but the local MCP is still running

Open the loopback control panel at `http://127.0.0.1:8766/` and inspect **Transport Health** before restarting anything.

The Supervisor reads the managed tunnel runtime's detailed health endpoint and keeps these failure classes separate:

- `HEALTHY`: control-plane polling and local transport components are healthy;
- `UPSTREAM_IDLE`: polling is healthy, but no new upstream command has reached the local transport recently;
- `POLL_STALLED`: the current control-plane poll exceeded the runtime-reported deadline plus the configured grace period;
- `CONTROL_PLANE_BACKOFF`: polling reports failures/backoff but has not crossed the stall threshold;
- `QUEUE_BACKPRESSURE`: local queue pressure can intentionally pause polling and must not be mistaken for a dead poll loop;
- `DISPATCH_STALLED`: the dispatcher is not accepting work normally;
- `RESPONSE_DELIVERY_STALLED`: response delivery is not in a healthy accepted state;
- `MCP_STALLED`: a local MCP tool call has remained active beyond the diagnostic threshold.

`UPSTREAM_IDLE` is evidence that the local transport is still healthy; it does **not** trigger automatic recovery. Only a repeated, confirmed `POLL_STALLED` diagnosis is eligible for the conservative poll watchdog.

For timeline evidence, inspect the bounded rotated metadata log:

```text
.agent_state/transport-health.jsonl
```

The activity snapshot and transport history contain only timestamps, component state, counters, process/generation identifiers, and tool names. Tool arguments, results, file contents, typed text, and credentials are not written there.

If needed, tune or disable the watchdog with:

```text
MCP_TRANSPORT_UPSTREAM_IDLE_SEC
MCP_TUNNEL_POLL_STALL_GRACE_SEC
MCP_TUNNEL_POLL_STALL_CONFIRMATIONS
MCP_TUNNEL_POLL_WATCHDOG
MCP_TOOL_STALL_SEC
MCP_TRANSPORT_HISTORY_INTERVAL_SEC
```

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

## The panel previously printed `WinError 10053`

On Windows, a browser/tab refresh or another client can close the loopback HTTP connection while the Supervisor is writing a response. That can surface as `ConnectionAbortedError: [WinError 10053]`.

v0.4.0 treats disconnects during response/header/download writes as a normal client lifecycle event, closes that connection, and avoids printing a server traceback. The catch is scoped to socket response delivery so unrelated Supervisor/backend exceptions are still visible.

If `WinError 10053` appears from a different subsystem or repeatedly without any client disconnect/refresh, capture the surrounding component/log context instead of assuming it is the handled panel case.

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
