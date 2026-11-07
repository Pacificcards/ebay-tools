# Veeqo label-cost integration: handoff

Status as of 2026-11-07. Discovery is done and nothing is built yet. Branch: `claude/inspiring-maxwell-qw21hs` (contains only this doc; temp probes were removed). Nothing was ever pushed to `main`.

## Goal
Capture every shipping label bought in Veeqo and bring it into the P&L:
- For eBay orders, map the label to the eBay order so it flows through exactly like eBay-reported label costs (Sales `shipping_cost`, Ad Fees tab, P&L by Group).
- For non-eBay "direct" orders (Dealernet, offline sales, etc.), there is no order to map to. These still need to land in the P&L via the Ad Fees tab and a user-assigned group.

The user only uses Veeqo for creating and paying for labels (for now). Veeqo is integrated with their eBay account.

## What the Veeqo API gives us (verified live 2026-11-05/06)
- Base URL `https://api.veeqo.com`, header `x-api-key: <key>`. Secret `VEEQO_API_KEY` already exists in GitHub Actions. Add it to local `.env` for local runs.
- `GET /orders` is the only way in. There is **no** `/shipments` list endpoint (404).
  - Params: `page`, `page_size` (max 100), `updated_at_min` (`YYYY-MM-DD HH:MM:SS`, URL-encode the space), `created_at_min`, `status`, `channel_ids[]`, `since_id`.
  - Paging: do NOT rely on `X-Total-Pages-Count`. Loop until a page returns fewer than `page_size` rows (the response-header casing broke my first attempt).
  - Rate limiting: leaky bucket, HTTP 429 when the queue is full. Back off and retry.
- Label cost lives at `order.allocations[].shipment`:
  - `outbound_label_charges.value` (USD) is the total label cost, e.g. `13.48`.
  - `charges[]` is the breakdown (`BASE_RATE`, `SIGNATURE_CONFIRMATION`, `OVERSIZE_SURCHARGE`, `TRANSACTION_FEE`...). In the sample, BASE_RATE + SIGNATURE_CONFIRMATION = 13.48 = `outbound_label_charges.value`.
  - `id` is the shipment id (use for idempotency), `created_at`, `sub_carrier_id` (USPS / UPS), `tracking_number.tracking_number`, `tracking_number.cancelled`, `carrier_id`.
- Three kinds of shipment appear:
  1. Veeqo-bought labels (`carrier_id` 33, "Buy Shipping"): have cost.
  2. Orders marked shipped without a Veeqo label (`carrier_id` 3): `charges` is `{}` and cost is null. Skip these.
  3. Direct-channel orders (`channel.type_code == "direct"`, number like `#P-2245589525`): labels with cost, no eBay match.
- eBay mapping is clean: for `channel.type_code == "ebay"` orders, Veeqo `order.number` equals the eBay order id exactly (e.g. `02-15405-69621`), and `orders_raw.order_id` is `<number>_<lineItemId>`. 10 of 10 sampled eBay orders matched via `SPLIT_PART(order_id, '_', 1)`. `line_items[].remote_id` looks like `<itemId>-<transactionId>`.
- Veeqo order JSON contains PII (names, addresses, `buyer_user_id`, the shipper's `login`). Never print those in CI logs. The repo is **public**, so Actions logs are public.

## Findings from the data
- Only 6 cost-bearing labels exist in the last 60 days (4 eBay, 2 direct, $57.23 total), all created Nov 4-5. Veeqo label usage started about Nov 4, so **no historical backfill is needed**.
- No voided labels seen yet (`tracking_number.cancelled` was false everywhere), so void handling is untested.
- No order had more than one shipment.
- **Dedupe check is inconclusive.** None of the 4 eBay-channel Veeqo labels had an eBay `SHIPPING_LABEL` row in `order_fees`, but all were under a day old, and eBay Finances can lag by hours or more. Re-run the comparison after about Nov 8-9. See "Next steps".

## How it plugs into the existing P&L (`pl/sync_to_sheets.py`, line numbers approximate)
- `order_fees` has a unique `billing_transaction_id`, nullable `order_id`, `fee_type`, `amount`, `booking_entry`, `group_name`.
- Sales `shipping_cost` (around line 168) sums `order_fees` where `fee_type = 'SHIPPING_LABEL' AND booking_entry = 'DEBIT'` joined on `SPLIT_PART(orders_raw.order_id,'_',1)`.
- `fetch_ad_fees` (around line 212): group inheritance applies to `fee_type = 'SHIPPING_LABEL'` when `order_id` is not null (around line 246). Category "Shipping" applies to `SHIPPING_LABEL` / `SHIPPING_MANUAL` with booking_entry not `CREDIT` (around line 264). The group column is user-editable and persisted via `save_ad_fee_groups` to `order_fees.group_name`.
- **Recommended design:** insert Veeqo labels as `fee_type = 'SHIPPING_LABEL'`, `booking_entry = 'DEBIT'`, with `billing_transaction_id = 'VEEQO-<shipment_id>'`. Set `order_id` to the base eBay order id for eBay-channel orders and NULL for direct orders. All existing joins, group inheritance and the "Shipping" category then work with no P&L code changes. The `VEEQO-` prefix identifies the source, and `ON CONFLICT DO NOTHING` keeps reruns idempotent.
- Direct orders appear in the Ad Fees tab only (category "Shipping"), with a blank group until the user assigns one in Ad Fees col H.

## Editing and deleting (decided: use offsets)
- Group assignment: edit the `group` column in the Ad Fees tab (already works).
- The existing New Entries delete flow only works for manual entries (`record_id` prefixes `MANUAL-`, `SHIP-`, `ADJ-`, or numeric purchase ids). It cannot delete imported rows, and a plain DELETE would be re-imported on the next fetch.
- **To cancel or void a label: add a New Entries row with type `shipping`, a negative amount, the same group as the original, and a non-empty description.** Per reading of the code (not yet tested live): amount parsing only strips `$` and commas, so `-5.64` is accepted, and it inserts as `SHIPPING_MANUAL` DEBIT with a negative amount, which nets against the group's "Shipping" total in P&L by Group.
- Caveats: the Sales tab `shipping_cost` for that order will not change (the manual row has no order id), and both rows stay visible in Ad Fees. The code skips New Entries rows with a blank description, even though CLAUDE.md says it is optional for shipping.
- If cancellations turn out to be frequent, consider a dedicated ignore flag. That is out of scope for now.

## Next steps
1. **Re-run the dedupe check** around Nov 8-9 (see "How the probe worked"). For each Veeqo eBay-channel label, compare against `order_fees` rows where `fee_type = 'SHIPPING_LABEL'` and `order_id` = the Veeqo order number. If eBay never reports them, import with no dedupe logic. If eBay does report them, pick one source of truth, since otherwise shipping cost is double counted.
2. Build `traffic_analytics/fetch_veeqo_labels.py`, in the style of `fetch_finances.py` / `fetch_ebay_purchases.py`:
   - Pull `/orders` with `updated_at_min` about 14 days back, paging to exhaustion.
   - Keep shipments where `outbound_label_charges.value > 0` and `tracking_number.cancelled` is not true.
   - Insert into `order_fees` as above, with `transaction_date` = the shipment `created_at` converted to Pacific Time (the project convention).
   - Add retries and timeouts on HTTP calls.
3. Wire it into the daily flow. Candidates: a step in `analytics-ingest.yml` (runs twice daily; `pl-ingest.yml` chains off it via `workflow_run`), or its own workflow. Pass `VEEQO_API_KEY` and `SUPABASE_DB_URL` as env.
4. Add tests (mock HTTP, check idempotency, direct vs eBay, zero-cost skip, cancelled skip).
5. Update `CLAUDE.md`: new subproject notes, the `VEEQO_API_KEY` secret, the negative-shipping-entry cancellation convention, and a session log entry.
6. Open question: confirm voided-label behaviour once a real void exists. If Veeqo flips `tracking_number.cancelled` or removes the charges, decide whether the importer should insert a CREDIT offset automatically.

## How the probe worked (to repeat the dedupe check)
- Cloud sessions here cannot reach Supabase or Veeqo directly, so the probe ran as a GitHub Actions job.
- It used a **push-triggered workflow that exists only on the working branch** (`on: push: branches: [claude/...]` with a `paths:` filter), so nothing touched `main`. Contrast with `CLAUDE.md`'s `workflow_dispatch`-on-`main` method, which was not needed.
- The probe script used stdlib `urllib` plus `psycopg2`, with env `VEEQO_API_KEY` and `SUPABASE_DB_URL`. It must print only aggregates and masked order-id tails, never PII (public logs). My first probe printed buyer usernames and the shipper's name by mistake; the user was asked to delete that run (42128611086) from the Actions tab.
- Locally you can simply run the same query with your own `.env`. No workflow needed.

## Gotchas
- The Veeqo MCP (`https://app.veeqo.com/mcp`, OAuth or `Authorization: Bearer apikey:<key>`) is fine for ad-hoc exploration in a local session, but it is not suitable for the scheduled pipeline. Use the REST API there.
- During this session GitHub intermittently returned 500 "commit_refs" errors on pushes to the branch. A later retry succeeded. If it recurs, retry rather than assume the push worked.
- Follow the repo rule: schema changes must be pushed atomically with code. None are needed for the recommended design, which reuses `order_fees`.
- Run `.venv/bin/python -m pytest tests/ -q` before pushing, and stage files by name (the repo often has unrelated uncommitted work).
