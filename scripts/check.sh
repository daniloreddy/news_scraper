#!/bin/bash
set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
    echo "[INFO] Creating virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    echo "[INFO] Installing dependencies..."
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

VENV=".venv/bin"

echo "[1/4] ruff format ..."
$VENV/ruff format app tests

echo "[2/4] ruff check ..."
$VENV/ruff check app tests --fix

echo "[3/4] mypy ..."
$VENV/mypy app

echo "[4/4] pytest ..."
$VENV/pytest tests -v

echo ""
echo "All checks passed."
