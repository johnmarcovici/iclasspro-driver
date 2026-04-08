#!/bin/bash
# Script to launch the iClassPro Enrollment Web Dashboard
# This script handles environment setup and launches the server.

# Change to the script directory
cd "$(dirname "$0")"

# Prepare environment (creates venv, installs dependencies)
source prepare_env.sh

# Kill any existing process on port 8000
echo "Stopping any existing server instances..."
fuser -k 8000/tcp 2>/dev/null || true
sleep 1 # Give it a moment to fully shut down

LOG_FILE="/tmp/iclasspro-dashboard.log"
PID_FILE="/tmp/iclasspro-dashboard.pid"

# Start the FastAPI web server using uvicorn
echo "Starting iClassPro Enrollment Dashboard..."
echo "Access the dashboard at: http://localhost:8000"
echo "Logs: $LOG_FILE"

nohup python3 -m uvicorn app:app --host 0.0.0.0 --port 8000 > "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

sleep 2
if kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "Dashboard is running in the background (PID: $SERVER_PID)."
    echo "To stop it later, run: kill $(cat "$PID_FILE")"
else
    echo "Dashboard failed to start. Recent log output:"
    tail -n 50 "$LOG_FILE"
    exit 1
fi
