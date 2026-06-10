#!/bin/bash
# Script per testare l'API su sistemi Unix/Linux

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
    echo "[ERROR] Virtual environment (.venv) not found."
    exit 1
fi

source .venv/bin/activate
python3 scripts/test_api.py
