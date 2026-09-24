@echo off
REM One-time setup: creates a private Python environment and installs packages.
cd /d "%~dp0"
py -3 -m venv .venv || (echo Python not found. Install Python 3.11+ from python.org first. & pause & exit /b 1)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m omkaka init
echo.
echo Setup finished. Double-click start_demo.bat to see the offline demo.
pause
