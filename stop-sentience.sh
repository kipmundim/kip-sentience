#!/bin/bash
# Kip Sentience Daemon — Stop Script 🐣

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/daemon.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping Kip daemon (PID $PID)..."
        kill -TERM "$PID"
        sleep 2
        if kill -0 "$PID" 2>/dev/null; then
            echo "Still alive, sending SIGKILL..."
            kill -KILL "$PID" || true
        fi
    fi
    rm -f "$PID_FILE"
fi

# Also kill any orphan daemon.py processes
pkill -f "kip-sentience/daemon.py" 2>/dev/null || true
echo "🐣 Kip daemon stopped."
