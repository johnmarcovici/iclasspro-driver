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

# Optional Docker setup for containerized workflows.
if [ "${REQUIRE_DOCKER:-0}" = "1" ]; then
    # Helper function to run docker commands (automatically handles permissions)
    # First check if docker works without sudo, if not use sudo.
    _docker_needs_sudo=
    _check_docker_sudo() {
        if [ -z "$_docker_needs_sudo" ]; then
            if docker ps &>/dev/null; then
                _docker_needs_sudo=0
            else
                _docker_needs_sudo=1
            fi
        fi
    }

    run_docker() {
        _check_docker_sudo
        if [ "$_docker_needs_sudo" -eq 1 ]; then
            sudo docker "$@"
        else
            docker "$@"
        fi
    }

    run_docker_compose() {
        _check_docker_sudo
        if [ "$_docker_needs_sudo" -eq 1 ]; then
            sudo docker-compose "$@"
        else
            docker-compose "$@"
        fi
    }

    # Check and install Docker if needed (for containerized deployment)
    if ! command -v docker &> /dev/null; then
        echo "📦 Installing Docker..."
        sudo apt-get update &> /dev/null
        sudo apt-get install -y docker.io &> /dev/null
        sudo usermod -aG docker "$USER"
        echo "✅ Docker installed"
        echo "   Note: Docker will work immediately with sudo; relogin enables passwordless access."
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

    # Verify Docker daemon access, with sudo fallback when needed.
    if ! run_docker ps &>/dev/null; then
        echo "❌ Docker daemon is not reachable. Start Docker and retry."
        exit 1
    fi

    echo ""
fi

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

# Only print command suggestions when this file is executed directly.
# When sourced by run_* scripts, this banner is noisy and confusing.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    echo ""
    echo "You can now run:"
    echo "  • ./run_dashboard.sh      (single-user, no Docker needed)"
    echo "  • ./run_local.sh          (multi-user with PostgreSQL)"
    echo "  • ./build_cloud.sh        (cloud deployment)"
    echo ""
fi
