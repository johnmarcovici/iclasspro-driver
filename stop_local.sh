#!/bin/bash
# Stop iClassPro Dashboard local environment

cd "$(dirname "$0")"

if ! command -v docker-compose &> /dev/null; then
    echo "❌ Error: docker-compose is required"
    exit 1
fi

if ! docker-compose ps 2>/dev/null | grep -q "Up"; then
    echo "ℹ️  No containers running"
    exit 0
fi

echo "🛑 Stopping containers..."
docker-compose down

echo "✅ Stopped"
