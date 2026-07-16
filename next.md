# Next Steps

## Market Monitor
1. **Presale Date / Release Date annotations on trend charts** — both fields are in the sheet, DB, and JSON (`q.presale_date`, `q.release_date`). Wire as vertical dashed-line annotations on the price chart labeled "Presale" / "Release". (suggested)
2. **Overview table: filter/group by Type** — with 17 queries, a Type filter dropdown above the table would let the user focus on one sport/property. (suggested)

## P&L
3. **Listing-level hierarchy refactor** — Group > Listing > Order; new `listing_groups` table; design complete (see CLAUDE.md P&L section). Steps: (a) create `listing_groups` in Supabase; (b) seed from `orders_raw` distinct listing_ids + titles; (c) add Listings tab to sheet; (d) update `fetch_sales`, `fetch_ad_fees` to derive group from listing; (e) update P&L by Group formula. Decide whether to add P&L by Listing tab before starting.
4. **Handle refunds** — refunded orders show as positive revenue in Sales tab. Fix: exclude/offset REFUND rows in `fetch_sales()` query. (suggested)
5. **Auto-assign "Unassigned Shipping Labels" group** — for SHIPPING_LABEL DEBIT rows in `order_fees` with no matching order in `orders_raw`, auto-assign `group_name = 'Unassigned Shipping Labels'` during sync. User approved this approach. Not yet implemented.
6. **Guard against group corruption in `save_sale_groups`** — add a check to reject group values that match `SPLIT_PART(order_id, '_', 1)` before writing to DB. Prevents the June 22–24 class of bug from recurring. (suggested)

## Listener
7. **Update cron-job.org frequency from every 15 min to hourly** — agreed 2026-06-21; reduces GHA spend ~75%. Job ID: 7684877. Needs cron-job.org dashboard update (or API call with key from `.claude/settings.local.json`).

## Price Check (ready to build — plan fully designed)
8. **Build `listener/price_check.py`** — reads "Price Check" tab, calls Claude to simplify query, searches eBay Browse API, applies IQR + grade filter, writes Clearing/Holding prices back. Full plan at `/Users/eastcoastlimited/.claude/plans/fancy-skipping-teapot.md`.
9. **Add `search_listings_for_price()` to `listener/ebay.py`** — Browse API call, no price/time filters, returns `{price, title}` list, limit 200.
10. **Add sheet helpers to `listener/sheets.py`** — `read_price_check()` and `write_price_check_row()`.
11. **Create `.github/workflows/price-check.yml`** — `workflow_dispatch` only; needs `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `LISTENER_SHEET_ID`, `GOOGLE_SHEETS_CREDENTIALS`, `ANTHROPIC_API_KEY`.
12. **User setup** — create "Price Check" tab in Listener sheet with headers: Description | Hint URL | EPID | Clearing Price | Holding Price | # Listings | Last Checked.

## Traffic Analytics
13. **Revenue metric in daily email** — `orders_raw.sale_price` is available; add a Revenue row to the report. (suggested)
14. **Weekly summary email** — Monday morning email aggregating the full prior week per listing. (suggested)

## Infra
15. **Fix `compute-metrics.yml`** — references `python -m analytics.compute_metrics` (wrong module path; should be `traffic_analytics.compute_metrics`). Workflow is also redundant since `analytics-ingest.yml` already runs this step. Either fix the path or delete the workflow. (suggested)
