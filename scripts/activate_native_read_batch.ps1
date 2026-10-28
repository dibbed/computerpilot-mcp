<#
.SYNOPSIS
  Opt in to native_read_batch on an existing full-profile Windows Tunnel service.
.DESCRIPTION
  Preflights Doctor/tool registration and active work, drains the existing
  Supervisor through its authenticated loopback panel, restarts the *same*
  workspace, and restores the exact previous settings on failure.
  Never run the wrapper through a synchronous MCP-owned process: restart would
  terminate the transport that owns that process. Launch it detached, with
  COMPUTERPILOT_INTERNAL_RESOURCE_CAPABILITY absent.
#>
[CmdletBinding()]
param([switch]$CheckOnly)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Only a fresh native execution may acquire its own resource ownership.
# An MCP tool's ephemeral capability must never escape into a detached launcher.
Remove-Item Env:COMPUTERPILOT_INTERNAL_RESOURCE_CAPABILITY -ErrorAction SilentlyContinue

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$stateDir = Join-Path $repo '.agent_state\native-read-batch'
$settings = Join-Path $repo '.agent_state\runtime-settings.cmd'
$backup = Join-Path $stateDir 'runtime-settings.original.bin'
$log = Join-Path $stateDir 'events.jsonl'
$state = Join-Path $stateDir 'status.json'
$origin = 'http://127.0.0.1:8766'
$launcherPath = Join-Path $repo 'START_MCP.bat'
$py = Join-Path $repo '.venv\Scripts\python.exe'
$original = $null
$changed = $false
$stopped = $false
$launched = $null
$priorPanelPid = 0
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
Set-Location -LiteralPath $repo

function Record([string]$phase, [string]$detail) {
    $entry = [ordered]@{ phase=$phase; detail=$detail; at_utc=[DateTime]::UtcNow.ToString('o') }
    $json = ConvertTo-Json $entry -Compress
    [IO.File]::WriteAllText($state, $json, [Text.Encoding]::UTF8)
    [IO.File]::AppendAllText($log, $json + [Environment]::NewLine, [Text.Encoding]::UTF8)
}
function PanelPid {
    $tcp = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $tcp) { return 0 }
    return [int]$tcp.OwningProcess
}
function PanelStatus {
    Invoke-RestMethod -Uri "$origin/api/status" -TimeoutSec 6
}
function IsHealthy($s) {
    return $s.state -eq 'running' -and $s.mcp_healthy -eq $true -and
           $s.tunnel_healthy -eq $true -and $s.transport_diagnosis -eq 'HEALTHY'
}
function RequestStop {
    $html = (Invoke-WebRequest -Uri "$origin/" -UseBasicParsing -TimeoutSec 6).Content
    $match = [regex]::Match($html, "const token='([^']+)';")
    if (-not $match.Success) { throw 'Operator panel control token unavailable' }
    $headers = @{ Origin=$origin; 'X-Control-Token'=$match.Groups[1].Value }
    $result = Invoke-RestMethod -Uri "$origin/api/stop" -Method Post -Headers $headers -ContentType 'application/json' -Body '{}' -TimeoutSec 8
    if ($result.ok -ne $true) { throw 'Operator panel rejected a graceful stop' }
}
function WaitPortFree([int]$limitSec) {
    $until = [DateTime]::UtcNow.AddSeconds($limitSec)
    while ([DateTime]::UtcNow -lt $until) {
        if ((PanelPid) -eq 0) { return $true }
        Start-Sleep -Milliseconds 650
    }
    return $false
}
function Launch {
    # Keep the already verified Tunnel executable, do not auto-update during activation.
    $env:MCP_TUNNEL_AUTO_UPDATE = '0'
    $proc = Start-Process -FilePath $env:ComSpec -ArgumentList @('/d','/c',('"' + $launcherPath + '"')) -WorkingDirectory $repo -WindowStyle Hidden -PassThru
    if ($null -eq $proc -or $proc.Id -le 0) { throw 'Could not spawn the existing launcher' }
    return $proc
}
function WaitHealthy([int]$limitSec) {
    $until = [DateTime]::UtcNow.AddSeconds($limitSec)
    while ([DateTime]::UtcNow -lt $until) {
        Start-Sleep -Seconds 2
        if ((PanelPid) -eq 0) { continue }
        try {
            $s = PanelStatus
            if (IsHealthy $s) { return $true }
        } catch { }
    }
    return $false
}
function StopOwnedLauncher($proc) {
    if ($null -eq $proc -or $proc.HasExited) { return }
    $info = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.Id)" -ErrorAction SilentlyContinue
    if ($null -eq $info -or $info.Name -ne 'cmd.exe' -or
        -not $info.CommandLine.Contains($launcherPath)) {
        Record 'manual_attention' 'Launched process identity is ambiguous; not terminating an unknown process'
        return
    }
    & "$env:SystemRoot\System32\taskkill.exe" /PID $proc.Id /T /F 2>$null | Out-Null
}
try {
    Record 'preflight' 'Checking the current workspace and control plane'
    if (-not (Test-Path -LiteralPath $py) -or -not (Test-Path -LiteralPath $settings)) { throw 'Expected workspace files are missing' }
    $branch = (& git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -notin @('main','master')) { throw 'Activation must run from the reviewed main branch' }
    & git diff --quiet HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Tracked changes are present; activation cancelled' }
    $current = PanelStatus
    $priorPanelPid = PanelPid
    if ($priorPanelPid -eq 0 -or -not (IsHealthy $current)) { throw 'Current Supervisor is not healthy' }
    if ($current.active_mutations -ne 0) { throw 'Active tool mutations prevent a safe restart' }

    # Refuse to cut off active durable work, including queued/running jobs.
    $probe = @'
import sqlite3
from pathlib import Path
root=Path(".agent_state")
for name,table,column,values in (
    ("jobs.sqlite3","jobs","status",("queued","running","orphaned")),
    ("workflows.sqlite3","workflow_resource_requests","state",("pending","active","quarantined")),
    ("workflows.sqlite3","workflows","state",("queued","running","uncertain")),
):
    uri=(root/name).resolve().as_uri()+"?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        placeholders=",".join("?" for _ in values)
        count=connection.execute("SELECT COUNT(*) FROM "+table+" WHERE "+column+" IN ("+placeholders+")",values).fetchone()[0]
        if count: raise SystemExit("Active work prevents a restart: "+table)
print("No unresolved work")
'@
    $probe | & $py -
    if ($LASTEXITCODE -ne 0) { throw 'Durable job/workflow preflight failed' }
    $original = [IO.File]::ReadAllBytes($settings)
    $sourceText = [Text.Encoding]::Default.GetString($original)
    if ($sourceText -match '(?i)MCP_NATIVE_READ_BATCH_ENABLED') { throw 'Settings already contain a Batch override; inspect manually' }
    if (Test-Path -LiteralPath $backup) { throw 'Previous settings backup already exists; inspect before retrying' }

    # Prove the exact Batch tool count BEFORE disrupting the running Tunnel.
    $env:MCP_NATIVE_READ_BATCH_ENABLED = '1'
    $startup = & $py main.py --check
    if ($LASTEXITCODE -ne 0) { throw 'Batch startup tool registration failed' }
    $registration = ($startup | Out-String).Trim() | ConvertFrom-Json
    if ($registration.tool_count -ne 152 -or $registration.unique_tool_names -ne $true) {
        throw 'Unexpected Batch tool catalog; refusing activation'
    }
    # Doctor must run with a clean, standalone resource capability environment.
    $tunnelProfile = (& $py -c 'from scripts.tunnel_runtime import detect_profile; print(detect_profile())').Trim()
    if ($LASTEXITCODE -ne 0 -or -not $tunnelProfile) { throw 'Tunnel profile discovery failed' }
    & $py -m scripts.doctor --mode tunnel --profile $tunnelProfile
    if ($LASTEXITCODE -ne 0) { throw 'Full Doctor failed before activation' }
    if ($CheckOnly) {
        Record 'preflight_passed' 'Tool registration 152 and Full Doctor succeeded; live service untouched'
        return
    }

    [IO.File]::WriteAllBytes($backup, $original)
    $suffix = [Text.Encoding]::ASCII.GetBytes([Environment]::NewLine + 'set "MCP_NATIVE_READ_BATCH_ENABLED=1"' + [Environment]::NewLine)
    $next = New-Object byte[] ($original.Length + $suffix.Length)
    [Array]::Copy($original, 0, $next, 0, $original.Length)
    [Array]::Copy($suffix, 0, $next, $original.Length, $suffix.Length)
    [IO.File]::WriteAllBytes($settings, $next)
    $changed = $true
    Record 'configured' 'Opt-in setting appended and the exact previous bytes saved'

    RequestStop
    $stopped = $true
    Record 'draining' 'Graceful stop requested through the authenticated panel'
    if (-not (WaitPortFree 90)) { throw 'Old Supervisor did not release its panel port' }
    Start-Sleep -Seconds 1
    if ((PanelPid) -ne 0) { throw 'Another Supervisor occupied the port' }
    $launched = Launch
    Record 'restarting' "Existing launcher started, PID=$($launched.Id)"
    if (-not (WaitHealthy 160)) { throw 'New Supervisor did not report healthy MCP and Tunnel' }
    Record 'healthy' 'Batch opt-in enabled; final plugin catalog refresh and live tool call still required'
} catch {
    $reason = $_.Exception.Message
    Record 'failed' $reason
    if ($changed) {
        # Never leave the new setting in place if service readiness fails.
        try {
            if ((PanelPid) -ne 0) { RequestStop; [void](WaitPortFree 90) }
            StopOwnedLauncher $launched
            [IO.File]::WriteAllBytes($settings, $original)
            Remove-Item Env:MCP_NATIVE_READ_BATCH_ENABLED -ErrorAction SilentlyContinue
            Record 'rolled_back' 'Original runtime settings bytes restored'
        } catch {
            Record 'manual_attention' ('Rollback could not finish: ' + $_.Exception.Message)
        }
    }
    if ($stopped -and (PanelPid) -eq 0) {
        try {
            Remove-Item Env:MCP_NATIVE_READ_BATCH_ENABLED -ErrorAction SilentlyContinue
            $restore = Launch
            Record 'recovery_started' "Restored previous settings; launcher PID=$($restore.Id)"
            if (WaitHealthy 160) { Record 'recovered' 'Previous MCP and Tunnel connectivity healthy' }
            else { Record 'manual_attention' 'Recovery launcher started but health not confirmed' }
        } catch {
            Record 'manual_attention' ('Recovery launcher could not start: ' + $_.Exception.Message)
        }
    }
    exit 1
} finally {
    Remove-Item Env:MCP_NATIVE_READ_BATCH_ENABLED -ErrorAction SilentlyContinue
}
