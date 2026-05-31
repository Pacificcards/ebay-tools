# Next Steps

## Listener
1. **Confirm emoji alert in production** — emoji commit has now been pushed; observe the next real Discord alert to verify 🟢/🔴 renders correctly inline with the % figure.

## P&L
2. **Handle refunds** — 14 refunded orders ($123.27) currently show as positive revenue in the Sales tab. All have order_ids that link to REFUND rows in `order_fees`. Fix: offset or exclude these in `sync_to_sheets.py`.
3. **Categorize Ad Fees tab** — 904 rows are an unlabeled mix of postage, ad/listing fees, store subscription, refunds, and credits. Add a `category` column with auto-classification logic. (suggested)
4. **Surface postage in P&L by Group** — $1,306 in SHIPPING_LABEL spend is captured in `order_fees` but never flows into the P&L by Group costs column. (suggested)

## Analytics Dashboard
5. **Add revenue to Mission Control** — `orders_raw` has `sale_price` but it's never surfaced. Add a revenue column to the table and a trend line to Listing Deep Dive. (suggested)
6. **Surface conversion rate in Deep Dive** — `listing_metrics_computed` already stores `conversion_rate`, `units_per_view`, `units_per_1k_impr`; dashboard never reads them. (suggested)
7. **Mission Control trend view** — currently shows only yesterday's data; add a 7–30 day aggregate chart for impressions + views over time. (suggested)
