@echo off
REM Opens the LIVE dashboard in your browser. Close this window to stop.
cd /d "%~dp0"
.venv\Scripts\python.exe -m omkaka app
pause
