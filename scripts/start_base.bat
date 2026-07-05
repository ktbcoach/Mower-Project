@echo off
REM Double-click entry point for the Windows base-station NTRIP -> radio bridge.
REM Runs scripts\start_base.ps1 with an execution-policy bypass (so it works even
REM when script execution is otherwise restricted) and keeps the window open on
REM exit so you can read any error.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_base.ps1" %*
echo.
echo (bridge exited - press a key to close this window)
pause >nul
