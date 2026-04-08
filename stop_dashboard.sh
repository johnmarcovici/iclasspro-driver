#!/bin/bash
# Stop the iClassPro Enrollment Web Dashboard if it is running.

cd "$(dirname "$0")"

PID_FILE="/tmp/iclasspro-dashboard.pid"
PORT="8000"
STOPPED=0

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if [[ "$PID" =~ ^[0-9]+$ ]] && kill -0 "$PID" 2>/dev/null; then
        echo "Stopping dashboard process $PID..."
        kill "$PID" 2>/dev/null || true
        sleep 1
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID" 2>/dev/null || true
        fi
        STOPPED=1
    fi
    rm -f "$PID_FILE"
fi

if fuser "$PORT"/tcp >/dev/null 2>&1; then
    echo "Stopping process on port $PORT..."
    fuser -k "$PORT"/tcp 2>/dev/null || true
    STOPPED=1
fi

if [ "$STOPPED" -eq 1 ]; then
    echo "Dashboard stopped."
else
    echo "No running dashboard instance found."
fi
