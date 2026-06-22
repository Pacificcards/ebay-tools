# ebay-tools — Claude Context

Monorepo for Pacific Cards Co. eBay operations. Three independent subprojects share the same repo and Supabase database.

## Subprojects

### 1. Traffic Analytics (`traffic_analytics/`)
Daily pipeline fetching eBay data into Supabase. Runs via `analytics-ingest.yml` at 12:00 UTC (5am PT).

Steps in order:
1. `sync_listings` — active listings → `listing_metadata`
2. `fetch_analytics` — traffic metrics → `listing_metrics_raw`
3. `fetch_orders` — order line items → `orders_raw`
4. `fetch_finances` — all financial transactions → `order_fees`
5. `compute_metrics` — derived metrics → `listing_metrics_computed`
6. `send_daily_report` — HTML email with yesterday's metrics for configured listings

**Daily email report:**
- Listings configured in `traffic_analytics/report_listings.json` (array of `{id, name}` — edit on GitHub to add/remove)
- Metrics per listing: Impressions, Views, CTR, Orders, Qty Sold, Ord/1k (orders per 1,000 impressions)
- Each metric shows value + % change vs. day before (DoD) and same day last week (WoW)
- Green = positive %, red = negative; sends to `GMAIL_ADDRESS` via SMTP
- No Streamlit dashboard — email replaced it (2026-06-20)

### 2. P&L Accounting (`pl/`)
Google Sheets-based P&L. Script: `pl/sync_to_sheets.py`. Runs daily via `pl-ingest.yml` (triggers after analytics ingest). GitHub Action name: **"P&L ingest"** (triggerable manually from Actions tab).

Sheet tabs: **Sales**, **Purchases**, **Ad Fees**, **P&L by Group**, **New Entries**

- `gross_sale` = item price + buyer-paid shipping
- `net_payout` = eBay SALE CREDIT after fees, proportionally split for multi-line orders
- `group` column is user-editable on Sales, Purchases, and Ad Fees tabs — preserved across syncs via Supabase
- Ad Fees `group` assignments flow into P&L by Group costs (alongside Purchases costs)
- Purchases tab columns: `purchase_date | description | vendor | total_cost | source | id | group`
  - eBay purchases show vendor = "eBay"; manual entries show vendor from New Entries tab
- New Entries amounts can include `$` sign — stripped automatically on sync
- New Entries processing uses savepoints — one bad row won't abort the whole batch
- Credentials: `pl/credentials/service_account.json` (gitignored)
- Service account: `ebay-tools-sheets@pcc-accounting.iam.gserviceaccount.com`

#### Manual sales via New Entries tab
- Set `type = sale` in the New Entries tab to route a row to the Sales tab (inserts into `orders_raw` with a `MANUAL-` prefixed order_id)
- Set `type = purchase` or leave blank to route to the Purchases tab (existing behavior)
- Any other value stamps `✗ Invalid type: '...'` in the status column and skips the row
- Manual sales: `gross_sale` is blank, `net_payout` = entered amount (no eBay fees)
- Group assigned in New Entries carries through to the Sales tab correctly

### 3. Listener (`listener/`)
Scans eBay every 15 minutes for underpriced cards on a watchlist. Fires Discord alerts on new finds.

- Triggered by cron-job.org → `workflow_dispatch` on `listener.yml`
- Schedule: :03/:18/:33/:48, every hour, 6am–9pm Pacific (job ID: 7684877)
- Dedup via Supabase `listener_seen_items` table
- Results logged to Google Sheet (Watchlist + Observed Listings tabs)

#### Watchlist tab columns (in order)
`Active (Y/N)` | `Description` | `Category` | `Market Price` | `Max Price ($)` | `Min Price ($)` | `Hint URL(s)` | `EPID` | `EPID Status` | `Last Hit` (col J — MAXIFS formula reading Observed Listings tab)

#### Key listener behavior
- Alert trigger is based on **Max Price**, not Market Price
- % calculation uses Market Price when set, falls back to Max Price
- Discord alert: 3-tier emoji 🟢 >5% below / 🟡 within ±5% / 🔴 >5% above market; only when Market Price is set
- Listing time shown as time-only in PT (e.g. `Listed: 2:32 PM`)
- Sellers with 0 feedback score or 0% positive rating are silently skipped — no sheet write, no alert
- Stale alert: daily ~8am PST (16:03 UTC), consolidated Discord message for active rows with no new listings in 3+ days
- Discord ingestion: post natural language in `#watchlist-add` → Claude Haiku parses → new Watchlist row + bot reply
- EPID lookup: uses Hint URL → Browse API → Catalog API fallback → keyword search
- `COL_EPID = 8`, `COL_EPID_STATUS = 9` (1-based, in `listener/sheets.py`)
- Discord bot requires Message Content Intent enabled in Discord developer portal

### 4. Campaign Scheduler (`scheduler/`)
Pauses/resumes eBay Promoted Listings campaigns on a schedule. Triggered by cron-job.org → `workflow_dispatch` on `campaign-scheduler.yml`.

- Campaigns defined in `scheduler/campaigns.json` (id + name — edit here to add/remove/rename)
- Schedule: Pause Mon–Thu 1:30am PT (08:30 UTC), Resume Mon–Wed+Fri 1:30pm PT (20:30 UTC)
- Email notification sent on every run (success or failure) via Gmail secrets
- `EBAY_CAMPAIGN_ID` secret is no longer used — campaigns.json is the source of truth

#### Current campaigns (as of 2026-06-15)
| Name | ID |
|------|----|
| Pokemon Packs | 163073689018 |
| Football Packs | 161285779018 |
| 15 Pokemon Packs | 159727416018 |
| JB Topps Now | 163743927018 |
| Sealed Boxes | 163206658018 |

## Key Commands

```bash
# Run full data refresh (last 30 days)
./refresh.sh

# Custom date range
./refresh.sh 2026-01-01

# Sync P&L to Google Sheets (local)
.venv/bin/python pl/sync_to_sheets.py

# Sync P&L to Google Sheets (via GitHub Action — same result, runs in CI)
gh workflow run pl-ingest.yml --repo Pacificcards/ebay-tools

# Run tests
.venv/bin/python -m pytest tests/ -q
```

## Environment

- `.venv` at repo root — always use `.venv/bin/python`
- `.env` at repo root (gitignored) — contains `SUPABASE_DB_URL`, `EBAY_*`, `SHEETS_DOC_ID`, `GOOGLE_CREDS_PATH`
- `pl/credentials/` is gitignored — never commit

## GitHub Secrets (org: Pacificcards)
`EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `EBAY_REFRESH_TOKEN`, `SUPABASE_DB_URL`, `GOOGLE_SHEETS_CREDENTIALS`, `LISTENER_SHEET_ID`, `PL_SHEETS_DOC_ID`, `DISCORD_WEBHOOK_URL`, `DISCORD_BOT_TOKEN`, `DISCORD_WATCHLIST_CHANNEL_ID`, `ANTHROPIC_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`

## Constraints
- Do NOT fetch eBay developer docs from the web — user downloads PDFs and places in `/Users/eastcoastlimited/ClaudeCode/ebay_dev_docs/`
- eBay refresh token valid ~Nov 2027. Re-gen: `/Users/eastcoastlimited/ClaudeCode/ebay_campaign_scheduler/get_refresh_token.py`
- Legacy scheduler repo at `/Users/eastcoastlimited/ClaudeCode/ebay_campaign_scheduler` — do not touch unless asked
- cron-job.org API key is in `.claude/settings.local.json` (not in repo)

## Open TODOs

### Listener
- No open items — fully operational as of 2026-06-15

### Campaign Scheduler
- No open items — campaigns.json migration complete and tested 2026-06-15

### P&L
1. Handle refunds — refunded orders show as positive revenue in Sales tab
2. Manual sales via New Entries — working as of 2026-06-22; `type = sale` routes to Sales tab
3. Manual entry UI — New Entries tab is functional but clunky; approach (Flask/Streamlit/other) TBD

### Traffic Analytics
1. ~~Add more listings to `report_listings.json`~~ — now has 4 listings (Pokemon 15 Card Lot, NBA Hoops Hobby Box, TAG 10 Mewtwo, TAG 10 Mienfoo)
2. ~~Streamlit dashboard~~ — dropped in favour of daily email (2026-06-20)

### Price Check (planned, not yet built)
New subproject — see plan file at `/Users/eastcoastlimited/.claude/plans/fancy-skipping-teapot.md`

**Concept:** User fills a "Price Check" tab in the Listener Google Sheet with card descriptions; runs a GitHub Action (`Price Check`) to fetch active eBay listings and write back Clearing Price + Holding Price per card.

**Key design decisions locked in:**
- Clearing Price = p30 of filtered distribution ("sell fast")
- Holding Price = p75 of filtered distribution ("wait for right buyer")
- Outlier removal: IQR filter (statistical) + grade/accessory title filter (removes PSA/CGC graded copies and cases from raw card searches)
- Search query: Claude parses the user's description → clean keyword query (full description is too strict, e.g. returns 1 result vs. 60+ for simplified query)
- Results write back to same row in sheet: Clearing Price | Holding Price | # Listings | Last Checked
- Triggered via GitHub Action (`price-check.yml`) — manual `workflow_dispatch`
- Uses existing Listener Sheet (`LISTENER_SHEET_ID`) — new "Price Check" tab
- No new secrets needed

**Tested live:** "Eevee ex 174 Prismatic Evolutions Promo" → 60 clean listings → Clearing $34.99 / Holding $50.00

**Still to decide before building:**
- Exact Claude prompt for query simplification
- Whether to expose raw price range alongside the two recommendations

## Session Log

### 2026-06-22
- P&L: added manual sales support — New Entries `type = sale` routes to `orders_raw` (Sales tab); `type = purchase` or blank routes to `import_queue` (Purchases tab)
- P&L: invalid type values now stamp `✗ Invalid type: '...'` in status column instead of silently defaulting to purchase
- P&L: manual sales show gross_sale blank, net_payout = entered amount
- P&L: fixed group assignment for Sales tab — `fetch_sales()` now returns `group_name` from DB (was hardcoded `''`); `write_sales_tab()` preserves DB group on first sync (same pattern as Purchases)
- P&L: all changes committed and pushed (commit eb3a67f)

### 2026-06-21 (session 2)
- P&L: added `group` column to Ad Fees tab — user assigns groups manually; assignments persisted to `order_fees.group_name` in Supabase
- P&L: Ad Fees group costs now flow into P&L by Group formula (alongside Purchases)
- P&L: added `vendor` column to Purchases tab — manual entries show vendor, eBay entries show "eBay"
- P&L: fixed New Entries → Purchases group carry-through (group was being wiped for new entries)
- P&L: fixed cascading transaction abort — bad New Entries row no longer kills the whole batch (savepoints)
- P&L: fixed dollar sign in amount field (`$77.00` → stripped to `77.00` automatically)
- P&L: data cleanup — found 28 entries stamped-but-not-in-DB (old rollback bug), cleared stamps and re-synced; deleted 17 duplicate rows from import_queue
- P&L: local changes NOT yet committed/pushed — GitHub Action will fail until pushed
- Listener: discussed reducing cron-job.org frequency from every 15 min to hourly to cut GHA costs ~75% — user agreed, pending cron-job.org update (needs API key or manual change)

### 2026-06-21 (session 1)
- Traffic Analytics: fixed trailing comma in `report_listings.json` (user edited on GitHub, invalid JSON caused pipeline failure)
- Traffic Analytics: `report_listings.json` now has 4 listings
- Price Check: designed and tested concept — live eBay search confirmed feasibility; plan file written at `/Users/eastcoastlimited/.claude/plans/fancy-skipping-teapot.md`
- Price Check: validated that full description → 1 result; simplified query → 60+ results; Claude query parsing is the right approach
- Price Check: two-pass filter (IQR + grade/accessory title filter) confirmed working on live data

### 2026-06-20
- Traffic Analytics: renamed subproject from `analytics/` to `traffic_analytics/` throughout repo
- Traffic Analytics: built `send_daily_report.py` — daily HTML email after pipeline with Impressions, Views, CTR, Orders, Qty Sold, Ord/1k; DoD and WoW % changes; green/red colours
- Traffic Analytics: `report_listings.json` config for listing IDs + names (currently 1 listing: Pokemon 15 Card Lot)
- Traffic Analytics: shifted pipeline cron from 8am UTC to 12:00 UTC (5am PT)
- Traffic Analytics: Streamlit dashboard dropped — email is the analytics surface going forward

### 2026-06-15
- Listener: fixed stale alert date parsing (gspread returns dates in locale format, not YYYY-MM-DD — now handles multiple formats + serial numbers)
- Listener: replaced relative "X min ago" timestamp with listing posted time in PT (time-only, e.g. `2:32 PM`)
- Listener: added seller filter — 0 feedback score or 0% positive rating silently skipped
- Campaign scheduler: discovered active code lives in `scheduler/` within this repo (not the legacy `ebay_campaign_scheduler` repo)
- Campaign scheduler: moved campaign IDs from `EBAY_CAMPAIGN_ID` secret to `scheduler/campaigns.json` with human-readable names; tested pause — all 5 campaigns SUCCESS

### 2026-06-10
- Listener: stale market price alert shipped (daily 8am PST, reads Last Hit col J from Watchlist)
- Listener: Discord watchlist ingestion shipped (`#watchlist-add` → Claude Haiku → Watchlist row + bot reply)
- Listener: emoji updated to 3-tier (🟢/🟡/🔴)
- P&L: date format fix for New Entries tab, group label persistence fix

### 2026-05-31
- Discord alert: added 🟢/🔴 emoji inline with the % figure — only when Market Price is set
