#!/bin/bash

# news-scraper API test script
# Usage: HOST=localhost PORT=8088 SCRAPE_URL="https://example.com" ./test_scraper.sh

set -e

TOKEN="${TOKEN:-}"
HOST="${HOST:-localhost}"
PORT="${PORT:-8088}"
SCRAPE_URL="${SCRAPE_URL:-https://www.acn.gov.it/portale/csirt-italia/alert-e-bollettini}"
MAX_ARTICLES="${MAX_ARTICLES:-1}"

echo "=== news-scraper API Test ==="
echo "Host: $HOST"
echo "Port: $PORT"
echo "Scrape URL: $SCRAPE_URL"
echo "Max articles: $MAX_ARTICLES"
echo ""

# Health check
echo "[1/2] Health Check..."
curl -s http://${HOST}:${PORT}/health | python3 -m json.tool || echo "Health check failed"
echo ""

# Scrape request
echo "[2/2] Scraping news..."
curl -s -X POST "http://${HOST}:${PORT}/scrape" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{\"url\": \"$SCRAPE_URL\", \"max_articles\": $MAX_ARTICLES}" | python3 -m json.tool

echo ""
echo "=== Test Complete ==="
