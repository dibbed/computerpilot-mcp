param(
    [switch]$Start
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $ProjectRoot

function Stop-WithError([string]$Message, [int]$Code = 1) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit $Code
}

$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    Write-Host '[1/5] Creating Python virtual environment...'
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        & py.exe -3 -m venv (Join-Path $ProjectRoot '.venv')
    }
    elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
        & python.exe -m venv (Join-Path $ProjectRoot '.venv')
    }
    else {
        Stop-WithError 'Python 3.10 or newer is required but was not found.' 10
    }
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError 'Virtual environment creation failed.' 11
    }
}
else {
    Write-Host '[1/5] Virtual environment found.'
}

& $VenvPython -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) {
    Stop-WithError 'The virtual environment must use Python 3.10 or newer.' 12
}
$PythonVersion = (& $VenvPython --version 2>&1).Trim()
Write-Host "[2/5] $PythonVersion"

$Requirements = Join-Path $ProjectRoot 'requirements.txt'
$Marker = Join-Path $ProjectRoot '.venv\.requirements.sha256'
$RequirementsHash = (& $VenvPython -c "import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest().upper())" $Requirements).Trim()
if ($LASTEXITCODE -ne 0 -or -not $RequirementsHash) {
    Stop-WithError 'Could not fingerprint requirements.txt.' 13
}
$InstalledHash = if (Test-Path -LiteralPath $Marker) { (Get-Content -LiteralPath $Marker -Raw).Trim() } else { '' }
$PreviousErrorPreference = $ErrorActionPreference
$ErrorActionPreference = 'SilentlyContinue'
& $VenvPython -c "import mcp, pydantic, psutil, charset_normalizer, PIL, pytest, ruff, mypy" *> $null
$ImportsExitCode = $LASTEXITCODE
$ErrorActionPreference = $PreviousErrorPreference
$ImportsHealthy = $ImportsExitCode -eq 0
if ($InstalledHash -ne $RequirementsHash -or -not $ImportsHealthy) {
    Write-Host '[3/5] Installing or refreshing dependencies...'
    $ErrorActionPreference = 'Continue'
    & $VenvPython -m pip install --quiet --disable-pip-version-check --requirement $Requirements
    $PipExitCode = $LASTEXITCODE
    $ErrorActionPreference = $PreviousErrorPreference
    if ($PipExitCode -ne 0) {
        Stop-WithError 'Dependency installation failed.' 13
    }
    Set-Content -LiteralPath $Marker -Value $RequirementsHash -Encoding ascii -NoNewline
}
else {
    Write-Host '[3/5] Dependencies are current.'
}

$ErrorActionPreference = 'Continue'
$DependencyCheck = & $VenvPython -m pip check 2>&1
$DependencyCheckExitCode = $LASTEXITCODE
$ErrorActionPreference = $PreviousErrorPreference
if ($DependencyCheckExitCode -ne 0) {
    Write-Host (($DependencyCheck | Select-Object -First 10) -join [Environment]::NewLine)
    Stop-WithError 'Installed dependencies are inconsistent.' 14
}

Write-Host '[4/5] Running MCP startup and tool smoke checks...'
& $VenvPython -m scripts.health_check --json
if ($LASTEXITCODE -ne 0) {
    Stop-WithError 'MCP health checks failed.' 15
}

if (-not $Start) {
    Write-Host '[5/5] Validation complete.'
    exit 0
}

$StartMode = if ($env:MCP_START_MODE) { $env:MCP_START_MODE } else { 'tunnel' }
if ($StartMode -eq 'local-http') {
    Write-Host '[5/5] Starting local Streamable HTTP server at http://127.0.0.1:8765/mcp'
    & $VenvPython -m scripts.supervisor --mode local-http
    exit $LASTEXITCODE
}

$TunnelClient = Join-Path $ProjectRoot 'tunnel-client.exe'
if (-not (Test-Path -LiteralPath $TunnelClient -PathType Leaf)) {
    Stop-WithError 'tunnel-client.exe is missing.' 16
}
$Profile = if ($env:MCP_TUNNEL_PROFILE) {
    $env:MCP_TUNNEL_PROFILE
} else {
    $DetectedProfiles = & $TunnelClient profiles list 2>$null
    $Candidate = $null
    if ($DetectedProfiles) {
        foreach ($Line in ($DetectedProfiles -split "`r?`n")) {
            $Parts = $Line -split "`t"
            if ($Parts.Count -ge 2 -and $Parts[0].Trim()) {
                $Candidate = $Parts[0].Trim()
                break
            }
        }
    }
    if ($Candidate) { $Candidate } else { 'default' }
}
if (-not $env:CONTROL_PLANE_API_KEY) {
    $SecretFile = Join-Path $ProjectRoot '.secrets\control_plane_api_key.txt'
    if (Test-Path -LiteralPath $SecretFile -PathType Leaf) {
        $env:CONTROL_PLANE_API_KEY = (Get-Content -LiteralPath $SecretFile -Raw).Trim()
    }
}
if (-not $env:CONTROL_PLANE_API_KEY) {
    Stop-WithError 'CONTROL_PLANE_API_KEY is unavailable and .secrets\control_plane_api_key.txt was not found.' 17
}

Write-Host "[5/5] Validating and starting Secure MCP Tunnel profile '$Profile'..."
$ErrorActionPreference = 'Continue'
$DoctorOutput = & $TunnelClient doctor --profile $Profile --json 2>&1
$DoctorExitCode = $LASTEXITCODE
$ErrorActionPreference = $PreviousErrorPreference
if ($DoctorExitCode -ne 0) {
    try {
        $Doctor = $DoctorOutput | Out-String | ConvertFrom-Json
        foreach ($Check in $Doctor.checks | Where-Object { $_.status -eq 'FAIL' }) {
            Write-Host ("FAIL {0}: {1}" -f $Check.id, $Check.summary) -ForegroundColor Red
        }
    }
    catch {
        Write-Host (($DoctorOutput | Out-String).Trim())
    }
    Stop-WithError 'Secure MCP Tunnel doctor checks failed.' 18
}

Write-Host 'Secure MCP Tunnel is starting. Local operator UI: http://127.0.0.1:8080/ui'
& $VenvPython -m scripts.supervisor --mode tunnel --profile $Profile
exit $LASTEXITCODE
