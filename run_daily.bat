@echo off
REM Called by Windows Task Scheduler every 30 minutes. The job itself decides
REM whether it is time to work (New York time), so repeated calls are harmless.
cd /d "%~dp0"
if not exist data\logs mkdir data\logs
.venv\Scripts\python.exe -m omkaka daily --scheduled >> data\logs\daily.log 2>&1
