@echo off
REM Opens an existing OFFLINE DEMO, or starts it on its own port.
cd /d "%~dp0"
set STREAMLIT_SERVER_PORT=8502
set STREAMLIT_SERVER_HEADLESS=false
.venv\Scripts\python.exe -c "import socket,webbrowser,subprocess,sys; s=socket.socket(); s.settimeout(2); running=s.connect_ex(('127.0.0.1',8502))==0; s.close(); webbrowser.open('http://localhost:8502') if running else sys.exit(subprocess.call([sys.executable,'-m','omkaka','demo']))"
if errorlevel 1 pause
