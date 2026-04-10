#!/bin/bash
# Script to launch the iClassPro Enrollment Web Dashboard
# This script handles environment setup and launches the server.

# Change to the script directory
cd "$(dirname "$0")"

# Prepare environment (creates venv, installs dependencies)
source prepare_env.sh

# Stop any existing dashboard instance before launching a fresh one
echo "Stopping any existing server instances..."
./stop_dashboard.sh >/dev/null 2>&1 || true
sleep 1 # Give it a moment to fully shut down

PID_FILE="/tmp/iclasspro-dashboard.pid"

echo $$ > "$PID_FILE"

# Start the FastAPI web server using uvicorn in the foreground.
echo "Starting iClassPro Enrollment Dashboard..."
echo "Access the dashboard at: http://localhost:8000"
echo "Press Ctrl+C to stop the server (or run ./stop_dashboard.sh from another terminal)."

exec python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
