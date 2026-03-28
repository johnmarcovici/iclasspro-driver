#!/bin/bash
# Script to stop the iClassPro Dashboard multi-user environment
# Works with docker-compose, podman-compose, and other container runtimes
#
# Container Runtime Detection:
#   - Respects CONTAINER_CLI environment variable
#   - Auto-detects docker-compose, docker compose, podman-compose, podman compose
#   - Works with any OCI-compatible container tool
#
# Examples:
#   ./stop_docker_local.sh                    # Auto-detect
#   CONTAINER_CLI=podman ./stop_docker_local.sh  # Force podman
#   CONTAINER_CLI="docker compose" ./stop_docker_local.sh  # Force docker v2


cd "$(dirname "$0")"

echo "================================================"
echo "Stopping iClassPro Multi-User Environment"
echo "================================================"
echo ""

# Detect container runtime (same as run script)
if [ -n "$CONTAINER_CLI" ]; then
    COMPOSE_CMD="$CONTAINER_CLI"
else
    if command -v docker-compose &> /dev/null; then
        COMPOSE_CMD="docker-compose"
    elif command -v docker &> /dev/null && docker compose version &> /dev/null; then
        COMPOSE_CMD="docker compose"
    elif command -v podman-compose &> /dev/null; then
        COMPOSE_CMD="podman-compose"
    elif command -v podman &> /dev/null && podman compose version &> /dev/null; then
        COMPOSE_CMD="podman compose"
    else
        echo "❌ Error: No container runtime found."
        exit 1
    fi
fi

# Check if containers are running
if ! $COMPOSE_CMD ps 2>/dev/null | grep -q "Up"; then
    echo "ℹ️  No running containers found."
    exit 0
fi

echo "🛑 Stopping containers..."
$COMPOSE_CMD down

echo "✅ Containers stopped and removed."
echo ""
echo "To remove the database volume and start fresh:"
echo "  $COMPOSE_CMD down -v"
echo ""
