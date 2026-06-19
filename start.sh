#!/bin/bash
set -e
cd "$( dirname "$0" )"

echo "Installing dependencies..."
pip install -r requirements.txt --quiet

echo "Creating directories..."
mkdir -p logs data

echo "Starting Swingbot with Git-driven Auto-Reload..."
# Note: Using python auto_run.py which manages the run.py process
nohup python auto_run.py --lang en > logs/output.log 2>&1 &
BOT_PID=$!

echo " "
echo "Swingbot Wrapper started (PID: $BOT_PID)"
echo "Dashboard: http://$(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_IP'):8080"
echo "Logs: tail -f logs/output.log"
echo "Stop: kill $BOT_PID"
echo " "

echo $BOT_PID > .bot.pid
