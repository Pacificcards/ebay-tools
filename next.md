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
6. **Add listings to report config** — `traffic_analytics/report_listings.json` currently only has 1 listing. Edit on GitHub to add the listings you want in the daily email.
7. **Revenue metric in email** — `orders_raw.sale_price` is available but not in the report yet; add a Revenue row if useful. (suggested)
8. **Weekly summary email** — a separate Monday morning email aggregating the full prior week per listing, for higher-level trend review. (suggested)
