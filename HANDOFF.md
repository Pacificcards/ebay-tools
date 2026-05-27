# eBay Tools — Session Handoff

---

## Listener subproject — built 2026-05-26, NOT YET DEPLOYED

### What was built
A semi-real-time underpriced card finder (`listener/`). Polls eBay Browse API every 15 min (6am–10pm Pacific), compares against a manual price guide in Google Sheets, dedupes via Supabase, appends findings to Google Sheets, and fires a Discord alert on each new find.

Supports both **sports cards and Pokémon cards**. Uses EPID (eBay Product ID) for precise card identification — resolved once via hint URL or catalog search, then stored in the sheet.

### Setup checklist

- [x] **Supabase:** `listener_seen_items` table created
- [x] **Google Sheets:** Sheet created, ID: `1T_ydHG5eWwNIiS79Fni04fy-U8fd_4d_7tbUahrMWec`, named `ebay_listener`
  - `Watchlist` tab: `Description | Category | Max Price ($) | Hint URL(s) | EPID | EPID Status | Active (Y/N)`
  - `Observed Listings` tab: `Timestamp | Watchlist Description | Title | Price | % Below Target | Item ID | URL`
- [x] **Google Cloud:** Reusing existing service account `ebay-tools-sheets@pcc-accounting.iam.gserviceaccount.com` (project: `pcc-accounting`). Credentials at `pl/credentials/service_account.json`. Sheet shared with service account.
- [x] **Discord:** Webhook created and set as GitHub secret
- [x] **GitHub Secrets:** All 3 added (`GOOGLE_SHEETS_CREDENTIALS`, `GOOGLE_SHEET_ID`, `DISCORD_WEBHOOK_URL`)
- [ ] **EPID investigation (next step — pick up here):** See notes below. Need to check eBay developer console for correct Catalog API OAuth scope.
- [ ] **Local test:** Run `python -m listener.main` end-to-end once EPID strategy is resolved
- [ ] **Push + manual trigger:** `gh workflow run listener.yml --repo Pacificcards/ebay-tools`

### EPID investigation notes (2026-05-27)
- `get_item_by_legacy_id` on listing `377213102243` (Psyduck 226/217 Ascended Heroes) → `epid: None` — card not cataloged by eBay
- Catalog API `GET /commerce/catalog/v1_beta/product_summary/search` → 403 Access Denied with scope `https://api.ebay.com/oauth/api_scope`
- Catalog API PDFs reviewed — docs don't specify the required OAuth scope
- Suspected required scope: `https://api.ebay.com/oauth/api_scope/commerce.catalog.readonly` (unconfirmed)
- Two problems to resolve before EPID via Catalog API is viable:
  1. **Scope fix:** Check eBay developer console → My Account → Application Access Keys → production app → OAuth scopes. Look for catalog scope and enable it, or download the auth/scope doc page as PDF and drop in `/Users/eastcoastlimited/ClaudeCode/ebay_dev_docs/`
  2. **Coverage check:** Even with correct scope, newer Pokémon sets may not be cataloged. Should test on a well-known card (e.g. vintage Charizard or PSA graded sports card) to gauge real coverage before committing to EPID approach
- Keyword search fallback already built in `listener/ebay.py` (`search_listings_by_keyword`) — ready to use if EPID proves unreliable

### Key files
| File | Purpose |
|------|---------|
| `listener/main.py` | Entry point |
| `listener/ebay.py` | App token + Browse/Catalog API wrappers |
| `listener/epid_resolver.py` | Resolves EPID from hint URL or catalog search |
| `listener/sheets.py` | Google Sheets read/write via gspread |
| `listener/discord.py` | Discord webhook POST |
| `db/schema_listener.sql` | Supabase dedup table |
| `.github/workflows/listener.yml` | GitHub Actions cron |
| `listener/README.md` | Full setup guide |

### No new dependencies needed
`gspread` and `google-auth` were already in `requirements.txt`.

---


**Project:** `ebay-tools` monorepo for Pacific Cards Co.
**GitHub:** https://github.com/Pacificcards/ebay-tools (private)
**Local path:** `/Users/eastcoastlimited/ClaudeCode/ebay-tools`

---

## What this project does

Daily analytics pipeline for an eBay seller:
1. `sync_listings` — fetches all active listings from Trading API, populates `listing_metadata`
2. `fetch_analytics` — pulls traffic metrics (impressions, views, CTR, orders) from eBay Analytics API, filtered to active listings only
3. `fetch_orders` — pulls order line items from Fulfillment API
4. `compute_metrics` — derives per-listing-per-day computed metrics in Supabase

Runs daily via GitHub Actions (`.github/workflows/analytics-ingest.yml`, 8am UTC).

---

## Current state (as of 2026-05-22)

### Just completed this session
- **`analytics/sync_listings.py`** — NEW. Calls `GetMyeBaySelling` (Trading API), upserts all active listings into `listing_metadata`, flips missing listings to `status = 'ended'`
- **`analytics/fetch_analytics.py`** — Updated. Now loads active listing IDs from `listing_metadata` before fetching, filters out ended listings post-fetch
- **`analytics/run_ingest.py`** — Updated. `sync_listings` now runs first in the pipeline
- **`db/schema.sql`** — Updated `listing_metadata` with new columns (see below)
- **Supabase live DB** — Migration already applied

### `listing_metadata` schema (current)
```
listing_id       TEXT PRIMARY KEY
title            TEXT
sku              TEXT
current_price    NUMERIC(10,2)
status           TEXT              -- 'active', 'active_hidden', 'ended'
hide_from_search BOOLEAN           -- from Trading API HideFromSearch
hide_reason      TEXT              -- from Trading API ReasonHideFromSearch
last_synced_at   TIMESTAMPTZ
updated_at       TIMESTAMPTZ
```
Note: `category` column was intentionally dropped — Trading API doesn't return it in `GetMyeBaySelling` (would require expensive per-listing `GetItem` calls).

---

## What needs to happen next

### 1. Test sync_listings + filtered analytics run
The code is written but hasn't been run yet against the live API. Trigger a manual test:
```bash
cd /Users/eastcoastlimited/ClaudeCode/ebay-tools
gh workflow run analytics-ingest.yml --repo Pacificcards/ebay-tools
```
Then check Supabase `listing_metadata` table to confirm listings populated with correct status.

### 2. Run full backfill from 2026-01-01
Once step 1 confirms sync_listings is working:
```bash
gh workflow run analytics-ingest.yml --repo Pacificcards/ebay-tools \
  -f backfill=1 -f backfill_start=2026-01-01
```
This will overwrite existing rows via `ON CONFLICT` — safe to re-run.

---

## Key files

| File | Purpose |
|------|---------|
| `analytics/sync_listings.py` | Trading API → listing_metadata |
| `analytics/fetch_analytics.py` | Analytics API → listing_metrics_raw |
| `analytics/fetch_orders.py` | Fulfillment API → orders_raw |
| `analytics/compute_metrics.py` | Derives listing_metrics_computed |
| `analytics/run_ingest.py` | Pipeline entry point |
| `shared/ebay_auth.py` | OAuth token exchange (all scripts) |
| `shared/db.py` | Supabase Postgres connection |
| `db/schema.sql` | All table definitions |
| `.github/workflows/analytics-ingest.yml` | Daily cron + manual backfill |

---

## GitHub Secrets (Pacificcards org)

| Secret | Notes |
|--------|-------|
| `EBAY_CLIENT_ID` | App ID from eBay developer console |
| `EBAY_CLIENT_SECRET` | Cert ID |
| `EBAY_REFRESH_TOKEN` | **Just re-generated 2026-05-22** with expanded scope including `https://api.ebay.com/oauth/api_scope` (required for Trading API) |
| `SUPABASE_DB_URL` | Session pooler URL |

---

## Important constraints

- **Do not fetch eBay developer docs from the web.** Point user to the URL; they download and share the PDF manually.
- Refresh token was regenerated this session. It's valid for 18 months (expires ~Nov 2027). File to re-run if needed: `/Users/eastcoastlimited/ClaudeCode/ebay_campaign_scheduler/get_refresh_token.py`
- The old repo at `/Users/eastcoastlimited/ClaudeCode/ebay_campaign_scheduler` is legacy — do not make changes there unless explicitly asked.
