@echo off
REM Installs the "OmKakaFinance Daily" task in Windows Task Scheduler.
cd /d "%~dp0"
.venv\Scripts\python.exe -m omkaka schedule install
pause
