# Next Steps

## Market Monitor
1. **Verify auction time-remaining after 2026-06-28 pipeline run** — `end_time` column was added 2026-06-27; existing rows have NULL so auctions show "—". Confirm Active Auctions table shows real times after the next daily run.
2. **Use Presale Date / Release Date on trend charts** — both fields are now in the sheet, DB, and JSON (`q.presale_date`, `q.release_date`). Wire them as vertical annotations on the Median Price trend chart (e.g. a dashed vertical line labeled "Presale" at the presale date). (suggested)
3. **Filter on presale_date/release_date raw string values** — DB columns are `DATE` type; if a user enters "TBD" or "June 2026" in the sheet, psycopg2 will throw a `DataError` and roll back that query's snapshot. Add a try/parse guard in `sheets.py` before storing. (suggested)
4. **Overview table: filter/group by Type** — with 12+ queries, a Type filter dropdown above the table would let the user focus on one sport/property. (suggested)

## P&L
5. **Listing-level hierarchy refactor** — full design complete (see CLAUDE.md P&L section). Steps: (a) create `listing_groups` table in Supabase; (b) seed from `orders_raw` distinct listing_ids + titles; (c) add Listings tab to sheet for group assignment; (d) update `fetch_sales`, `fetch_ad_fees` to derive group from listing; (e) update P&L by Group formula. Decide whether to add P&L by Listing tab before starting.
6. **Handle refunds** — refunded orders show as positive revenue in Sales tab. Fix: exclude/offset REFUND rows in `fetch_sales()` query. (suggested)
7. **Auto-assign "Unassigned Shipping Labels" group** — for SHIPPING_LABEL DEBIT rows in `order_fees` with no matching order in `orders_raw`, auto-assign `group_name = 'Unassigned Shipping Labels'` during sync. User approved this approach. Not yet implemented.
8. **Guard against group corruption in `save_sale_groups`** — add a check to reject group values that match `SPLIT_PART(order_id, '_', 1)` before writing to DB. Prevents the June 22–24 class of bug from recurring. (suggested)

## Listener
9. **Update cron-job.org frequency from every 15 min to hourly** — agreed 2026-06-21; reduces GHA spend ~75%. Job ID: 7684877. Needs cron-job.org dashboard update (or API call with key from `.claude/settings.local.json`).

## Price Check (ready to build — plan fully designed)
10. **Build `listener/price_check.py`** — reads "Price Check" tab, calls Claude to simplify query, searches eBay Browse API, applies IQR + grade filter, writes Clearing/Holding prices back. Full plan at `/Users/eastcoastlimited/.claude/plans/fancy-skipping-teapot.md`.
11. **Add `search_listings_for_price()` to `listener/ebay.py`** — Browse API call, no price/time filters, returns `{price, title}` list, limit 200.
12. **Add sheet helpers to `listener/sheets.py`** — `read_price_check()` and `write_price_check_row()`.
13. **Create `.github/workflows/price-check.yml`** — `workflow_dispatch` only; needs `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `LISTENER_SHEET_ID`, `GOOGLE_SHEETS_CREDENTIALS`, `ANTHROPIC_API_KEY`.
14. **User setup** — create "Price Check" tab in Listener sheet with headers: Description | Hint URL | EPID | Clearing Price | Holding Price | # Listings | Last Checked.

## Traffic Analytics
15. **Revenue metric in daily email** — `orders_raw.sale_price` is available; add a Revenue row to the report. (suggested)
16. **Weekly summary email** — Monday morning email aggregating the full prior week per listing. (suggested)
