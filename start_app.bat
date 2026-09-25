@echo off
REM Opens an existing LIVE dashboard, or starts it if needed.
cd /d "%~dp0"
set STREAMLIT_SERVER_PORT=8501
set STREAMLIT_SERVER_HEADLESS=false
.venv\Scripts\python.exe -c "import socket,webbrowser,subprocess,sys; s=socket.socket(); s.settimeout(2); running=s.connect_ex(('127.0.0.1',8501))==0; s.close(); webbrowser.open('http://localhost:8501') if running else sys.exit(subprocess.call([sys.executable,'-m','omkaka','app']))"
if errorlevel 1 pause
