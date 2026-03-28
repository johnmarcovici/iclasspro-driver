#!/bin/bash
# Script to prepare the Python virtual environment and dependencies
# This is sourced by run_dashboard.sh and run_enrollment.sh

set -e

echo "================================================"
echo "Preparing iClassPro Dashboard Environment"
echo "================================================"
echo ""

# Check if python3 is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is not installed. Please install it to continue."
    exit 1
fi

# Helper function to run docker commands (handles group membership issue)
run_docker() {
    if groups $USER | grep -q docker; then
        # User is in docker group, run normally
        docker "$@"
    else
        # User not in docker group yet, use sudo
        sudo docker "$@"
    fi
}

run_docker_compose() {
    if groups $USER | grep -q docker; then
        # User is in docker group, run normally
        docker-compose "$@"
    else
        # User not in docker group yet, use sudo
        sudo docker-compose "$@"
    fi
}

# Check and install Docker if needed (for containerized deployment)
if ! command -v docker &> /dev/null; then
    echo "📦 Installing Docker..."
    sudo apt-get update &> /dev/null
    sudo apt-get install -y docker.io &> /dev/null
    sudo usermod -aG docker $USER
    echo "✅ Docker installed"
    echo "   Note: Docker will work immediately, but logout/login needed for permanent non-sudo access"
else
    echo "✅ Docker already installed"
fi

# Check and install docker-compose if needed
if ! command -v docker-compose &> /dev/null; then
    echo "📦 Installing docker-compose..."
    sudo apt-get install -y docker-compose &> /dev/null
    echo "✅ docker-compose installed"
else
    echo "✅ docker-compose already installed"
fi

# Verify Docker works (with sudo if needed)
if ! run_docker ps &>/dev/null; then
    echo "⚠️  Docker requires sudo. Attempting with elevated privileges..."
    if ! sudo docker ps &>/dev/null; then
        echo "❌ Docker not working. Please restart your terminal."
        exit 1
    fi
fi

echo ""

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "🐍 Creating Python virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install/Update requirements
echo "📚 Installing Python dependencies..."
python3 -m pip install -r requirements.txt &> /dev/null

# Ensure playwright browser is installed
echo "🌐 Installing Playwright browser..."
python3 -m playwright install chromium &> /dev/null

# Create .env if it doesn't exist
if [ ! -f .env ]; then
    echo "⚙️  Creating .env file..."
    cp .env.example .env
fi

echo ""
echo "================================================"
echo "✅ Environment Ready!"
echo "================================================"
echo ""
echo "You can now run:"
echo "  • ./run_dashboard.sh      (single-user, no Docker needed)"
echo "  • ./run_local.sh          (multi-user with PostgreSQL)"
echo "  • ./build_cloud.sh        (cloud deployment)"
echo ""
