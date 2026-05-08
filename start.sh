#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Create venv if it doesn't exist
if [ ! -d "venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv venv
fi

source venv/bin/activate

# Install / upgrade deps
pip install -q -r requirements.txt

# Copy .env.example if .env doesn't exist
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ""
  echo "  ⚠️  .env file created from .env.example"
  echo "  Open .env and add your API keys before continuing."
  echo ""
  exit 1
fi

echo ""
echo "  MCG Marketing Dashboard starting..."
echo "  Open: http://localhost:5050"
echo ""

python app.py
