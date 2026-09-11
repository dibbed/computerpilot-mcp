@echo off
setlocal
cd /d "%~dp0"

set "MCP_POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%MCP_POWERSHELL%" set "MCP_POWERSHELL=powershell.exe"
set "MCP_FAILURES=0"

:start_mcp
"%MCP_POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" -Start
set "MCP_EXIT_CODE=%ERRORLEVEL%"

rem Preserve intentional console interruption instead of restarting after Ctrl+C.
if "%MCP_EXIT_CODE%"=="-1073741510" exit /b %MCP_EXIT_CODE%
if "%MCP_EXIT_CODE%"=="130" exit /b %MCP_EXIT_CODE%
set /a MCP_FAILURES+=1
set "MCP_RETRY_SECONDS=5"
if %MCP_FAILURES% GEQ 2 set "MCP_RETRY_SECONDS=10"
if %MCP_FAILURES% GEQ 3 set "MCP_RETRY_SECONDS=30"
if %MCP_FAILURES% GEQ 4 set "MCP_RETRY_SECONDS=60"

echo.
echo [%DATE% %TIME%] MCP exited with code %MCP_EXIT_CODE%. Restarting in %MCP_RETRY_SECONDS% seconds...
echo Close this window or press Ctrl+C to stop.
if exist "%~dp0scripts\retry_notice.ps1" "%MCP_POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\retry_notice.ps1" -ExitCode %MCP_EXIT_CODE% -Attempt %MCP_FAILURES% -Delay %MCP_RETRY_SECONDS%
"%MCP_POWERSHELL%" -NoLogo -NoProfile -Command "Start-Sleep -Seconds %MCP_RETRY_SECONDS%"
if errorlevel 1 exit /b %ERRORLEVEL%
goto start_mcp
