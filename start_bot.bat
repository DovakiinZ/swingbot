@echo off
cd /d "%~dp0"
echo Starting Swingbot with Git-driven Auto-Reload...
echo Dashboard: http://localhost:8080
echo Press Ctrl+C to stop
python auto_run.py --lang en
