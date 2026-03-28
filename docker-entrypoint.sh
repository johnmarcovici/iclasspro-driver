#!/bin/bash
set -e

# Initialize database
python -c "from app import _init_db; _init_db()"

# Run the application
PORT=${PORT:-8000}
exec python -m uvicorn app:app --host 0.0.0.0 --port $PORT
