@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "MCP_POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%MCP_POWERSHELL%" set "MCP_POWERSHELL=powershell.exe"
set "MCP_FAILURES=0"

if /I "%MCP_START_VALIDATE_ONLY%"=="1" (
    "%MCP_POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1"
    exit /b !ERRORLEVEL!
)

rem PowerShell is only needed to create the venv. Keep it out of the live
rem process chain so one console interrupt is handled by ComputerPilot itself.
if not exist "%~dp0.venv\Scripts\python.exe" (
    "%MCP_POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1"
    set "MCP_BOOTSTRAP_CODE=!ERRORLEVEL!"
    if not "!MCP_BOOTSTRAP_CODE!"=="0" exit /b !MCP_BOOTSTRAP_CODE!
)

:start_mcp
rem The trailing CALL clears cmd.exe's pending Ctrl+C batch-confirmation state.
rem Save ERRORLEVEL first because CALL intentionally resets it.
"%~dp0.venv\Scripts\python.exe" -m scripts.bootstrap --start & set "MCP_EXIT_CODE=!ERRORLEVEL!" & call;

rem Intentional console shutdown must not enter the crash-restart loop.
if "!MCP_EXIT_CODE!"=="-1073741510" exit /b !MCP_EXIT_CODE!
if "!MCP_EXIT_CODE!"=="130" exit /b !MCP_EXIT_CODE!
set /a MCP_FAILURES+=1
set "MCP_RETRY_SECONDS=5"
if !MCP_FAILURES! GEQ 2 set "MCP_RETRY_SECONDS=10"
if !MCP_FAILURES! GEQ 3 set "MCP_RETRY_SECONDS=30"
if !MCP_FAILURES! GEQ 4 set "MCP_RETRY_SECONDS=60"

echo.
echo [%DATE% %TIME%] MCP exited with code !MCP_EXIT_CODE!. Restarting in !MCP_RETRY_SECONDS! seconds...
echo Close this window or press Ctrl+C to stop.
if exist "%~dp0scripts\retry_notice.ps1" "%MCP_POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\retry_notice.ps1" -ExitCode !MCP_EXIT_CODE! -Attempt !MCP_FAILURES! -Delay !MCP_RETRY_SECONDS!
"%MCP_POWERSHELL%" -NoLogo -NoProfile -Command "Start-Sleep -Seconds !MCP_RETRY_SECONDS!"
if errorlevel 1 exit /b !ERRORLEVEL!
goto start_mcp
