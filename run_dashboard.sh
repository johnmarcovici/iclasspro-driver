#!/bin/bash
# Script to launch the iClassPro Enrollment Web Dashboard
# This script handles environment setup and launches the server.

# Change to the script directory
cd "$(dirname "$0")"

# Check if python3 is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is not installed. Please install it to continue."
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "First-time setup: Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install/Update requirements
echo "Checking dependencies..."
pip install -r requirements.txt &> /dev/null

# Ensure playwright browser is installed
echo "Ensuring Playwright browser is ready..."
playwright install chromium &> /dev/null

# Create .env if it doesn't exist
if [ ! -f .env ]; then
    echo "Creating .env file from template..."
    cp .env.example .env
fi

# Kill any existing process on port 8000
echo "Stopping any existing server instances..."
fuser -k 8000/tcp 2>/dev/null || true
sleep 1 # Give it a moment to fully shut down

# Start the FastAPI web server using uvicorn
echo "Starting iClassPro Enrollment Dashboard..."
echo "Access the dashboard at: http://localhost:8000"
echo "Press Ctrl+C to stop the server."

# Run uvicorn directly
uvicorn app:app --host 0.0.0.0 --port 8000
