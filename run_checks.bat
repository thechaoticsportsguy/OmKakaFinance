@echo off
REM Runs the automated checks and the journal integrity check.
cd /d "%~dp0"
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m omkaka verify
pause
