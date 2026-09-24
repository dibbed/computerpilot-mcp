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

$StartMode = if ($env:MCP_START_MODE) { $env:MCP_START_MODE } else { 'tunnel' }
if ($StartMode -notin @('tunnel', 'local-http')) { Stop-WithError 'Unsupported MCP_START_MODE.' 16 }
$Profile = 'default'
if ($StartMode -eq 'tunnel') {
    Write-Host 'INFO Resolving Secure MCP Tunnel runtime...'
    $PreviousErrorPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $TunnelClientOutput = & $VenvPython -m scripts.tunnel_runtime ensure --print-path
    $TunnelClientExit = $LASTEXITCODE
    $ErrorActionPreference = $PreviousErrorPreference
    if ($TunnelClientExit -ne 0 -or -not $TunnelClientOutput) {
        Stop-WithError 'No verified tunnel-client runtime is available.' 16
    }
    $TunnelClient = ($TunnelClientOutput | Select-Object -Last 1).Trim()
    if (-not (Test-Path -LiteralPath $TunnelClient -PathType Leaf)) {
        Stop-WithError "Resolved tunnel-client runtime is missing: $TunnelClient" 16
    }
    $env:MCP_TUNNEL_CLIENT_BIN = $TunnelClient

    $DetectedProfile = (& $VenvPython -m scripts.tunnel_runtime profile 2>$null | Select-Object -Last 1)
    if ($LASTEXITCODE -ne 0 -or -not $DetectedProfile) {
        $Profile = 'default'
    }
    else {
        $Profile = $DetectedProfile.Trim()
    }
    if (-not $Profile) { $Profile = 'default' }

    if (-not $env:CONTROL_PLANE_API_KEY) {
        $SecretFile = Join-Path $ProjectRoot '.secrets\control_plane_api_key.txt'
        if (Test-Path -LiteralPath $SecretFile -PathType Leaf) {
            $env:CONTROL_PLANE_API_KEY = (Get-Content -LiteralPath $SecretFile -Raw).Trim()
        }
    }
    if (-not $env:CONTROL_PLANE_API_KEY) {
        Stop-WithError 'CONTROL_PLANE_API_KEY is unavailable and .secrets\control_plane_api_key.txt was not found.' 17
    }
}
$PreviousErrorPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $VenvPython -m scripts.doctor --check --mode $StartMode --profile $Profile
$FastExit = $LASTEXITCODE
$ErrorActionPreference = $PreviousErrorPreference
if ($FastExit -ne 0) {
    $BrowserInstalled = & $VenvPython -c "import importlib.util; print('yes' if importlib.util.find_spec('playwright') else 'no')"
    $Requirements = if ($BrowserInstalled -eq 'yes') { Join-Path $ProjectRoot 'requirements-browser.txt' } else { Join-Path $ProjectRoot 'requirements.txt' }
    $Marker = Join-Path $ProjectRoot '.venv\.requirements.sha256'
    $RequirementsHash = (& $VenvPython -c "import hashlib, pathlib, sys; p=pathlib.Path(sys.argv[1]); print(hashlib.sha256(p.read_bytes() + (p.parent / 'requirements.txt').read_bytes()).hexdigest().upper())" $Requirements).Trim()
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
    Write-Host '[4/5] Running full startup doctor...'
    & $VenvPython -m scripts.doctor --mode $StartMode --profile $Profile
    if ($LASTEXITCODE -ne 0) { Stop-WithError 'Full doctor failed; run python -m scripts.doctor for diagnosis.' 15 }
}
else {
    Write-Host '[3/5] Successful startup fingerprint unchanged.'
    Write-Host '[4/5] Fast checks complete.'
}
if (-not $Start) { Write-Host '[5/5] Validation complete.'; exit 0 }
Write-Host '[5/5] Starting MCP supervisor...'
if ($StartMode -eq 'local-http') {
    & $VenvPython -m scripts.supervisor --mode local-http
}
else {
    & $VenvPython -m scripts.supervisor --mode tunnel --profile $Profile
}
exit $LASTEXITCODE
