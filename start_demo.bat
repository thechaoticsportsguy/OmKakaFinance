@echo off
REM Opens the OFFLINE DEMO (fictional data) in your browser. Close this window to stop.
cd /d "%~dp0"
.venv\Scripts\python.exe -m omkaka demo
pause
