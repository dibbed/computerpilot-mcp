param([int]$ExitCode, [int]$Attempt, [int]$Delay)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$StateDirectory = Join-Path $ProjectRoot '.agent_state'
New-Item -Path $StateDirectory -ItemType Directory -Force | Out-Null
$LogPath = Join-Path $StateDirectory 'launcher.log'
if ((Test-Path -LiteralPath $LogPath) -and (Get-Item -LiteralPath $LogPath).Length -ge 2097152) {
    for ($Index = 2; $Index -ge 1; $Index--) {
        $OldPath = "$LogPath.$Index"
        if (Test-Path -LiteralPath $OldPath) {
            Move-Item -LiteralPath $OldPath -Destination "$LogPath.$($Index + 1)" -Force
        }
    }
    Move-Item -LiteralPath $LogPath -Destination "$LogPath.1" -Force
}
Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value "$(Get-Date -Format o) exit_code=$ExitCode attempt=$Attempt retry_seconds=$Delay"
