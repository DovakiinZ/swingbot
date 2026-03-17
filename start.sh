#!/bin/bash
set -e
cd "$(dirname "$0")"
echo "Installing dependencies..."
pip install -r requirements.txt --quiet
echo "Creating directories..."
mkdir -p logs data
echo "Starting Swingbot..."
nohup python run.py --lang en > logs/output.log 2>&1 &
BOT_PID=$!
echo ""
echo "Swingbot started (PID: $BOT_PID)"
echo "Dashboard: http://$(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_IP'):8080"
echo "Logs: tail -f logs/output.log"
echo "Stop: kill $BOT_PID"
echo ""
echo $BOT_PID > .bot.pid
