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

$Arguments = @('-m', 'scripts.bootstrap')
if ($Start) { $Arguments += '--start' }
& $VenvPython @Arguments
exit $LASTEXITCODE
