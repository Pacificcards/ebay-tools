# Next Steps

## Listings Publisher
1. **Sport-to-League mapping coverage** — only Baseball/Football/Basketball mapped; extend `_SPORT_TO_LEAGUE` in `listings-publisher/publish.py` when adding other sports. (suggested)
2. **Bulk retry error rows** — consider a `--retry-errors` flag that clears Status for all error rows automatically. (suggested)
3. **Mobile edit limitation** — Inventory API listings can't be edited via eBay mobile app. Reprice via sheet is the current workaround. Trading API migration (`AddFixedPriceItem`) would fully resolve this but was deferred.

## Market Monitor
1. **Presale Date / Release Date annotations on trend charts** — both fields are in the sheet, DB, and JSON (`q.presale_date`, `q.release_date`). Wire as vertical dashed-line annotations on the price chart labeled "Presale" / "Release". (suggested)
2. **Overview table: filter/group by Type** — with 17 queries, a Type filter dropdown above the table would let the user focus on one sport/property. (suggested)

## P&L
3. **Listing-level hierarchy refactor** — Group > Listing > Order; new `listing_groups` table; design complete (see CLAUDE.md P&L section). Steps: (a) create `listing_groups` in Supabase; (b) seed from `orders_raw` distinct listing_ids + titles; (c) add Listings tab to sheet; (d) update `fetch_sales`, `fetch_ad_fees` to derive group from listing; (e) update P&L by Group formula. Decide whether to add P&L by Listing tab before starting.
4. **Adjustments feature** — design complete (2026-08-07); fixes refunds showing as positive revenue. Implement in order:
   - a) `fetch_ad_fees()` SQL: update `category` CASE to map `REFUND`/`CREDIT`/`ADJUSTMENT`/`ADJUSTMENT_MANUAL` → "Adjustment"; map `SHIPPING_LABEL` CREDIT → "Adjustment" (currently maps to "Shipping" regardless of booking_entry — bug). Add `fl.booking_entry` as col J to the SELECT.
   - b) `AD_FEES_HEADERS`: add `"booking_entry"` (col J, index 9). `_read_ad_fee_groups()` and `save_ad_fee_groups()` unchanged (use col G/H).
   - c) `write_pl_tab()`: add `adjustments` column (`ab = 'Ad Fees'!J2:J{n}`); formula = BYROW+LAMBDA `SUMIFS(CREDIT,"Adjustment") − SUMIFS(DEBIT,"Adjustment")` per group. Update profit formula to `B−C−D−E+F`. Headers become 7 cols: `group|net_payout|costs|ad_fees|shipping_cost|adjustments|profit`.
   - d) `process_new_entries()`: add `"adjustment"` to valid types.
   - e) `_insert_manual_adjustment()`: new function — mirrors `_insert_manual_shipping()`; inserts into `order_fees` as `ADJUSTMENT_MANUAL` / `DEBIT`; record_id = `ADJ-{hex16}`.
   - f) `_delete_manual_entry()`: add `ADJ-` branch — `DELETE FROM order_fees WHERE billing_transaction_id = %s AND fee_type = 'ADJUSTMENT_MANUAL'`.
   - g) Tests: update Ad Fees header count 9→10, add booking_entry assertion, add P&L adjustments column assertion, add "adjustment" type test.
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
13. **Backfill the 8/6 gap** — partial coverage left over from the 2026-08-07 pagination-bug backfill hitting the API's daily rate limit. Low priority, explicitly deferred by user until requested — re-run `_fetch_window_with_retry` for just that date.
14. **Verify `views_total` is actually comprehensive** — same `LISTING_*_TOTAL` naming pattern that undersold impressions; not yet checked against Seller Hub. Now easy to check using the proven `listing_ids`-filter live-pull method from the pagination fix. See CLAUDE.md Traffic Analytics TODOs.

## Infra
15. **Fix `compute-metrics.yml`** — references `python -m analytics.compute_metrics` (wrong module path; should be `traffic_analytics.compute_metrics`). Workflow is also redundant since `analytics-ingest.yml` already runs this step. Either fix the path or delete the workflow. (suggested)
