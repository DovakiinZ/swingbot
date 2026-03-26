#!/bin/bash
if [ -f .bot.pid ]; then
    kill $(cat .bot.pid) && rm .bot.pid
    echo "Swingbot stopped."
else
    echo "No PID file found. Try: pkill -f run.py"
fi
