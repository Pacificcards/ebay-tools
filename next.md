# Next Steps

## Listener
1. ~~**Confirm emoji alert in production**~~ — confirmed working (shipped 2026-06-10).
2. ~~**Stale market price alert**~~ — shipped 2026-06-10; date parsing bug fixed 2026-06-15.
3. ~~**Discord watchlist ingestion**~~ — shipped 2026-06-10.
4. ~~**Listener bug fixes**~~ — shipped 2026-06-15: stale date parsing, PT timestamp (time-only), 0-feedback seller filter.

## Campaign Scheduler
5. ~~**Move campaign IDs to campaigns.json**~~ — shipped 2026-06-15. Edit `scheduler/campaigns.json` to add/remove/rename campaigns. Tested pause successfully.

## P&L
2. **Handle refunds** — 14 refunded orders ($123.27) currently show as positive revenue in the Sales tab. All have order_ids that link to REFUND rows in `order_fees`. Fix: offset or exclude these in `sync_to_sheets.py`.
3. **Categorize Ad Fees tab** — 904 rows are an unlabeled mix of postage, ad/listing fees, store subscription, refunds, and credits. Add a `category` column with auto-classification logic. (suggested)
4. **Surface postage in P&L by Group** — $1,306 in SHIPPING_LABEL spend is captured in `order_fees` but never flows into the P&L by Group costs column. (suggested)
5. **Manual entry UI** — the New Entries Google Sheet tab works but is clunky. Build a dedicated local UI (Flask, Streamlit tab, or other — TBD) that submits directly to Supabase and triggers a sheet sync. Decision between approaches still open.

## Traffic Analytics
6. ~~**Add listings to report config**~~ — done, 4 listings now configured.
7. **Revenue metric in email** — `orders_raw.sale_price` is available but not in the report yet; add a Revenue row if useful. (suggested)
8. **Weekly summary email** — a separate Monday morning email aggregating the full prior week per listing, for higher-level trend review. (suggested)

## Price Check (ready to build — plan fully designed)
9. **Build `listener/price_check.py`** — core script: reads "Price Check" tab from Listener sheet, calls Claude to simplify query, searches eBay Browse API, applies IQR + grade filter, writes Clearing/Holding prices back to sheet.
10. **Add `search_listings_for_price()` to `listener/ebay.py`** — new Browse API function without price/time filters, returns `{price, title}` list, limit 200.
11. **Add sheet helpers to `listener/sheets.py`** — `read_price_check()` and `write_price_check_row()`.
12. **Create `.github/workflows/price-check.yml`** — `workflow_dispatch` only; needs `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `LISTENER_SHEET_ID`, `GOOGLE_SHEETS_CREDENTIALS`, `ANTHROPIC_API_KEY`.
13. **User setup** — create "Price Check" tab in Listener sheet with headers: Description | Hint URL | EPID | Clearing Price | Holding Price | # Listings | Last Checked.
