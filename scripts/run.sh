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
    echo "[INFO] Activating virtual environment..."
    source .venv/bin/activate
fi

APP_PORT="$(grep -m1 '^APP_PORT=' .env 2>/dev/null | cut -d '=' -f2-)"
APP_PORT="${APP_PORT:-8088}"

echo "[INFO] Starting news-scraper on port $APP_PORT..."
uvicorn app.main:app --reload --port "$APP_PORT"
