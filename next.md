# Next Steps

## Immediate / Unblocked
1. **Push local P&L changes to repo** — `pl/sync_to_sheets.py` and `CLAUDE.md` have uncommitted changes; the GitHub Action ("P&L ingest") will fail until pushed.
2. **Update listener cron-job.org from every 15 min to hourly** — agreed this session; reduces GHA spend ~75%. Job ID: 7684877. Needs cron-job.org API key or manual update in the dashboard.

## P&L
3. **Handle refunds** — refunded orders show as positive revenue in Sales tab. Fix: offset or exclude REFUND rows from `order_fees` in the sales query in `sync_to_sheets.py`.
4. **Manual entry UI** — New Entries tab works but is clunky. Approach (Flask/Streamlit/other) still TBD.

## Price Check (ready to build — plan fully designed)
5. **Build `listener/price_check.py`** — reads "Price Check" tab, calls Claude to simplify query, searches eBay Browse API, applies IQR + grade filter, writes Clearing/Holding prices back. Full plan at `/Users/eastcoastlimited/.claude/plans/fancy-skipping-teapot.md`.
6. **Add `search_listings_for_price()` to `listener/ebay.py`** — Browse API call, no price/time filters, returns `{price, title}` list, limit 200.
7. **Add sheet helpers to `listener/sheets.py`** — `read_price_check()` and `write_price_check_row()`.
8. **Create `.github/workflows/price-check.yml`** — `workflow_dispatch` only; needs `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `LISTENER_SHEET_ID`, `GOOGLE_SHEETS_CREDENTIALS`, `ANTHROPIC_API_KEY`.
9. **User setup** — create "Price Check" tab in Listener sheet with headers: Description | Hint URL | EPID | Clearing Price | Holding Price | # Listings | Last Checked.

## Traffic Analytics (suggested)
10. **Revenue metric in daily email** — `orders_raw.sale_price` is available; add a Revenue row to the report. (suggested)
11. **Weekly summary email** — Monday morning email aggregating the full prior week per listing. (suggested)
