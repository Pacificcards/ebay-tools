# Next Steps

## P&L — Code Review Fixes (2026-09-09 review, not yet applied)

1. **`_backfill_record_ids` routes shipping/adjustment entries to wrong table** — `shipping` and `adjustment` type rows fall into the `else` branch and query `import_queue`, but those entries live in `order_fees`. Result: their `record_id` is never backfilled → "Marked for Deletion" permanently stamps `✗ No Record ID` and the entry can never be deleted. Fix: add branches for `SHIP-` and `ADJ-` prefixes that query `order_fees` instead.

2. **`_insert_manual_adjustment` hardcodes `DEBIT` for all manual adjustments** — The P&L formula computes `adjustments = CREDIT_sum − DEBIT_sum`. Any adjustment entered via New Entries (type=adjustment) always inserts as DEBIT, so even a received credit/refund subtracts from profit instead of adding to it. Fix: add a `booking_entry` field to the New Entries UI, or document that the adjustment type is only for refunds paid out (costs), and rely on eBay-sourced REFUND/CREDIT rows for credits received.

3. **`save_purchase_groups` crashes on blank `id` cell** — `int(k)` is called on every key in the groups dict with no guard. A blank `id` cell in the Purchases tab (e.g. a user-added empty row) produces key `''` → `int('')` raises `ValueError` → uncaught → all purchase group assignments for that sync run are lost silently. Fix: filter out non-numeric keys before the `execute_values` call.

4. **Blank New Entries amount silently loses the entry** — `raw_amount = row[3].strip().replace("$", "").replace(",", "") or None` resolves `None` for a blank amount. For purchase/shipping/adjustment types this is passed directly to a NOT NULL DB column → `IntegrityError` inside the per-row savepoint → rolled back silently → no error stamped on the sheet row → entry re-attempted on every sync forever. Fix: detect blank amount before inserting and stamp an error status on the row.

5. **Date parse failure in New Entries doesn't stamp an error status** — when `_normalize_date` raises `ValueError`, a message is printed to stdout and the loop continues; the sheet row keeps a blank status and is re-attempted on every subsequent sync with no user-visible feedback. Fix: stamp `✗ Invalid date: '...'` on the row, same pattern as invalid type.

6. **`ws.update()` in `_ensure_new_entries_tab` missing `'A1'` range_name (line 352)** — every other `ws.update()` in the file passes explicit `'A1'`; this one omits it. Per gspread 6.x constraint, range_name is required to avoid ambiguity. Low risk in practice (newly-created tab), but inconsistent.

7. **Deletion loop calls `ws.update_cell()` per row** — each "Marked for Deletion" row triggers a separate Sheets API HTTP call instead of being batched into the `ws.batch_update()` at the end of `process_new_entries`. Fix: accumulate deletion stamps alongside the other status updates and send in one batch call.

8. **`_backfill_record_ids` issues one DB round-trip per row** — queries `import_queue` (or `orders_raw`) individually for each row that needs backfilling. At 50+ rows this becomes 1–2s of avoidable latency. Fix: batch all lookups into one `IN (...)` query.

---

## P&L — Features

9. **Listing-level hierarchy refactor** — Group > Listing > Order; new `listing_groups` table; design complete (see CLAUDE.md P&L section). Steps: (a) create `listing_groups` in Supabase; (b) seed from `orders_raw` distinct listing_ids + titles; (c) add Listings tab to sheet; (d) update `fetch_sales`, `fetch_ad_fees` to derive group from listing; (e) update P&L by Group formula. Decide whether to add P&L by Listing tab before starting.

10. **Auto-assign "Unassigned Shipping Labels" group** — for SHIPPING_LABEL DEBIT rows in `order_fees` with no matching order in `orders_raw`, auto-assign `group_name = 'Unassigned Shipping Labels'` during sync. User approved this approach. Not yet implemented.

11. **Guard against group corruption in `save_sale_groups`** — add a check to reject group values that match `SPLIT_PART(order_id, '_', 1)` before writing to DB. Prevents the June 22–24 class of bug from recurring. (suggested)

12. **Monthly tab: extend to future years** — currently hardcoded Jan–Dec 2026. When 2027 rolls around, update `write_monthly_tab` to add a new set of 12 rows (or make the year range dynamic). (suggested)

---

## Listings Publisher

12. **Sport-to-League mapping coverage** — only Baseball/Football/Basketball mapped; extend `_SPORT_TO_LEAGUE` in `listings-publisher/publish.py` when adding other sports. (suggested)
13. **Bulk retry error rows** — consider a `--retry-errors` flag that clears Status for all error rows automatically. (suggested)
14. **Mobile edit limitation** — Inventory API listings can't be edited via eBay mobile app. Reprice via sheet is the current workaround. Trading API migration (`AddFixedPriceItem`) would fully resolve this but was deferred.

---

## Market Monitor

15. **Presale Date / Release Date annotations on trend charts** — both fields are in the sheet, DB, and JSON (`q.presale_date`, `q.release_date`). Wire as vertical dashed-line annotations on the price chart labeled "Presale" / "Release". (suggested)
16. **Overview table: filter/group by Type** — with 17 queries, a Type filter dropdown above the table would let the user focus on one sport/property. (suggested)

---

## Listener

17. **Update cron-job.org frequency from every 15 min to hourly** — agreed 2026-06-21; reduces GHA spend ~75%. Job ID: 7684877. Needs cron-job.org dashboard update (or API call with key from `.claude/settings.local.json`).

---

## Price Check (ready to build — plan fully designed)

18. **Build `listener/price_check.py`** — reads "Price Check" tab, calls Claude to simplify query, searches eBay Browse API, applies IQR + grade filter, writes Clearing/Holding prices back. Full plan at `/Users/eastcoastlimited/.claude/plans/fancy-skipping-teapot.md`.
19. **Add `search_listings_for_price()` to `listener/ebay.py`** — Browse API call, no price/time filters, returns `{price, title}` list, limit 200.
20. **Add sheet helpers to `listener/sheets.py`** — `read_price_check()` and `write_price_check_row()`.
21. **Create `.github/workflows/price-check.yml`** — `workflow_dispatch` only; needs `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `LISTENER_SHEET_ID`, `GOOGLE_SHEETS_CREDENTIALS`, `ANTHROPIC_API_KEY`.
22. **User setup** — create "Price Check" tab in Listener sheet with headers: Description | Hint URL | EPID | Clearing Price | Holding Price | # Listings | Last Checked.

---

## Traffic Analytics

23. **Backfill the 8/6 gap** — partial coverage left over from the 2026-08-07 pagination-bug backfill hitting the API's daily rate limit. Low priority, explicitly deferred by user until requested — re-run `_fetch_window_with_retry` for just that date.
24. **Verify `views_total` is actually comprehensive** — same `LISTING_*_TOTAL` naming pattern that undersold impressions; not yet checked against Seller Hub. (suggested)
25. **Confirm the mobile fixes actually look right on a real device** — the 2026-08-08/09 mobile pass was iterated purely from user screenshots; worth a final on-device pass. (suggested)

---

## Takehome Calculator

26. **Confirm the Advanced Fee Settings relocation matched intent** — "move the FVF calculator to the Advanced Fee Settings area" was interpreted as moving the itemized High tier/Low tier/Flat fee/FVF credit lines there (recapped with a Total eBay Fee line), keeping one Total eBay Fee summary line in the main breakdown. Not yet explicitly confirmed. (suggested)
27. **Real-browser visual pass** — breakdown height-sync and Qty/Unit-Price box-height fix were only verified structurally (jsdom), not pixel-checked. (suggested)

---

## Infra

28. **Fix `compute-metrics.yml`** — references `python -m analytics.compute_metrics` (wrong module path; should be `traffic_analytics.compute_metrics`). Workflow is also redundant since `analytics-ingest.yml` already runs this step. Either fix the path or delete the workflow. (suggested)
