#!/bin/bash
# Script to launch iClassPro Dashboard with PostgreSQL (multi-user setup)
# Container runtime agnostic - supports docker, podman, and others
#
# Container Runtime Detection:
#   - Respects CONTAINER_CLI environment variable for override
#   - Auto-detects docker-compose, docker compose, podman-compose, podman compose
#   - Works with any OCI-compatible container tool
#
# Examples:
#   ./run_docker_local.sh                    # Auto-detect
#   CONTAINER_CLI=podman ./run_docker_local.sh  # Force podman
#   CONTAINER_CLI="docker compose" ./run_docker_local.sh  # Force docker v2

set -e

cd "$(dirname "$0")"

echo "================================================"
echo "iClassPro Dashboard - Local Multi-User Environment"
echo "================================================"
echo ""

# Detect container runtime
# Support CONTAINER_CLI override via environment
if [ -n "$CONTAINER_CLI" ]; then
    COMPOSE_CMD="$CONTAINER_CLI"
else
    # Try to find compose command in order of preference
    if command -v docker-compose &> /dev/null; then
        COMPOSE_CMD="docker-compose"
    elif command -v docker &> /dev/null && docker compose version &> /dev/null; then
        # Docker v2 with compose plugin
        COMPOSE_CMD="docker compose"
    elif command -v podman-compose &> /dev/null; then
        COMPOSE_CMD="podman-compose"
    elif command -v podman &> /dev/null && podman compose version &> /dev/null; then
        # Podman v4+ with compose plugin
        COMPOSE_CMD="podman compose"
    else
        echo "❌ Error: No container runtime found."
        echo ""
        echo "Install one of:"
        echo "  • Docker Desktop - https://www.docker.com/products/docker-desktop"
        echo "  • Podman - https://podman.io/docs/installation"
        echo ""
        exit 1
    fi
fi

echo "✅ Using container runtime: $COMPOSE_CMD"
echo ""

# Check if containers are already running
if $COMPOSE_CMD ps 2>/dev/null | grep -q "Up"; then
    echo "⚠️  Containers are already running."
    echo "   Run './stop_container_local.sh' to stop them, or continue here."
    read -p "   Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 0
    fi
fi

echo "🚀 Starting services..."
echo ""

# Start services
$COMPOSE_CMD up -d

echo ""
echo "✅ Services started successfully!"
echo ""
echo "================================================"
echo "Access the application at:"
echo ""
echo "📊 Dashboard:     http://localhost:8000"
echo "   Login with your iClassPro credentials"
echo ""
echo "🗄️  pgAdmin:      http://localhost:5050"
echo "   User: admin@example.com"
echo "   Pass: admin"
echo ""
echo "💻 App logs:      $COMPOSE_CMD logs -f app"
echo "🛑 Stop all:      ./stop_docker_local.sh"
echo ""
echo "================================================"
echo ""
echo "Waiting for services to be ready..."
sleep 3

# Check if app is responding
echo "Testing connection..."
for i in {1..30}; do
    if curl -s http://localhost:8000/login > /dev/null 2>&1; then
        echo "✅ Dashboard is ready!"
        echo ""
        break
    fi
    echo -n "."
    sleep 1
done

echo ""
echo "🎉 Everything is running!"
