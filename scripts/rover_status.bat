@echo off
REM Double-click entry point for the rover-status dashboard (Tkinter window).
REM Reads rover-status.txt written by the base bridge; coexists with it.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rover_status.ps1" %*
