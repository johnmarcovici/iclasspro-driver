#!/bin/bash
# Launch iClassPro Dashboard with PostgreSQL (local multi-user environment)

set -e
cd "$(dirname "$0")"

# Prepare environment (installs Docker, creates venv, installs Python dependencies)
REQUIRE_DOCKER=1 source prepare_env.sh

# Require Docker
if ! command -v docker &> /dev/null || ! command -v docker-compose &> /dev/null; then
    echo "❌ Error: Docker and docker-compose are required"
    echo "   Try running: sudo apt-get install docker.io docker-compose"
    exit 1
fi

echo "================================================"
echo "iClassPro Dashboard - Local Multi-User Setup"
echo "================================================"
echo ""

# Stop any existing containers silently to ensure fresh start
if run_docker_compose ps 2>/dev/null | grep -q "Up"; then
    echo "🔄 Restarting containers..."
    run_docker_compose down &>/dev/null || true
    sleep 1
fi

# Ensure bind-mounted entrypoint is executable before container start.
chmod +x docker-entrypoint.sh

echo "🚀 Building and starting services..."
run_docker_compose up -d --build

echo "✅ Services ready!"
echo ""
echo "📊 Dashboard: http://localhost:8000"
echo "🗄️  pgAdmin:  http://localhost:5050 (admin / admin)"
echo ""
echo "To see app logs:  ./view_logs_local.sh"
echo "To stop:          ./stop_local.sh"
echo ""

# Wait for app to be ready
sleep 2
for i in {1..60}; do
    if curl -s http://localhost:8000/login > /dev/null 2>&1; then
        echo "🎉 Everything is running!"
        exit 0
    fi
    echo -n "."
    sleep 1
done

echo ""
echo "⚠️  Dashboard is still warming up or failed to start."
echo "   Expected URLs:"
echo "   - Dashboard: http://localhost:8000"
echo "   - pgAdmin:   http://localhost:5050"
echo "   Troubleshoot with: ./view_logs_local.sh"
