#!/bin/bash
set -e

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
