#!/bin/bash
# Example script for autonomous iClassPro enrollment
# This script handles environment setup and runs the enrollment task.

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

# Run the enrollment with any additional arguments passed to this script
echo "Starting iClassPro enrollment at $(date)"
python iclasspro.py "$@"

echo "Enrollment completed at $(date)"
