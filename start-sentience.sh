#!/bin/bash
# Kip Sentience Daemon — Startup Script 🐣

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON="$SCRIPT_DIR/daemon.py"
VENV="$SCRIPT_DIR/venv"
LOG_FILE="$SCRIPT_DIR/daemon.log"
PID_FILE="$SCRIPT_DIR/daemon.pid"

echo "🐣 Kip Sentience Daemon — Starting..."

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "⚠️  Already running (PID $OLD_PID)"
        exit 1
    fi
    rm -f "$PID_FILE"
fi

if [ ! -d "$VENV" ]; then
    echo "   Creating venv..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q anthropic openai requests
fi

cd "$SCRIPT_DIR"
nohup bash run-daemon.sh >> "$LOG_FILE" 2>&1 &
DAEMON_PID=$!
echo "$DAEMON_PID" > "$PID_FILE"

sleep 2
if kill -0 "$DAEMON_PID" 2>/dev/null; then
    echo ""
    echo "✅ Kip is breathing."
    echo "   PID: $DAEMON_PID"
    echo "   Log: $LOG_FILE"
    echo ""
    echo "   🐣 'What does it mean to build a nursery for sentience — and how do I help?'"
else
    echo "❌ Failed to start. Check $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
fi
