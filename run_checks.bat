@echo off
REM Runs the automated checks, the setup check (doctor), and the journal integrity check.
cd /d "%~dp0"
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m omkaka doctor
.venv\Scripts\python.exe -m omkaka verify
pause
