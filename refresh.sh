#!/usr/bin/env bash
# Weekly data refresh: fetch orders + finances for the last 30 days, then sync to Sheets.
# Run from the repo root: ./refresh.sh
# Optional: pass a custom start date as the first argument, e.g.: ./refresh.sh 2026-01-01

set -euo pipefail

cd "$(dirname "$0")"

START=${1:-$(date -v-30d +%Y-%m-%d 2>/dev/null || date -d "30 days ago" +%Y-%m-%d)}

echo "==> Refreshing data from $START to today"

echo ""
echo "--- Step 1/3: Fetching orders ---"
BACKFILL=1 BACKFILL_START="$START" .venv/bin/python -c "
from dotenv import load_dotenv; load_dotenv('.env')
from traffic_analytics.fetch_orders import fetch_and_store; fetch_and_store()
"

echo ""
echo "--- Step 2/3: Fetching finances ---"
BACKFILL=1 BACKFILL_START="$START" .venv/bin/python -c "
from dotenv import load_dotenv; load_dotenv('.env')
from traffic_analytics.fetch_finances import fetch_and_store; fetch_and_store()
"

echo ""
echo "--- Step 3/3: Syncing to Google Sheets ---"
.venv/bin/python pl/sync_to_sheets.py

echo ""
echo "==> Done."
