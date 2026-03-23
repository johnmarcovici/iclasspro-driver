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

# Start the FastAPI web server using uvicorn
echo "Starting iClassPro Enrollment Dashboard..."
echo "Access the dashboard at: http://localhost:8000"
echo "Press Ctrl+C to stop the server."

# Run uvicorn directly
python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
