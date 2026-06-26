# Next Steps

## Immediate / Unblocked
1. **Market Monitor: user setup** — create `pcc_sealed_monitor` Google Sheet with Queries tab, share with service account, add `MARKET_MONITOR_SHEET_ID` secret, enable GitHub Pages, trigger first run. All code is live; waiting on these steps before the pipeline can run.
2. **Update listener cron-job.org from every 15 min to hourly** — agreed 2026-06-21; reduces GHA spend ~75%. Job ID: 7684877. Needs cron-job.org API key or manual dashboard update.

## Market Monitor (live — pending user setup)
3. **Add Market Monitor cron-job.org trigger** — the `market-monitor.yml` workflow is set to `workflow_dispatch` only. Once running, create a cron-job.org job targeting it at 10:30 UTC daily (after analytics-ingest finishes at 10:00 UTC).
4. **Validate first run** — after user setup, confirm: items appear in `market_snapshots` and `market_snapshot_items`, `market_data.json` committed to repo, GitHub Pages dashboard loads correctly.
5. **Dashboard: "Gone Today" labeling** — consider adding a note on the dashboard clarifying "gone = disappeared from search results = likely sold or ended, not confirmed sold." (suggested)

## P&L
6. **Listing-level hierarchy refactor** — full design complete (see CLAUDE.md P&L section). Steps: (a) create `listing_groups` table in Supabase; (b) seed from `orders_raw` distinct listing_ids + titles; (c) add Listings tab to sheet for group assignment; (d) update `fetch_sales`, `fetch_ad_fees` to derive group from listing; (e) update P&L by Group formula. Decide whether to add P&L by Listing tab before starting.
7. **Fix stale tests** — `write_pl_tab` called with 3 args in tests but now takes 4 (`ad_fees_row_count`); `fetch_purchases` mock uses 6-column schema but now returns 7; Purchases batch preservation test index offsets wrong. Fix before next P&L deploy.
8. **Handle refunds** — refunded orders show as positive revenue in Sales tab. Fix: exclude/offset REFUND rows in `fetch_sales()` query.
9. **Manual entry UI** — New Entries tab functional but clunky. Approach (Flask/Streamlit/other) still TBD.

## Price Check (ready to build — plan fully designed)
10. **Build `listener/price_check.py`** — reads "Price Check" tab, calls Claude to simplify query, searches eBay Browse API, applies IQR + grade filter, writes Clearing/Holding prices back. Full plan at `/Users/eastcoastlimited/.claude/plans/fancy-skipping-teapot.md`.
11. **Add `search_listings_for_price()` to `listener/ebay.py`** — Browse API call, no price/time filters, returns `{price, title}` list, limit 200.
12. **Add sheet helpers to `listener/sheets.py`** — `read_price_check()` and `write_price_check_row()`.
13. **Create `.github/workflows/price-check.yml`** — `workflow_dispatch` only; needs `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `LISTENER_SHEET_ID`, `GOOGLE_SHEETS_CREDENTIALS`, `ANTHROPIC_API_KEY`.
14. **User setup** — create "Price Check" tab in Listener sheet with headers: Description | Hint URL | EPID | Clearing Price | Holding Price | # Listings | Last Checked.

## Traffic Analytics
15. **Revenue metric in daily email** — `orders_raw.sale_price` is available; add a Revenue row to the report. (suggested)
16. **Weekly summary email** — Monday morning email aggregating the full prior week per listing. (suggested)

## Active Listings Counter
*(This was the working title — now implemented as Market Monitor above.)*
