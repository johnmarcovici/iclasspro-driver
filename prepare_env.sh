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

REQUIREMENTS_STAMP="venv/.requirements_installed"
PLAYWRIGHT_STAMP="venv/.playwright_chromium_installed"
PLAYWRIGHT_CACHE_DIR="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"

# Install or update requirements only when needed
if [ ! -f "$REQUIREMENTS_STAMP" ] || [ requirements.txt -nt "$REQUIREMENTS_STAMP" ]; then
    echo "Installing Python dependencies..."
    python3 -m pip install -r requirements.txt
    touch "$REQUIREMENTS_STAMP"
else
    echo "Dependencies already up to date."
fi

# Ensure Playwright's Chromium browser is installed only when missing
if find "$PLAYWRIGHT_CACHE_DIR" -maxdepth 1 -type d -name 'chromium-*' -print -quit 2>/dev/null | grep -q .; then
    echo "Playwright Chromium already installed."
    touch "$PLAYWRIGHT_STAMP"
else
    echo "Installing Playwright Chromium browser..."
    python3 -m playwright install chromium
    touch "$PLAYWRIGHT_STAMP"
fi

# Create .env if it doesn't exist
if [ ! -f .env ]; then
    echo "Creating .env file from template..."
    cp .env.example .env
fi
