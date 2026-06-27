#!/bin/bash
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
    echo "[INFO] Creating virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    echo "[INFO] Installing dependencies..."
    pip install -r requirements.txt
else
    echo "[INFO] Activating virtual environment..."
    source .venv/bin/activate
fi

echo "[INFO] Starting news-scraper..."
uvicorn app.main:app --reload --port 8088
