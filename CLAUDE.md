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
- **Sales**: `order_date | title | gross_sale | net_payout | shipping_cost | order_id | ebay_order_id | listing_id | group | source`
  - `shipping_cost` (col E) = from `order_fees` SHIPPING_LABEL DEBIT, proportionally split for multi-line orders; blank if label was via Pirateship or not yet reported by eBay
  - `order_id` (col F) = full internal key (e.g. `22-14785-63636_10082049011622`) — used for group persistence
  - `ebay_order_id` (col G) = base order ID (e.g. `22-14785-63636`) — matches Ad Fees tab for cross-reference; blank for manual sales
  - `listing_id` (col H) = eBay listing ID; blank for manual sales; used to trace listing→group relationship
  - `group` (col I) = user-editable; resolved by header name in code, not hardcoded index
  - `source` (col J) = "eBay" or "Manual"
- **Purchases**: `purchase_date | description | vendor | total_cost | source | id | group`
- **Ad Fees**: `date | fee_type | amount | order_id | listing_id | title | transaction_id | group | category`
  - `order_id` populated for SHIPPING_LABEL rows; blank for NON_SALE_CHARGE (ad spend has no order-level attribution)
  - `listing_id` + `title` populated for most rows; derived from `listing_metadata` or `orders_raw` via join
  - `group` (col H) auto-populated from matching order (SHIPPING_LABEL) or matching listing (NON_SALE_CHARGE) via SQL COALESCE; user-editable and persisted to `order_fees.group_name`
  - `category` (col I) auto-computed, not editable: NON_SALE_CHARGE→"Ad Fee", SHIPPING_LABEL/SHIPPING_MANUAL→"Shipping", else→"Other"
- **P&L by Group**: `group | net_payout | costs | ad_fees | shipping_cost | profit`
  - `net_payout` = SUMIF(Sales!D) per group
  - `costs` = SUMIF(Purchases!D) per group
  - `ad_fees` = BYROW+LAMBDA SUMIFS(Ad Fees!C, group, "Ad Fee") per group
  - `shipping_cost` = BYROW+LAMBDA SUMIFS(Ad Fees!C, group, "Shipping") per group — includes both eBay SHIPPING_LABEL and manual SHIPPING_MANUAL rows
  - `profit` = net_payout − costs − ad_fees − shipping_cost
- **New Entries**: `date | description | type | amount | vendor | payment_method | group | status | record_id`
  - `record_id` (col 9) — stamped on insert: `MANUAL-{hex}` for sales, numeric `import_queue.id` for purchases, `SHIP-{hex}` for shipping
  - Deletion: set `status` to anything containing "mark" + "delet" (e.g. "Marked for Deletion") → hard DELETE from DB, stamp "Deleted {date}"
  - Scope: deletion only works on manual entries (record_id must be present); eBay-sourced rows cannot be deleted via this flow

- `gross_sale` = item price + buyer-paid shipping
- `net_payout` = eBay SALE CREDIT after fees, proportionally split for multi-line orders
- `group` column is user-editable on Sales, Purchases, and Ad Fees tabs — preserved across syncs via Supabase
- Ad Fees `group` assignments flow into P&L by Group costs (alongside Purchases costs)
- Purchases tab columns: `purchase_date | description | vendor | total_cost | source | id | group`
  - eBay purchases show vendor = "eBay"; manual entries show vendor from New Entries tab
  - eBay-sourced purchases are fetched by `traffic_analytics/fetch_ebay_purchases.py` (`ebay-purchases.yml`, separate from `analytics-ingest.yml`) — runs **daily** at 10:00 UTC (3am PT), 14-day lookback window, deduped via `ON CONFLICT DO NOTHING` on `(ebay_item_id, transaction_id)`
- New Entries amounts can include `$` sign — stripped automatically on sync
- New Entries processing uses savepoints — one bad row won't abort the whole batch
- Credentials: `pl/credentials/service_account.json` (gitignored)
- Service account: `ebay-tools-sheets@pcc-accounting.iam.gserviceaccount.com`

#### Manual entries via New Entries tab
| `type` value | Routes to | record_id format | Notes |
|---|---|---|---|
| `purchase` or blank | Purchases tab (`import_queue`) | numeric id | default |
| `sale` | Sales tab (`orders_raw`) | `MANUAL-{hex16}` | gross_sale blank, net_payout = entered amount |
| `shipping` | Ad Fees tab (`order_fees` as SHIPPING_MANUAL) | `SHIP-{hex16}` | appears with category="Shipping"; flows into P&L shipping_cost |
| anything else | — | stamps `✗ Invalid type: '...'` | skipped |

- Group assigned in New Entries carries through to the destination tab correctly
- Required fields for `shipping` type: date, amount, group; description is optional; vendor/payment_method ignored

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
- **Raw cards only** — search filters (both EPID and keyword paths, via `_search_raw_cards()` in `listener/ebay.py`): BIN only (`buyingOptions:{FIXED_PRICE}`), US location only (`itemLocationCountry:US`), listed in last 12h, plus eBay's native `Graded: No` item-aspect filter. The `Graded` aspect is scoped per category, so each search runs separately against 3 assumed trading-card categories (`_RAW_CARD_CATEGORY_IDS = ("183050", "183454", "261328")`) with `aspect_filter=categoryId:<id>,Graded:{No}`, then results are merged/deduped by item_id. No support yet for watching graded cards — see Open TODOs.
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
Daily pipeline that tracks active eBay listings for ~12 sealed product search queries. Runs via `market-monitor.yml` at 11:00 UTC (4am PDT) on a GHA schedule. Live at `pacificcards.github.io/ebay-tools/market/`.

**Steps in order:**
1. `fetch_market.py` — reads `pcc_sealed_monitor` Google Sheet ("Queries" tab), searches eBay Browse API (paginated, BIN + auction, up to 2,000 results/query), upserts per-listing tracking and daily aggregate stats to Supabase
2. `generate_dashboard.py` — reads last 90 days of DB data, writes `docs/market/market_data.json`
3. GHA commits `market_data.json` back to repo → GitHub Pages serves updated dashboard

**Google Sheet config (`pcc_sealed_monitor` → `Queries` tab):**
Columns: `Name | Exclusions | Category | CategoryID | Min Price | Max Price | Active | MSRP | Presale Date | Release Date | Type`
- `CategoryID` = numeric eBay category ID (col D); user fills with a VLOOKUP formula. Code reads this only — non-numeric values log an error and run without category filter. `Category` col exists for the formula reference but is never read by code.
- `Active` = `Yes` / `Y` / `TRUE` — non-yes rows are skipped
- `Exclusions` = CSV of terms → appended as eBay `-keyword` exclusions to the query (per-query, not global)
- `MSRP` = optional; stored in DB and shown as a dashed amber reference line on trend charts
- `Presale Date` / `Release Date` = optional dates for future trend chart annotations
- `Type` = product type label (e.g. "Baseball", "Pokemon", "Disney Lorcana") — shown in Overview table
- Query `id` (DB key) is auto-derived: slugified lowercase Name
- **DO NOT add "case" as a global exclusion** — some queries specifically search for sealed cases. Exclusions are per-row.

**Lot filter (`_LOT_RE` in `fetch_market.py`) — Layer 2 after eBay category filter:**
Filters by title. Catches: `2x / x2`, `lot of N`, `2+ boxes`, `bundle`, `case of N`, `booster box case`, `pack/set of N`, `qty 2–9`, `(4)/(12)/etc in parens`, `team/player/case break`, `spot auction`, `$N auctions`
- **DO NOT use the eBay Taxonomy API for category lookup** — it returned wrong IDs. Use the numeric ID directly in the CategoryID column.

**Key behaviors:**
- BIN + auction both fetched; auction current-bid price stored (not $0); `itemEndDate` stored as `end_time` in `market_snapshot_items`
- Price stats (median, histogram) use BIN-only items — auctions excluded from pricing, counted in supply
- `generate_dashboard.py` uses `max(date)` from DB as "today" — avoids UTC/PT date mismatch
- Queries list in market_data.json filtered to only queries that ran on the most recent snapshot date
- Metadata (msrp, type, presale_date, release_date) read via `DISTINCT ON (query_id) ORDER BY date DESC` — always returns most recent value, not lex max

**Database tables:**
- `market_snapshots` — daily aggregate per query: `(query_id, date)` UNIQUE; stores count, new_count, gone_count, price stats, msrp, presale_date, release_date, type, fetched_at
- `market_snapshot_items` — per-listing lifespan: `(query_id, item_id)` UNIQUE; `first_seen`, `last_seen`, `end_time TIMESTAMPTZ NULL` (auction end from eBay), `url`
- `market_sold_items` — sold comps from sold-comps.com: `(query_id, item_id)` UNIQUE; `sold_price`, `shipping_price`, `total_price`, `buying_format`, `condition`, `ended_at TIMESTAMPTZ` (PT-normalized), `url`, `bid_count`, `seller_username`, `seller_positive_pct`, `seller_feedback_score`, `fetched_at`; index on `(query_id, ended_at DESC)`

**market_data.json keys:**
- `generated_at`, `fetched_at` — date string and ISO timestamp of last pipeline run
- `queries` — list of `{id, name, type, msrp, presale_date, release_date}` for active queries
- `trends`, `today`, `yesterday` — standard snapshot data
- `new_medians`, `gone_medians` — median BIN price of new/gone listings per query
- `bin_listings` — `{title, price, url}` per query
- `auction_listings` — `{title, price, url, end_time}` per query
- `prices` — BIN price floats per query (histogram source)
- `gone_items` — `{item_id, title, price, buying_format, first_seen, url}` per query
- `sold_comps` — per query: `{sold_count, sold_median, sold_p25, sold_p75, sold_fetched_at}` from 30-day window of `market_sold_items`; empty dict `{}` if table not yet populated (generate_dashboard.py handles this gracefully)
- `sold_trends` — per query: array of `{date, median, prices:[]}` for each day in last 30 days (daily sold median + all individual prices; used for time-series on price chart)
- `sold_items` — per query: array of `{date, price, sold, shipping, title, condition, url}` sorted newest first (full per-listing detail for drilldown table)

**Dashboard (GitHub Pages):**
URL: `pacificcards.github.io/ebay-tools/market/`
- Favicon: Pacific Cards Co. circle logo (`docs/market/favicon.png`)
- Overview table: sortable by 11 columns: Product, Type, Listings, Median Price, vs Yesterday, New Today, New Median, Sold Yesterday, Sold Median, Sold (30d), Comps Median
- "Sold comps as of [date]" freshness note appears above Overview table when sold data is present
- `<hr>` separator between Overview and Trend Analysis
- Product names in Overview table are clickable — scrolls to Trend Analysis and selects that query
- Overview table header sort arrows use nowrap to prevent arrow landing on its own line
- Trend charts (supply + "Active vs. Sold Prices", 90 days); query selector drives all below sections
- **Price chart** is a mixed Chart.js chart with 4 datasets:
  - Green filled line: active listing median (BIN snapshots)
  - Purple scatter dots: individual sold listings (from `sold_trends`)
  - Dashed purple line: daily sold median (connects dots)
  - Light purple bars on right/secondary y-axis (`y1`): daily sold unit count
  - Amber dashed line: MSRP (when set)
- Price Distribution — BIN Listings histogram ($5 fixed bins)
- Sold Comps (collapsible, default collapsed) — per-listing sold detail table: Date, Title (eBay link), Condition, Sale, Shipping, Total; from `sold_items`
- Sold Yesterday (collapsible, titles link to eBay listing)
- Active BIN Listings (collapsible, sortable by title or price)
- Active Auctions (collapsible, sortable by current bid or time remaining; default: ending soonest)
Reads `docs/market/market_data.json` at page load (~1.1MB; Chart.js 4.4.3, no backend).

**Key files:**
- `market_monitor/sheets.py` — `load_queries()`; reads CategoryID (not Category) and Type
- `market_monitor/fetch_market.py` — main daily fetcher; `_LOT_RE` lot filter
- `market_monitor/fetch_sold.py` — weekly sold comps fetcher; calls sold-comps.com `/v1/scrape`, applies `_LOT_RE` filter, upserts `market_sold_items`
- `market_monitor/generate_dashboard.py` — writes `market_data.json`; sold_comps query wrapped in try/except (graceful if table missing)
- `listener/ebay.py` — `search_all_listings()` (paginated, BIN+auction)
- `docs/market/index.html` — static dashboard HTML
- `docs/market/market_data.json` — regenerated daily by pipeline

**Sold comps integration (sold-comps.com):**
- API: `GET https://api.sold-comps.com/v1/scrape`, Bearer auth (`SOLD_COMPS_API_KEY` secret)
- Keyword uses `q["query"]` (includes eBay `-exclusion` syntax which the API passes verbatim to eBay's engine — confirmed working)
- `total_price` (sold + shipping) used for dashboard medians; `sold_price` and `shipping_price` also stored in DB for reference
- `ended_at` stored as TIMESTAMPTZ, converted to PT in Python before insert
- Budget: 16 queries × N on-demand runs/month (limit: 100/month); was weekly (4/mo); now on-demand
- Workflow: `market-sold.yml` — **manual only** via "↻ Refresh Comps" button on dashboard (weekly cron removed 2026-07-15); accepts `query_ids` input (comma-separated, empty = all); also runs `generate_dashboard.py` and commits updated JSON
- Concurrency group `market-data` on `market-sold.yml` prevents race with `market-monitor.yml` committing `market_data.json`
- Dashboard refresh flow: PAT stored in browser `localStorage` (not source); fine-grained PAT required with **Actions: write** on `Pacificcards/ebay-tools` only (classic PAT needs full `repo` scope — too broad for a public page)
- Dedup: `UNIQUE(query_id, item_id)` + `ON CONFLICT DO UPDATE` handles all dedup correctly; no watermark needed
- First run 2026-07-02: 1,748 sold listings upserted across 16 queries

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

### 6. Listings Publisher (`listings-publisher/` + `pokemon-publisher/`)
Publishes ungraded trading card listings to eBay from a Google Sheet + local photo folder. Run locally via a unified entry point.

**Command:**
```
.venv/bin/python publish.py
```
Asks: `Card type [sports/tcg]:` then `Photo folder path:` — routes to the correct publisher.

**Flow:** reads pending rows → matches photos by EXIF timestamp order → previews → uploads photos → creates inventory item → creates offer → checks fees → publishes → writes Listing ID + Status back to sheet.

**Mobile edit limitation** — Inventory API creates "inventory managed" listings. These cannot be edited via the eBay mobile app or Seller Hub. Trading API migration was evaluated and deferred. Current workaround: use the REPRICE feature to push price/quantity changes from the sheet.

**REPRICE feature (live):** Set Status = `REPRICE` on any published row (with a Listing ID) to push a price/quantity update on the next run. Leave the photo folder blank to do a reprice-only run. On success, Status resets to `published` and the resolved SKU is written back to the SKU column (speeds up future reprice runs). Rows with blank SKU (legacy) trigger a full inventory_item scan + per-SKU offer lookup on first reprice; subsequent runs use the cached SKU (fast, 1 offer lookup).

#### Sports Cards (`listings-publisher/`)
- Sheet: `LISTINGS_SHEET_ID` — "Listings" tab, 25 columns:
  `Title | SKU | Sport | Player/Athlete | Manufacturer | Set | Year | Team | Card Number | Features | Parallel/Variety | Condition | Price | Quantity | Description | Shipping Policy ID | Return Policy ID | Payment Policy ID | Weight (oz) | Length (in) | Width (in) | Height (in) | Best Offer | Listing ID (24) | Status (25)`
- Category 261328; pending = has Player/Athlete, no Listing ID
- Condition descriptor IDs: NM=400010, Excellent=400011, VG=400012, Poor=400013
- League aspect auto-populated: Baseball→MLB, Football→NFL, Basketball→NBA
- Parallel/Variety aspect from sheet column

#### Pokemon/TCG (`pokemon-publisher/`)
- Sheet: `POKEMON_SHEET_ID` — "pokemon_draft" tab, 23 columns:
  `Game | Language | Title | Price | Character | Card Number | Rarity | Finish | Set | Condition | SKU | Quantity | Description | Shipping Policy ID | Return Policy ID | Payment Policy ID | Weight (oz) | Length (in) | Width (in) | Height (in) | Best Offer | Listing ID (22) | Status (23)`
- Category 183454; pending = has Character, no Listing ID
- TCG condition terminology: NM=400010, Lightly Played=400015, Moderately Played=400016, Heavily Played=400017
- Aspects: Game, Language, Character Family, Card Number, Rarity, Finish, Set, Graded=No

#### Shared facts (both publishers)
- `condition` field: always `USED_VERY_GOOD` (eBay enum = ID 4000 = "Ungraded"); conditionDescriptor `40001` REQUIRED
- `condition_map` parameter on `create_and_publish()` allows per-publisher terminology
- Merchant location `PCC-MAIN` (Santa Monica, CA) — ENABLED; `EBAY_MERCHANT_LOCATION_KEY` in `.env`
- Photos matched by EXIF timestamp (shoot order = row order); N photos per listing asked at runtime
- Title ≤ 80 chars; auto-generated if blank; no subtitle ever
- Fulfillment policy per-row; return/payment fall back to `.env` defaults
- Fee check: `ListingFee` + `InsertionFee` silently ignored (store subscription covers them); other fees prompt for confirmation
- `write_result(sheet_id, creds_path, row_idx, listing_id, status, sku="")` uses `batch_update` (1 API call); writes SKU column when `sku` is provided (`COL_SKU=2` sports, `COL_SKU=11` Pokemon)
- Error rows: clear Status to retry; put any value in Listing ID to permanently skip
- Custom SKU: fill SKU column to override auto-generated `PCC-{row}-{slug}`
- All 129 sports + 74 Pokemon published rows had blank SKU at time of REPRICE feature launch — first reprice of any legacy row uses `build_listing_offer_map()` (scans all inventory items + offer lookups), then caches SKU in sheet

**Key files:**
- `publish.py` — unified entry point (routes sports/tcg, asks photo path; photo folder optional for reprice-only runs)
- `listings-publisher/ebay_api.py` — shared API layer; `create_and_publish()`, `get_offer_for_sku()`, `build_listing_offer_map()`, `bulk_update_price_quantity()`
- `listings-publisher/images.py` — shared EXIF sort + eBay Media API upload
- `listing-publishers-summary.txt` — full technical summary for external review

**`EBAY_REFRESH_TOKEN_WRITE`** scope: `sell.inventory` only — does NOT cover `sell.account`.

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
`EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `EBAY_REFRESH_TOKEN`, `SUPABASE_DB_URL`, `GOOGLE_SHEETS_CREDENTIALS`, `LISTENER_SHEET_ID`, `PL_SHEETS_DOC_ID`, `MARKET_MONITOR_SHEET_ID`, `DISCORD_WEBHOOK_URL`, `DISCORD_BOT_TOKEN`, `DISCORD_WATCHLIST_CHANNEL_ID`, `ANTHROPIC_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `SOLD_COMPS_API_KEY`

## Constraints
- Do NOT fetch eBay developer docs from the web — user downloads PDFs and places in `/Users/eastcoastlimited/ClaudeCode/ebay_dev_docs/`
- **Schema change gotcha (P&L):** when adding/removing columns that shift the `group` column index, BOTH `_read_*_groups()` AND the preservation block inside `write_*_tab()` must be updated — they are separate and both read from the sheet. Add backwards-compat for the old schema so the first sync after a change doesn't corrupt group assignments.
- **gspread 6.x:** `ws.update()` takes `(values, range_name)` — values first. Always pass explicit `'A1'` as range_name to avoid ambiguity. `ws.clear()` may not clear beyond gspread's tracked column range; user-added columns in the sheet survive a clear.
- **eBay Finances API delay:** shipping label transactions can appear hours after purchase. If a label is missing, it may just not have been reported yet — trigger a manual `fetch_finances` re-run before assuming it was purchased externally.
- eBay refresh token valid ~Nov 2027. Re-gen: `/Users/eastcoastlimited/ClaudeCode/ebay_campaign_scheduler/get_refresh_token.py`
- Legacy scheduler repo at `/Users/eastcoastlimited/ClaudeCode/ebay_campaign_scheduler` — do not touch unless asked
- cron-job.org API key is in `.claude/settings.local.json` (not in repo)
- **Supabase schema changes must be pushed atomically with code:** the daily GHA jobs run whatever is on `main`, not local state. A local-only DB migration (e.g. a column rename) will crash the live job the next time it runs until the matching code is pushed. Always commit+push schema-dependent code changes in the same sitting as the migration, staging only the affected files by name (never `git add -A`, since this repo commonly has unrelated WIP sitting uncommitted).
- **`listing_metrics_raw` / `listing_metrics_computed` CTR columns:** `ctr_ebay_search_page` (renamed from `ctr` 2026-07-29) is eBay's own `CLICK_THROUGH_RATE` metric — search-results-page views ÷ search-results-page impressions only. `ctr_calculated` (new column) is `views_total / impressions_search_and_store` — broader, all-sources-except-promoted CTR. They will not match; this is expected, not a bug.
- **`listing_metrics_raw` impressions columns (renamed 2026-07-31 to stop two different columns both being called "total"):** `impressions_search_and_store` (was `impressions_total`) = eBay's `LISTING_IMPRESSION_TOTAL` = search-results-page + store impressions ONLY, despite the misleading eBay metric name. `impressions_all_sources` (new) = eBay's `TOTAL_IMPRESSION_TOTAL` = every placement (search, store, promoted, off-eBay, etc.) — this is the one that matches the Seller Hub traffic page, confirmed empirically 2026-07-29 to run 4-9x higher than `impressions_search_and_store`. Both are now fetched and stored side by side; `ctr_calculated`/`units_per_1k_impr`/the daily email all still key off `impressions_search_and_store` (unchanged behavior) — nothing downstream has been switched to the broader number yet, that's still an open decision (see Traffic Analytics TODOs).
- **eBay Analytics API (`traffic_report`) rate limit:** hit a persistent 429 after heavy ad-hoc diagnostic pulls (paginated, uncached listing_ids-less calls) on 2026-07-29; did not clear after a 45-min cooldown, only cleared the next day — behaves like a daily quota, not a short burst throttle. When pulling ad-hoc data for a small number of listings, always use the `listing_ids:{id1|id2}` filter (single lightweight call, no pagination) rather than unfiltered `dimension=LISTING` calls that paginate through the full catalog.

## Open TODOs

### Listener
- Raw cards only — watchlist assumes every card is raw/ungraded (2026-07-22, see Session Log). Graded-card watching not yet supported.
- The 3 hardcoded category IDs in `_RAW_CARD_CATEGORY_IDS` were supplied by the user, not verified live against eBay's API from this environment — confirm the `Graded` aspect actually resolves correctly for all 3 on the first live run.

### Campaign Scheduler
- No open items — campaigns.json migration complete and tested 2026-06-15

### P&L
1. **Listing-level hierarchy refactor** — Group > Listing > Order; new `listing_groups` table; design complete, not yet built (see full spec in P&L section above)
2. Handle refunds — refunded orders show as positive revenue in Sales tab
3. **Delete workflow** — fully operational as of 2026-06-27: record_id stamped on New Entries insert, backfill complete, fuzzy "mark"+"delet" trigger wired up, tested with Yamamoto Grading (id 337 hard-deleted, purchase count 303 → 302)

### Traffic Analytics
1. **`TOTAL_IMPRESSION_TOTAL` now fetched and stored (2026-07-31)** as `impressions_all_sources`, alongside the renamed `impressions_search_and_store` (was `impressions_total`). Still undecided/not yet implemented: whether `ctr_calculated`, `units_per_1k_impr`, or the daily email should switch to using the broader `impressions_all_sources` number instead of (or alongside) `impressions_search_and_store`. Hold off on that until the user decides — this item only covers fetching + naming.
2. **Requires a one-time Supabase migration before this lands on `main`** — see the ALTER TABLE statements in the 2026-07-31 Session Log entry. `fetch_analytics.py`/`compute_metrics.py` on this branch write to the new/renamed columns; the live DB must have them before the daily pipeline (or any push-triggered `analytics-ingest.yml` run) touches `listing_metrics_raw`/`listing_metrics_computed`, or it will crash like the 2026-07-29→30 outage.
3. Pipeline outage 2026-07-29→30 (unpushed `ctr` column rename crashed the daily job) is resolved and pushed — no action needed, documented in Constraints/Session Log as a process gotcha to avoid repeating.

### Market Monitor (live and running)
Fully operational. Daily pipeline runs at 11:00 UTC (4am PDT) via `market-monitor.yml`. Sold comps now fetched **on-demand** via "↻ Refresh Comps" button on the dashboard (weekly cron deprecated). Dashboard at `pacificcards.github.io/ebay-tools/market/`. 17 active queries as of 2026-07-07.

**No pending setup actions.**

### Listings Publisher (sports + Pokemon live; REPRICE feature live)
- Sports: 130+ listings published, fully operational
- Pokemon: 74 listings published, fully operational
- REPRICE: set Status=`REPRICE` on published rows to push price/quantity updates via `bulkUpdatePriceQuantity`; tested and working
- Mobile edit limitation remains (Inventory API listings can't be edited via mobile app) — Trading API migration deferred

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

### 2026-07-31 — Fetch `TOTAL_IMPRESSION_TOTAL`, rename the two "total impressions" columns
- Traffic Analytics: added `TOTAL_IMPRESSION_TOTAL` to the metrics pulled in `fetch_analytics.py` — this is the eBay metric confirmed 2026-07-29 to match the Seller Hub traffic page (4-9x higher than what was previously stored)
- Traffic Analytics: renamed `listing_metrics_raw.impressions_total` → `impressions_search_and_store` (still eBay's `LISTING_IMPRESSION_TOTAL` — search-results-page + store only; renamed because calling it "total" next to the new, actually-comprehensive column was confusing)
- Traffic Analytics: added `listing_metrics_raw.impressions_all_sources` (eBay's `TOTAL_IMPRESSION_TOTAL` — every placement, matches Seller Hub)
- Traffic Analytics: updated `compute_metrics.py` (`ctr_calculated`, `units_per_1k_impr`) and `send_daily_report.py` to reference `impressions_search_and_store` — same underlying numbers as before, rename only; nothing was switched to use `impressions_all_sources` yet, that's a separate open decision (see TODOs)
- Traffic Analytics: updated `dashboard/app.py` (legacy/unused) and `db/schema.sql` to match, same as the prior CTR rename's precedent
- **Migration required before this branch's code touches the live DB** (not yet run — no DB write access from the dev session this was written in):
  ```sql
  ALTER TABLE listing_metrics_raw RENAME COLUMN impressions_total TO impressions_search_and_store;
  ALTER TABLE listing_metrics_raw ADD COLUMN impressions_all_sources INTEGER;
  COMMENT ON COLUMN listing_metrics_raw.impressions_search_and_store IS 'eBay LISTING_IMPRESSION_TOTAL: search-results-page + store impressions ONLY, despite the misleading eBay metric name';
  COMMENT ON COLUMN listing_metrics_raw.impressions_all_sources IS 'eBay TOTAL_IMPRESSION_TOTAL: all placements (search, store, promoted, off-eBay, etc.) — matches Seller Hub traffic page';
  ```
  Run this against Supabase (SQL editor or psql) before merging to `main` / before this branch's push-triggered `analytics-ingest.yml` run touches `listing_metrics_raw` — otherwise it's the same `UndefinedColumn` crash class as the 2026-07-29→30 outage.

### 2026-07-29 to 2026-07-30 — CTR/impressions investigation, pipeline outage, and recovery
- Traffic Analytics: user working through an external assignment needing Impressions/Clicks/CTR for 3 specific listings (287468232189, 287482256093, 287461336640) — not part of `report_listings.json`, ad-hoc pulls only
- Traffic Analytics: diagnosed why stored `ctr` looked inconsistent with impressions/views totals — it's eBay's own `CLICK_THROUGH_RATE` metric, scoped to search-results-page traffic only, not derived from `impressions_total`/`views_total`. Confirmed by reproducing it exactly from `views_search / impressions_search`.
- Traffic Analytics: renamed `listing_metrics_raw.ctr` and `listing_metrics_computed.ctr` → `ctr_ebay_search_page`; added new `listing_metrics_computed.ctr_calculated` (`views_total/impressions_total`); added `COMMENT ON COLUMN` documentation in Postgres; updated `fetch_analytics.py`, `compute_metrics.py`, `dashboard/app.py` (legacy/unused) to match; backfilled all 8,539 historical computed rows
- Traffic Analytics: separately diagnosed why our impressions numbers don't match the user's eBay Seller Hub traffic page — `LISTING_IMPRESSION_TOTAL` (what we store) explicitly excludes non-search/non-store impressions per eBay's own docs; `TOTAL_IMPRESSION_TOTAL` is the one that matches Seller Hub, confirmed 4-9x higher live. Not yet added to the pipeline — user wants to decide the approach later (see Traffic Analytics TODOs)
- **Incident:** the `ctr` rename above was made locally but not pushed; the deployed `analytics-ingest.yml` job crashed the next two mornings (`UndefinedColumn: ctr does not exist`), which cascaded into `pl-ingest.yml` being skipped (shows as "skipped" not "failed" — easy to miss). Diagnosed via a background subagent, backfilled `orders_raw`/`order_fees`/P&L sheet manually, then the fix was committed (isolated to the 2 affected files, not `git add -A` — repo has ongoing unrelated WIP) and pushed. Added a Constraints-section gotcha so this class of failure doesn't recur.
- Traffic Analytics: hit a sustained 429 on eBay's `traffic_report` API from heavy unfiltered diagnostic pulls; behaved like a daily quota (didn't clear after 45 min, cleared overnight). Switched all further ad-hoc pulls to the `listing_ids:{...}` filter (single targeted call per date, no pagination) — worked cleanly with zero further rate-limit errors.

### 2026-07-30 — Pokemon publisher aspect fix
- Fixed `_build_aspects` in `pokemon-publisher/publish.py`: `Character` column was mapped to eBay aspect `"Character Family"` — corrected to `"Character"`

### 2026-07-27 — REPRICE feature (sheet-driven price/quantity updates)
- Added REPRICE path to both publishers: set Status=`REPRICE` on a published row to push price/quantity update via `bulkUpdatePriceQuantity` API
- New functions in `ebay_api.py`: `get_offer_for_sku()` (fast path, SKU known), `build_listing_offer_map()` (legacy path, scans all inventory items + offer lookups, stops early once all requested listing IDs found), `bulk_update_price_quantity()` (up to 25 per call)
- `read_reprice()` added to both `sheets.py` — returns `(reprice_rows, ambiguous_rows)`; ambiguous = has Listing ID with unexpected Status (not blank/published/REPRICE/error:*)
- `write_result()` updated in both sheets.py: added optional `sku=""` param; writes SKU column when provided (`COL_SKU=2` sports, `COL_SKU=11` Pokemon); going forward SKU stored in sheet at publish time and on first reprice
- Both `publish.py` restructured: reads pending + reprice rows together; photo folder now optional (skips new-listing path if blank); single confirm covers both new listings and reprice in same run; progress display during legacy offer map build
- Root `publish.py`: photo folder prompt updated to indicate it's optional for reprice-only runs
- Key discovery: `GET /sell/inventory/v1/offer` requires `sku` param (returns 400 error 25707 without it) — can't list all offers. `GET /sell/inventory/v1/inventory_item` works without params and returns all SKUs
- All 129 sports + 74 Pokemon legacy rows have blank SKU (never written at publish time) — first reprice triggers full scan; SKU cached in sheet for fast future reprices
- Trading API migration was evaluated (full plan written) but user chose additive reprice approach instead; plan abandoned

### 2026-07-27 — Pokemon Publisher + unified entry point
- Built `pokemon-publisher/` — category 183454, TCG condition terminology (Lightly Played/Moderately Played/Heavily Played → 400015/400016/400017), aspects: Game, Language, Character Family, Card Number, Rarity, Finish, Set, Graded=No
- Added `condition_map` param to `ebay_api.create_and_publish()` (backward compatible) so both publishers share one API layer with different condition terminology
- Created `publish.py` at repo root — unified entry point; asks `Card type [sports/tcg]:` and `Photo folder path:`, routes to correct publisher via `importlib`
- Pokemon sheet: `POKEMON_SHEET_ID` in `.env`, tab `pokemon_draft`, 23 columns; `COL_LISTING_ID=22`, `COL_STATUS=23`
- SKU column added to Pokemon template (between Condition and Quantity); custom SKU used if present, auto-generated otherwise
- All same validations confirmed present in both publishers (photo count, title length, required fields, fee check, shipping policy, proceed confirmation)
- Created `listing-publishers-summary.txt` — full technical summary for external LLM review
- **Discovered: Inventory API listings cannot be edited via eBay mobile app** — "inventory managed" state. Decision pending on whether to switch to Trading API.

### 2026-07-25 — Listings Publisher: condition fix + new aspects + fee check
- Fixed core condition bug: category 261328 only accepts conditionId 4000 (Ungraded). `USED_VERY_GOOD` is the ConditionEnum that maps to ID 4000 — used as a constant for ALL ungraded cards. conditionDescriptor `40001` (REQUIRED) carries the actual grade. Confirmed via `GET /sell/metadata/v1/marketplace/EBAY_US/get_item_condition_policies`.
- Added `Parallel/Variety` column to sheet template (col 11, between Features and Condition) → `COL_LISTING_ID` 23→24, `COL_STATUS` 24→25; mapped to `Parallel/Variety` aspect
- Added `League` aspect auto-populated from Sport: Baseball→"Major League Baseball (MLB)", Football→"National Football League (NFL)", Basketball→"National Basketball Association (NBA)"
- Added fee check via `get_listing_fees` API before publish; `ListingFee` + `InsertionFee` silently ignored (confirmed via 500+ finance transactions: zero such charges ever billed — store subscription covers them); any other non-zero fee shows a confirmation prompt
- Fixed Google Sheets 429 rate limit: `write_result` changed from 2× `update_cell` to 1× `batch_update`
- Removed debug print from `ebay_api.py`
- **130+ listings successfully published**

**Ruled out (condition field):**
- `"4000"` string → error 2004 "Could not serialize field [condition]" — not a valid ConditionEnum
- `USED_EXCELLENT` (ID 3000) → error 25059 "condition 3000 not valid for category 261328"
- Per-grade ConditionEnum mapping (USED_EXCELLENT for NM, USED_GOOD for VG, etc.) — wrong; only ID 4000 is valid for ungraded in this category

### 2026-07-22 — eBay purchases import: weekly → daily
- P&L: found root cause of "purchases not updating daily" complaint — `fetch_ebay_purchases.py` (populates `import_queue`, source of the Purchases tab) ran on its own separate `ebay-purchases.yml` workflow scheduled **weekly (Mondays only)**, not as part of the daily `analytics-ingest.yml` pipeline that Sales/Fees use. P&L sync itself is daily, but had nothing new to sync between Mondays.
- P&L: changed `ebay-purchases.yml` cron from `0 10 * * 1` (Mondays) to `0 10 * * *` (daily), same 10:00 UTC / 3am PT slot as the analytics pipeline; workflow renamed "eBay purchase import (daily)"
- P&L: 14-day lookback window in `fetch_ebay_purchases.py` left unchanged — safe for daily runs since inserts dedup via `ON CONFLICT DO NOTHING` on `(ebay_item_id, transaction_id)`
- P&L: documented actual eBay-purchases fetch cadence in CLAUDE.md (previously undocumented / implied daily incorrectly)
- P&L: triggered a manual `workflow_dispatch` run to backfill the missing 7/21 purchase immediately

### 2026-07-15 to 2026-07-17 — Market Monitor on-demand sold comps refresh

**On-demand sold comps refresh (completed):**
- Deprecated weekly Sunday cron in `market-sold.yml` (removed `schedule:` block entirely)
- Added `workflow_dispatch.inputs.query_ids` (comma-separated query IDs, empty = all) and `concurrency: group: market-data`
- `fetch_sold.py`: reads `FETCH_QUERY_IDS` env var; filters queries list when set
- Dashboard: "↻ Refresh Comps" button in top-right header → modal with per-query checkboxes (All/None toggles), fine-grained PAT input (saved to `localStorage`), 5-step progress UI (Dispatch → Fetch → Update → Save → Refresh page); polls `market_data.json?t=<cachebust>` until `generated_at` changes, then auto-re-renders entire dashboard in place
- Fine-grained PAT requires **Actions: write** on `Pacificcards/ebay-tools` only — do NOT use a classic PAT (requires full `repo` scope, too broad for a public GitHub Pages site)
- Dedup already handled by `UNIQUE(query_id, item_id)` + `ON CONFLICT DO UPDATE`; no MAX(ended_at) watermark needed

### 2026-07-07 to 2026-07-15 — P&L restructure + Market Monitor sold comps visualization

**P&L restructure (completed):**
- Sales tab: added `listing_id` column between `ebay_order_id` and `group` — now 10 cols; `group` shifted from col H → col I
- Ad Fees tab: added `category` column (col I, auto-computed, not editable): NON_SALE_CHARGE→"Ad Fee", SHIPPING_LABEL/SHIPPING_MANUAL→"Shipping", else→"Other"
- Ad Fees group auto-assignment via SQL COALESCE: SHIPPING_LABEL rows use matching order via `order_id`; NON_SALE_CHARGE rows use matching order via `listing_id`; persisted to DB on next sync
- P&L by Group restructured from 5→6 cols: `group | net_payout | costs | ad_fees | shipping_cost | profit`; SUMIFS for ad_fees/shipping_cost use BYROW+LAMBDA (ARRAYFORMULA+SUMIFS only produces one row on array criteria)
- New Entries: added `type = shipping` → inserts into `order_fees` as SHIPPING_MANUAL (DEBIT); record_id stamped as `SHIP-{hex16}`; deletion via "mark"+"delet" status removes from `order_fees`
- 30 tests passing

**Market Monitor sold comps chart (completed):**
- Price chart redesigned as mixed chart: green filled line (active median) + purple scatter dots (individual sales from `sold_trends`) + dashed purple line (daily sold median) + light purple bars on secondary right y-axis (daily sold count)
- Chart title: "Median Price ($)" → "Active vs. Sold Prices ($)"
- Drilldown: collapsible Sold Comps table (below Price Distribution); shows Date, Title (eBay link), Condition, Sale, Shipping, Total for each sold listing in last 30 days
- Overview QOL: product names clickable → jumps to Trend Analysis with query selected; sort arrows no longer wrap to own line (last word + arrow in nowrap span)
- `generate_dashboard.py`: added `sold_trends` (daily medians + price arrays) and `sold_items` (full per-listing detail) to market_data.json; total JSON size ~1.1MB
- Design decision: used daily time-series (not flat 30-day reference line) because density check showed 1–20 sales/day per query — sufficient for a meaningful trend

### 2026-07-02 — Sold comps integration (sold-comps.com → Market Monitor)
- Built `market_monitor/fetch_sold.py` — weekly fetcher calling sold-comps.com `/v1/scrape`; uses `q["query"]` (eBay exclusion syntax passed verbatim by API); applies `_LOT_RE` client-side; upserts into `market_sold_items`
- `market_monitor/generate_dashboard.py` — added 30-day sold comps query (total_price percentiles); wrapped in try/except so daily dashboard generation doesn't break if table is absent
- `docs/market/index.html` — Overview table expanded to 11 columns: added "Sold (30d)" (true sold count) and "Comps Median" (median total_price); "sold comps as of [date]" freshness note
- `.github/workflows/market-sold.yml` — new weekly workflow; runs fetch_sold then generate_dashboard then commits JSON
- `market_sold_items` table created in Supabase; `SOLD_COMPS_API_KEY` secret added to Pacificcards org
- First run 2026-07-02: 1,748 sold listings upserted; 0 listings for Topps Tier One (new/unreleased product, expected)
- Bug fixes shipped in same session (from prior summary): UTC→PT date fix in `fetch_orders.py` and `send_daily_report.py`; `_parse_date()` graceful handling in `sheets.py`; `new_count` re-run immunity via `first_seen` in `generate_dashboard.py`; `batch_update` fix for P&L 429 rate limit

### 2026-06-27 (session 3) — Market Monitor dashboard + pipeline hardening
- Dashboard: Pacific Cards Co. favicon added (`docs/market/favicon.png`)
- Dashboard: Overview table fully sortable (all 9 columns incl. new Type column); collapsible Sold Yesterday / Active BIN / Active Auctions sections
- Dashboard: Sold Yesterday titles are now clickable eBay links
- Dashboard: Active Auctions sortable by current bid or time remaining; null end_time respects sort direction
- Dashboard: histogram fixed $5 bins with edge-case guard (all prices identical)
- Dashboard: `sortArrow()` extracted to module-level (was duplicated in 3 places)
- Schema: added `presale_date DATE`, `release_date DATE`, `type TEXT` to `market_snapshots`; `url` field now included in `gone_items` JSON
- Pipeline: replaced `CATEGORY_ID_MAP` (plain-English name resolution) with `CategoryID` column in sheet (numeric, user-managed VLOOKUP formula); `Category` column still exists in sheet but is not read by code
- Pipeline: added `Type`, `Presale Date`, `Release Date` columns to sheet + DB + JSON
- Pipeline: `_LOT_RE` extended with `\bbooster\s+box\s+case\b` and `\((?:[2-9]|\d{2,})\)` (fixed: was `\([2-9]\d*\)` which missed (10)–(19))
- Pipeline: `_upsert_snapshot` param `type` renamed to `query_type` to avoid shadowing builtin
- Pipeline: `generate_dashboard.py` metadata query changed from `MAX(type/msrp/dates)` to `DISTINCT ON (query_id) ORDER BY date DESC` — always returns most recent values
- Pipeline: GHA schedule migrated from cron-job.org to native `schedule: cron: '0 11 * * *'`

### 2026-06-27 (session 2) — Market Monitor dashboard visualization
- Dashboard: replaced "New Listings Today" table with full sortable BIN listings table (sort by title or price, default price asc)
- Dashboard: added Active Auctions table (title, current bid, time remaining as of data pull)
- Dashboard: moved "Sold Yesterday" above BIN listings table
- Dashboard: added `<hr>` separator between Overview table (all queries) and Trend Analysis section (query-specific)
- Dashboard: overview table — removed listing count "vs Yesterday %" (redundant with New/Sold counts); added "New Median" (median price of new BIN listings today) and "Sold Median" (median price of gone BIN listings) columns
- Dashboard: retitled price histogram to "Price Distribution — BIN Listings"
- Backend: `listener/ebay.py` — added `end_time` field from `itemEndDate` in `_parse_items()`
- Backend: `fetch_market.py` — added `end_time` to `market_snapshot_items` upsert
- Backend: `generate_dashboard.py` — added `bin_listings`, `auction_listings`, `fetched_at`, `new_medians`, `gone_medians` to JSON output; removed `new_items`; `prices` dict now derived from `bin_listings`
- DB: added `end_time TIMESTAMPTZ NULL` column to `market_snapshot_items` via `ADD COLUMN IF NOT EXISTS`
- Regenerated `market_data.json` locally (731 BIN + 835 auction listings across 7 queries) and pushed so dashboard shows data immediately without waiting for pipeline

### 2026-06-27 — P&L delete workflow
- P&L: added `record_id` column (col 9) to New Entries tab; stamped on every insert — `MANUAL-{hex}` for sales, numeric `import_queue.id` for purchases
- P&L: backfilled record_ids for all previously-synced rows using batch gspread `ws.batch_update()` (per-cell `update_cell()` loop was hitting 429 quota)
- P&L: deletion trigger: any status containing "mark" + "delet" (case-insensitive, handles "Mark For Deletion", "Marked for Deletion", etc.) → hard DELETE from DB + stamp "Deleted {date}"
  - Sales: `DELETE FROM orders_raw WHERE order_id = %s AND order_id LIKE 'MANUAL-%%'`
  - Purchases: `DELETE FROM import_queue WHERE id = %s AND source = 'manual'`
  - Scope: only manual entries (record_id required; rows without one get a ✗ stamp instead)
- P&L: resolved Cooper Flagg PP ambiguity — 3 distinct purchases ($5.71, $7.36, $7.89); backfill loose-matched two rows to the same DB record; manually inserted ids 347 ($5.71) and 348 ($7.36), updated sheet
- P&L: re-synced Gemini Plan rows (rows 137/138/139) — were future-dated and mis-stamped "✓ Synced"; user updated dates, cleared status, re-synced as ids 349, 350, 351
- P&L: deleted "Yamamoto Grading" (row 218, record_id=337) — confirmed hard-deleted; purchase count went from 303 → 302
- P&L: 28 tests passing (6 new tests in `TestNewEntriesRecordId` class)
- P&L: `_ensure_new_entries_tab` updated to append missing header columns to existing tab (previously only added headers on new tab creation)

### 2026-06-26 (session 3) — Market Monitor bugs
- Market Monitor: fixed 3 dashboard bugs reported by user:
  1. **Price histogram was including auction prices** — `generate_dashboard.py` prices query now has `AND buying_format = 'FIXED_PRICE'`; auction bids (including $0.99 case break spots) no longer appear in the distribution
  2. **UTC/PT date mismatch** — `generate_dashboard.py` replaced `date.today()` with `max(date) from market_snapshots`; pipeline runs at 10:30 UTC (still previous calendar day in PT), so local clock was targeting the wrong date and returning nearly empty item/price data
  3. **Deactivated queries showing in dashboard** — queries list now filtered to only query_ids present in the most recent snapshot date; historical rows for deactivated queries no longer appear
- Market Monitor: replaced eBay Taxonomy API name→ID lookup with hardcoded `CATEGORY_ID_MAP` in `sheets.py`; Taxonomy API was returning wrong IDs (e.g. 183438 = "Card Toploaders" instead of sealed boxes). Sheet Category column now accepts plain-English names from the map, or numeric IDs directly.
- Market Monitor: confirmed that adding category 261332 ("Sealed Trading Card Boxes") to Inception row reduced results from 245 → 144; the 101 excluded items were case break spot auctions (TEAM BREAK / $1 Auctions)
- All changes committed, pushed, and validated via manual GHA run

### 2026-06-26 (session 2)
- P&L: ran full code review (8 findings); fixed 5 of 8 in previous session + this session
- P&L: `save_sale_groups`, `save_purchase_groups`, `save_ad_fee_groups` now use `execute_values` bulk UPDATE instead of per-row loops — 3 SQL statements instead of ~2,000 per sync
- P&L: `fetch_sales`, `fetch_purchases`, `fetch_ad_fees`, and all 3 save functions now share one DB connection per sync run (was 6 separate opens/closes)
- P&L: `fetch_sales(conn)`, `fetch_purchases(conn)`, `fetch_ad_fees(conn)` take explicit `conn` param — tests updated to pass mock conn directly (no more `patch.object` needed)
- Market Monitor: `_count_new()` deleted — replaced with `len(today_ids - yesterday_ids)` from sets already in memory; note: counts "new since yesterday" not "first ever seen" (minor semantic difference, acceptable for supply monitoring)
- Market Monitor: `conn = get_connection()` moved before the query loop — 1 connection for all queries instead of N
- Market Monitor: `print(f"→ new=...")` moved inside try block — was outside, would `UnboundLocalError` on DB failure
- All 22 tests pass after updates
- Fixed eBay purchases workflow — module path was `analytics.fetch_ebay_purchases` (module doesn't exist); corrected to `traffic_analytics.fetch_ebay_purchases`; 8 purchases captured on first successful run
- Fixed group corruption on June 22–24 orders — corrupted `group_name` values matched `SPLIT_PART(order_id, '_', 1)`; cleared 7 rows and re-synced
- Unfixed review findings (deferred): #3 `_count_new` already removed; #4 was the per-query connection (now fixed); remaining unfixed: none from original 8 except the listing-level refactor which is tracked separately

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
