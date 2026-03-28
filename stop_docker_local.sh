#!/bin/bash
# Script to stop the iClassPro Dashboard multi-user environment
# Removes all containers and volumes

cd "$(dirname "$0")"

echo "================================================"
echo "Stopping iClassPro Multi-User Environment"
echo "================================================"
echo ""

# Check if docker-compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Error: docker-compose is not installed."
    exit 1
fi

# Check if containers are running
if ! docker-compose ps 2>/dev/null | grep -q "Up"; then
    echo "ℹ️  No running containers found."
    exit 0
fi

echo "🛑 Stopping containers..."
docker-compose down

echo "✅ Containers stopped and removed."
echo ""
echo "To remove the database volume and start fresh:"
echo "  docker volume rm iclasspro-driver_postgres_data"
echo ""
