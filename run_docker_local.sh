#!/bin/bash
# Script to launch iClassPro Dashboard with PostgreSQL (multi-user setup)
# Run this for local multi-user testing with a real database

set -e

cd "$(dirname "$0")"

echo "================================================"
echo "iClassPro Dashboard - Local Multi-User Environment"
echo "================================================"
echo ""

# Check if docker and docker-compose are installed
if ! command -v docker &> /dev/null; then
    echo "❌ Error: Docker is not installed."
    echo "   Download from: https://www.docker.com/products/docker-desktop"
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ Error: docker-compose is not installed."
    echo "   It usually comes with Docker Desktop."
    exit 1
fi

echo "✅ Docker is installed"
echo ""

# Check if containers are already running
if docker-compose ps 2>/dev/null | grep -q "Up"; then
    echo "⚠️  Containers are already running."
    echo "   Run './stop_docker_local.sh' to stop them, or continue here."
    read -p "   Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 0
    fi
fi

echo "🚀 Starting services..."
echo ""

# Start services
docker-compose up -d

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
echo "💻 App logs:      docker-compose logs -f app"
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
