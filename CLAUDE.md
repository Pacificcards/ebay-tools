# ebay-tools — Claude Context

Monorepo for Pacific Cards Co. eBay operations. Three independent subprojects share the same repo and Supabase database.

## Subprojects

### 1. Analytics Pipeline (`analytics/`)
Daily pipeline fetching eBay data into Supabase. Runs via `analytics-ingest.yml` at 8am UTC.

Steps in order:
1. `sync_listings` — active listings → `listing_metadata`
2. `fetch_analytics` — traffic metrics → `listing_metrics_raw`
3. `fetch_orders` — order line items → `orders_raw`
4. `fetch_finances` — all financial transactions → `order_fees`
5. `compute_metrics` — derived metrics → `listing_metrics_computed`

**Dashboard:** `dashboard/app.py` — Streamlit, two tabs: Mission Control + Listing Deep Dive.

### 2. P&L Accounting (`pl/`)
Google Sheets-based P&L. Script: `pl/sync_to_sheets.py`. Runs daily via `pl-ingest.yml` (triggers after analytics ingest).

Sheet tabs: **Sales**, **Purchases**, **Ad Fees**, **P&L by Group**, **New Entries**

- `gross_sale` = item price + buyer-paid shipping
- `net_payout` = eBay SALE CREDIT after fees, proportionally split for multi-line orders
- `group` column is user-editable and preserved across syncs
- Credentials: `pl/credentials/service_account.json` (gitignored)
- Service account: `ebay-tools-sheets@pcc-accounting.iam.gserviceaccount.com`

### 3. Listener (`listener/`)
Scans eBay every 15 minutes for underpriced cards on a watchlist. Fires Discord alerts on new finds.

- Triggered by cron-job.org → `workflow_dispatch` on `listener.yml`
- Schedule: :03/:18/:33/:48, every hour, 6am–9pm Pacific (job ID: 7684877)
- Dedup via Supabase `listener_seen_items` table
- Results logged to Google Sheet (Watchlist + Observed Listings tabs)

#### Watchlist tab columns (in order)
`Active (Y/N)` | `Description` | `Category` | `Market Price` | `Max Price ($)` | `Min Price ($)` | `Hint URL(s)` | `EPID` | `EPID Status`

#### Key listener behavior
- Alert trigger is based on **Max Price**, not Market Price
- % calculation uses Market Price when set, falls back to Max Price
- Discord alert: `🟢/🔴 X% below/above market ($Y)` if Market Price set; `X% below/above your $Y target` if blank
- Emoji inline with the % figure: 🟢 = below market, 🔴 = above market; only shown when Market Price is set
- EPID lookup: uses Hint URL → Browse API → Catalog API fallback → keyword search
- `COL_EPID = 8`, `COL_EPID_STATUS = 9` (1-based, in `listener/sheets.py`)

## Key Commands

```bash
# Run full data refresh (last 30 days)
./refresh.sh

# Custom date range
./refresh.sh 2026-01-01

# Sync P&L to Google Sheets
.venv/bin/python pl/sync_to_sheets.py

# Run tests
.venv/bin/python -m pytest tests/ -q
```

## Environment

- `.venv` at repo root — always use `.venv/bin/python`
- `.env` at repo root (gitignored) — contains `SUPABASE_DB_URL`, `EBAY_*`, `SHEETS_DOC_ID`, `GOOGLE_CREDS_PATH`
- `pl/credentials/` is gitignored — never commit

## GitHub Secrets (org: Pacificcards)
`EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `EBAY_REFRESH_TOKEN`, `SUPABASE_DB_URL`, `GOOGLE_SHEETS_CREDENTIALS`, `LISTENER_SHEET_ID`, `PL_SHEETS_DOC_ID`, `DISCORD_WEBHOOK_URL`

## Constraints
- Do NOT fetch eBay developer docs from the web — user downloads PDFs and places in `/Users/eastcoastlimited/ClaudeCode/ebay_dev_docs/`
- eBay refresh token valid ~Nov 2027. Re-gen: `/Users/eastcoastlimited/ClaudeCode/ebay_campaign_scheduler/get_refresh_token.py`
- Legacy scheduler repo at `/Users/eastcoastlimited/ClaudeCode/ebay_campaign_scheduler` — do not touch unless asked
- cron-job.org API key is in `.claude/settings.local.json` (not in repo)

## Open TODOs

### Listener
- No open items — fully operational as of 2026-05-31

### P&L
1. Handle refunds — 14 refunded orders ($123.27) show as positive revenue in Sales tab
2. Categorize Ad Fees tab — unlabeled mix of postage, ad fees, store subscription, refunds, credits
3. Surface postage in P&L — $1,306 in SHIPPING_LABEL spend not flowing into P&L by Group costs

### Analytics Dashboard
1. Add revenue column to Mission Control + revenue trend to Deep Dive
2. Surface conversion rate in Deep Dive (`listing_metrics_computed` has it, dashboard never reads it)
3. Mission Control trend view — currently shows only yesterday; add 7–30 day aggregate chart
4. Tag-based filtering — `listing_tags` table exists in schema but is never read

## Session Log

### 2026-05-31
- Discord alert: added 🟢/🔴 emoji inline with the % figure (e.g. `🟢 18% below market`) — only when Market Price is set
- Confirmed emoji commit was not pushed to GitHub (was 1 commit ahead of origin); pushed manually after a real alert fired without emojis

### 2026-05-30
- Listener Watchlist columns restructured: Active moved to col A, Market Price inserted after Category (col D)
- Discord alert updated: shows `X% below market ($Y)` when Market Price set; falls back to max price text when blank
- % calculation now uses Market Price when available, Max Price otherwise; alert trigger still based on Max Price
- Cron schedule shifted from :00/:15/:30/:45 to :03/:18/:33/:48 (via cron-job.org API)
- CLAUDE.md created and committed
- `save-progress` skill created at `~/.claude/skills/save-progress/SKILL.md`
