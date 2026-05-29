# listener

Monitors eBay for underpriced sports cards and Pokémon cards against a manual price guide. Runs every 15 minutes via GitHub Actions (6am–10pm Pacific), writes qualifying finds to Google Sheets, and fires a Discord alert on each new discovery.

Uses the eBay Browse API with application-only auth — no user OAuth required.

---

## How it works

1. Reads a **Watchlist** tab in Google Sheets (cards + target prices)
2. For each active row, resolves the eBay Product ID (EPID) if not already stored
3. Searches Browse API for listings at or below the target price
4. Deduplicates against `listener_seen_items` in Supabase
5. Appends new finds to an **Observed Listings** tab and sends a Discord alert

---

## Google Sheets setup

Create a Google Sheet with two tabs:

**Tab: `Watchlist`** — one row per card to monitor

| Column | Example |
|--------|---------|
| Description | Luka Doncic 2018-19 Prizm Silver PSA 10 |
| Category | sports |
| Max Price ($) | 150 |
| Min Price ($) | 50 (optional — listings below this are ignored) |
| Hint URL(s) | https://www.ebay.com/itm/... (any existing listing for this card) |
| EPID | *(auto-filled by the tool)* |
| EPID Status | *(auto-filled: resolved / needs_review / not_found)* |
| Active (Y/N) | Y |

> **Tip:** Providing a Hint URL (any eBay listing for the card — active or sold) is the most reliable way to get the correct EPID. If you leave it blank, the tool searches the eBay catalog by description.

**Tab: `Observed Listings`** — the tool appends here automatically

| Timestamp | Watchlist Description | Title | Price | % Below Target | Item ID | URL |

---

## EPID resolution

eBay Product IDs (EPIDs) uniquely identify a card regardless of individual listing titles. Once resolved, the EPID is written back to the Watchlist tab and reused on every future run — no re-lookup needed.

If EPID Status shows `needs_review`, the tool found multiple catalog matches and took the top result. Check the EPID manually and correct if needed.

If EPID Status shows `not_found`, add a Hint URL to that row and clear the status field — the tool will retry on the next run.

---

## Supabase setup

Run `db/schema_listener.sql` against your Supabase database to create the `listener_seen_items` deduplication table.

---

## GitHub Secrets required

| Secret | How to get it |
|--------|--------------|
| `EBAY_CLIENT_ID` | Already set (shared with analytics pipeline) |
| `EBAY_CLIENT_SECRET` | Already set |
| `SUPABASE_DB_URL` | Already set |
| `GOOGLE_SHEETS_CREDENTIALS` | Service account JSON from Google Cloud Console → APIs & Services → Credentials |
| `GOOGLE_SHEET_ID` | The long ID in your Google Sheet's URL |
| `DISCORD_WEBHOOK_URL` | Discord server settings → Integrations → Webhooks → New Webhook |

For `GOOGLE_SHEETS_CREDENTIALS`: create a service account, download the JSON key, and share the Google Sheet with the service account's email address.

---

## Local testing

```bash
export EBAY_CLIENT_ID=...
export EBAY_CLIENT_SECRET=...
export SUPABASE_DB_URL=...
export GOOGLE_SHEETS_CREDENTIALS='{"type":"service_account",...}'
export GOOGLE_SHEET_ID=...
export DISCORD_WEBHOOK_URL=...
python -m listener.main
```
