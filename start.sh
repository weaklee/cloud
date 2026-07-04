#!/bin/bash
# cloud Panel - Start Script
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Install dependencies
python3 -m pip install -r requirements.txt -q

# Initialize admin user (if not exists)
python3 seed.py

# Start server
echo "Starting cloud panel on http://0.0.0.0:3000"
exec python3 run.py
