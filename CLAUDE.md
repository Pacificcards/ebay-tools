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

#### Column schemas (current)
- **Sales**: `order_date | title | gross_sale | net_payout | order_id | ebay_order_id | shipping_cost | group`
  - `order_id` = full internal key (e.g. `22-14785-63636_10082049011622`) — used for group persistence
  - `ebay_order_id` = base order ID (e.g. `22-14785-63636`) — matches Ad Fees tab for cross-reference; blank for manual sales
  - `shipping_cost` = from `order_fees` SHIPPING_LABEL DEBIT, proportionally split for multi-line orders; blank if label was via Pirateship or not yet reported by eBay
- **Purchases**: `purchase_date | description | vendor | total_cost | source | id | group`
- **Ad Fees**: `date | fee_type | amount | order_id | listing_id | title | transaction_id | group`
  - `order_id` populated for SHIPPING_LABEL rows; blank for NON_SALE_CHARGE (ad spend has no order-level attribution)
  - `listing_id` + `title` populated for most rows; derived from `listing_metadata` or `orders_raw` via join

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

#### Planned: Listing-level P&L hierarchy (NOT YET BUILT — 2026-06-23)
Architecture decision: move group assignment from order/fee level to **listing level**.

**Hierarchy:** Group > Listing > Order
- A group contains multiple listings (e.g. all cards from one hobby box break)
- A listing has many orders; all orders for a listing map to the same group
- Shipping costs and ad spend attributed at listing level, then roll up to group

**New table: `listing_groups`** (`listing_id | group_name | title`)
- Do NOT anchor to `listing_metadata` — it only covers listings active since 2026-05-23; 164 of 243 distinct listing_ids in `orders_raw` are missing from it
- Seed titles from `orders_raw.title` — all 243 distinct listing_ids have titles (confirmed complete)
- Group assignment happens in a new **Listings** tab in the sheet (one row per listing, one `group` column to edit)
- Sales, Ad Fees tabs become read-only for group (display only, derived from listing)

**Cost attribution facts (confirmed from DB):**
- Shipping labels (`SHIPPING_LABEL`): 721 of 729 rows have `order_id`; join to `orders_raw` via `SPLIT_PART(order_id, '_', 1)` — 591 orders matched, $1,377 attributable
- Ad spend (`NON_SALE_CHARGE`): 333 of 336 rows have `listing_id`; NO `order_id` — listing-level only, $2,132 total
- Existing group assignments on `orders_raw` and `order_fees`: clean slate acceptable, will be reassigned via Listings tab

**Goal:** per-listing profitability (revenue - shipping label - ad spend = listing profit); groups aggregate listings for batch P&L

**Still to decide before building:**
- Whether to add a P&L by Listing tab (in addition to P&L by Group)
- Exact migration plan for existing group assignments

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

### 4. Market Monitor (`market_monitor/`)
Daily pipeline that tracks active eBay listings for ~12 sealed product search queries. Runs via `market-monitor.yml` at 10:30 UTC (after analytics-ingest).

**Steps in order:**
1. `fetch_market.py` — reads `pcc_sealed_monitor` Google Sheet ("Queries" tab), resolves category names → IDs via eBay Taxonomy API, searches eBay Browse API (paginated, BIN + auction, up to 2,000 results/query), upserts per-listing tracking and daily aggregate stats to Supabase
2. `generate_dashboard.py` — reads last 90 days of DB data, writes `docs/market/market_data.json`
3. GHA commits `market_data.json` back to repo → GitHub Pages serves updated dashboard

**Google Sheet config (`pcc_sealed_monitor` → `Queries` tab):**
Columns: `Name | Query | Category | Min Price | Max Price | Active`
- `Category` = plain English name (e.g. "Trading Card Games") — code looks up the ID via eBay Taxonomy API
- `Active` = checkbox — unchecked rows are skipped
- Query `id` (DB key) is auto-derived: slugified lowercase Name

**Database tables:**
- `market_snapshots` — daily aggregate per query: `(query_id, date)` UNIQUE; stores count, new_count, gone_count, price_min/max/mean/median/p25/p75
- `market_snapshot_items` — per-listing lifespan: `(query_id, item_id)` UNIQUE; `first_seen` set on insert, `last_seen` updated each run. "New" = first_seen = today; "Gone" = last_seen = yesterday (proxy: sold or ended)

**Dashboard (GitHub Pages):**
URL: `pacificcards.github.io/ebay-tools/market/` (once GitHub Pages enabled)
Views: overview table (all queries, DoD comparison), trend charts (listing count + median price, 90 days), price histogram, new listings today table, gone today table.
Reads `docs/market/market_data.json` at page load (Chart.js, no backend needed).

**Key files:**
- `market_monitor/sheets.py` — `load_queries()` reads Google Sheet
- `market_monitor/fetch_market.py` — main daily fetcher
- `market_monitor/generate_dashboard.py` — writes `market_data.json`
- `listener/ebay.py` — `search_all_listings()` (paginated, no time filter, BIN+auction) + `get_category_id()` (Taxonomy API)
- `docs/market/index.html` — static dashboard HTML (committed once, not regenerated)
- `docs/market/market_data.json` — regenerated daily by pipeline

**GitHub secret needed:** `MARKET_MONITOR_SHEET_ID`

### 5. Campaign Scheduler (`scheduler/`)
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
`EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `EBAY_REFRESH_TOKEN`, `SUPABASE_DB_URL`, `GOOGLE_SHEETS_CREDENTIALS`, `LISTENER_SHEET_ID`, `PL_SHEETS_DOC_ID`, `MARKET_MONITOR_SHEET_ID` (pending — user to add), `DISCORD_WEBHOOK_URL`, `DISCORD_BOT_TOKEN`, `DISCORD_WATCHLIST_CHANNEL_ID`, `ANTHROPIC_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`

## Constraints
- Do NOT fetch eBay developer docs from the web — user downloads PDFs and places in `/Users/eastcoastlimited/ClaudeCode/ebay_dev_docs/`
- **Schema change gotcha (P&L):** when adding/removing columns that shift the `group` column index, BOTH `_read_*_groups()` AND the preservation block inside `write_*_tab()` must be updated — they are separate and both read from the sheet. Add backwards-compat for the old schema so the first sync after a change doesn't corrupt group assignments.
- **gspread 6.x:** `ws.update()` takes `(values, range_name)` — values first. Always pass explicit `'A1'` as range_name to avoid ambiguity. `ws.clear()` may not clear beyond gspread's tracked column range; user-added columns in the sheet survive a clear.
- **eBay Finances API delay:** shipping label transactions can appear hours after purchase. If a label is missing, it may just not have been reported yet — trigger a manual `fetch_finances` re-run before assuming it was purchased externally.
- eBay refresh token valid ~Nov 2027. Re-gen: `/Users/eastcoastlimited/ClaudeCode/ebay_campaign_scheduler/get_refresh_token.py`
- Legacy scheduler repo at `/Users/eastcoastlimited/ClaudeCode/ebay_campaign_scheduler` — do not touch unless asked
- cron-job.org API key is in `.claude/settings.local.json` (not in repo)

## Open TODOs

### Listener
- No open items — fully operational as of 2026-06-15

### Campaign Scheduler
- No open items — campaigns.json migration complete and tested 2026-06-15

### P&L
1. **Listing-level hierarchy refactor** — Group > Listing > Order; new `listing_groups` table; design complete, not yet built (see full spec in P&L section above)
2. Handle refunds — refunded orders show as positive revenue in Sales tab
3. Tests are stale — `write_pl_tab` takes 4 args but tests call it with 3; `fetch_purchases` returns 7 cols but mock uses 6; Sales/Purchases batch preservation tests use old column indices
4. Manual entry UI — New Entries tab functional but clunky; approach TBD

### Traffic Analytics
- No open items — orders/qty bug fixed 2026-06-25 (see session log)

### Market Monitor (built, pending user setup)
New subproject tracking active eBay listing supply and pricing for ~12 sealed product queries. See subproject section below for full spec.

**Pending user actions before first run:**
1. Create Google Sheet named `pcc_sealed_monitor` with a `Queries` tab (headers: `Name | Query | Category | Min Price | Max Price | Active`)
2. Share sheet with service account `ebay-tools-sheets@pcc-accounting.iam.gserviceaccount.com`
3. Add GitHub secret `MARKET_MONITOR_SHEET_ID`
4. Enable GitHub Pages (repo Settings → Pages → main branch, /docs folder)
5. Trigger `Market Monitor` workflow manually for first run

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

### 2026-06-25 / 2026-06-26
- Traffic Analytics: fixed two bugs in daily email orders/qty reporting:
  1. `listing_metrics_raw.orders` stores eBay's `TRANSACTION` metric (units sold, not distinct orders) — 1 order for qty 3 reported as 3 orders. Fixed by sourcing orders from `orders_raw` using `COUNT(DISTINCT SPLIT_PART(order_id, '_', 1))`
  2. Date mismatch: `listing_metrics_raw` uses eBay's PT reporting date; `orders_raw.order_date` is UTC — caused qty to appear on a different day than orders. Fixed by sourcing both metrics from `orders_raw`. Same `COUNT(*)` → `COUNT(DISTINCT ...)` fix applied to `compute_metrics.py`
- Market Monitor: built complete new subproject — `market_monitor/` with `fetch_market.py`, `generate_dashboard.py`, `sheets.py`; `docs/market/index.html` static dashboard; `market-monitor.yml` workflow; two new DB tables; `search_all_listings()` + `get_category_id()` added to `listener/ebay.py`
- Market Monitor: category column changed from "Category ID" (user enters ID) to "Category" (user enters name); code resolves name → ID via eBay Taxonomy API (`get_category_id()` in `listener/ebay.py`), cached per run
- Market Monitor: Google Sheet named `pcc_sealed_monitor` (opened by ID, not name — name is just for user reference)
- All changes committed and pushed (commits 2cdd28f, b33926d, f45f11a)

### 2026-06-24
- P&L: added `ebay_order_id` and `shipping_cost` to Sales tab; shipping cost joins via `SPLIT_PART` on order_id, proportionally split for multi-line orders
- P&L: added `order_id`, `listing_id`, `title` columns to Ad Fees tab (derived from `order_fees` + `orders_raw` + `listing_metadata` via CTE)
- P&L: fixed group preservation bug — `write_sales_tab` had stale index for group column (was reading index 5 after schema change moved group to index 7, causing ebay_order_id to be written as group value); both `_read_sale_groups` and `write_sales_tab`'s internal preservation block now handle old 6-col and new 8-col schemas
- P&L: fixed gspread 6.x deprecation — all `ws.update()` calls now pass values before range_name
- P&L: fixed missing Ad Fees header row and stale user columns — root cause was gspread not clearing beyond its tracked range; fix: explicit `'A1'` range on all update calls
- P&L: eBay label transactions can have same-day reporting delay (order 14-14798-26564 purchased June 24, not in morning pipeline run; resolved by manual `fetch_finances` re-run)
- P&L: all changes committed and pushed (commits 553713d, f39798f, 5a510b1)
- Backlog: added traffic report orders/qty bug (1 order qty 3 → reported as 3 orders qty 0, seen 2026-06-23)
- Backlog: added active listings counter stub (new listener subproject, details TBD)

### 2026-06-23
- P&L: confirmed shipping label costs (SHIPPING_LABEL) ARE attributable to orders — join via `SPLIT_PART(order_id, '_', 1)`; 591 orders matched, $1,377 in attributable shipping
- P&L: confirmed ad spend (NON_SALE_CHARGE) is NOT attributable to orders — no order_id in eBay API response; listing_id only (333/336 rows), $2,132 total
- P&L: designed listing-level hierarchy (Group > Listing > Order) — group assignment moves to listing level via new `listing_groups` table; full spec written in CLAUDE.md
- P&L: ruled out anchoring to `listing_metadata` (only 79 of 243 order listing_ids present — started May 23); confirmed `orders_raw.title` has complete title coverage for all 243 listing_ids
- P&L: implementation not started — user paused to think; will revisit

### 2026-06-22
- P&L: added manual sales support — New Entries `type = sale` routes to `orders_raw` (Sales tab); `type = purchase` or blank routes to `import_queue` (Purchases tab)
- P&L: invalid type values now stamp `✗ Invalid type: '...'` in status column instead of silently defaulting to purchase
- P&L: manual sales show gross_sale blank, net_payout = entered amount
- P&L: fixed group assignment for Sales tab — `fetch_sales()` now returns `group_name` from DB (was hardcoded `''`); `write_sales_tab()` preserves DB group on first sync (same pattern as Purchases)
- P&L: all changes committed and pushed (commit eb3a67f)
- Traffic Analytics: removed GitHub Actions schedule cron — was unreliable (delayed or skipped); replaced with cron-job.org workflow_dispatch at 10:00 UTC (3am PT)
- Traffic Analytics: email send step now skipped on push triggers (`if: github.event_name != 'push'`) — code pushes run the data pipeline but do not fire the email
- Traffic Analytics: cron-job.org job created and validated (workflow_dispatch test run confirmed email sends correctly)

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
