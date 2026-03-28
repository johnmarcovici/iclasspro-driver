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

# Warn if containers already running
if run_docker_compose ps 2>/dev/null | grep -q "Up"; then
    echo "⚠️  Containers already running. Use ./stop_local.sh to stop them."
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    [[ ! $REPLY =~ ^[Yy]$ ]] && exit 0
fi

echo "🚀 Starting services..."
run_docker_compose up -d

echo "✅ Services ready!"
echo ""
echo "📊 Dashboard: http://localhost:8000"
echo "🗄️  pgAdmin:  http://localhost:5050 (admin / admin)"
echo ""
echo "To see logs:      run_docker_compose logs -f app"
echo "To stop:          ./stop_local.sh"
echo ""

# Wait for app to be ready
sleep 2
for i in {1..30}; do
    if curl -s http://localhost:8000/login > /dev/null 2>&1; then
        echo "🎉 Everything is running!"
        exit 0
    fi
    echo -n "."
    sleep 1
done

echo ""
echo "⚠️  Dashboard not responding yet. Check logs with: ./stop_local.sh && ./run_local.sh"
