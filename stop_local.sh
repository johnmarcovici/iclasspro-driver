#!/bin/bash
# Stop iClassPro Dashboard local environment

set -e
cd "$(dirname "$0")"

# Source prepare_env for helper functions
source prepare_env.sh

if ! command -v docker-compose &> /dev/null; then
    echo "❌ Error: docker-compose is required"
    exit 1
fi

if ! run_docker_compose ps 2>/dev/null | grep -q "Up"; then
    echo "ℹ️  No containers running"
    exit 0
fi

echo "🛑 Stopping containers..."
run_docker_compose down

echo "✅ Stopped"
