#!/bin/bash
# Script per sistemi Unix/Linux (WSL o Docker context)

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
    echo "[ERROR] Virtual environment (.venv) not found."
    exit 1
fi

echo "[INFO] Activating virtual environment..."
source .venv/bin/activate

echo "[INFO] Starting news-scraper..."
uvicorn app.main:app --reload --port 8088
