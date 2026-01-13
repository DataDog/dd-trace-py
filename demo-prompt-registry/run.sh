#!/bin/bash
# Run demo scripts with dd-auth staging credentials
#
# Usage:
#   ./run.sh scripts/01_basic_fetch.py
#   ./run.sh scripts/02_chat_template.py
#   ./run.sh scripts/04_caching_performance.py

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -z "$1" ]; then
    echo "Usage: ./run.sh <script>"
    echo ""
    echo "Available scripts:"
    ls -1 scripts/*.py 2>/dev/null | sed 's/^/  /'
    exit 1
fi

if [ ! -f "$1" ]; then
    echo "Error: Script not found: $1"
    exit 1
fi

# Activate venv and run with dd-auth for staging credentials
dd-auth --domain dd.datad0g.com "$SCRIPT_DIR/.venv/bin/python" "$1"
