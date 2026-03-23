#!/bin/bash
# Script to prepare the Python virtual environment and dependencies
# This is sourced by run_dashboard.sh and run_enrollment.sh

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
python3 -m pip install -r requirements.txt &> /dev/null

# Ensure playwright browser is installed
echo "Ensuring Playwright browser is ready..."
python3 -m playwright install chromium &> /dev/null

# Create .env if it doesn't exist
if [ ! -f .env ]; then
    echo "Creating .env file from template..."
    cp .env.example .env
fi
