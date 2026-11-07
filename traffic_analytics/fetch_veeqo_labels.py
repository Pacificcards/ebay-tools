"""Fetch shipping labels bought in Veeqo and insert them into order_fees.

Each label with a cost becomes one order_fees row (fee_type SHIPPING_LABEL, DEBIT,
billing_transaction_id VEEQO-<shipment_id>), so the existing P&L queries (Sales
shipping_cost, Ad Fees tab, group inheritance) pick it up with no changes.

  - eBay-channel orders: order_id is the eBay order id (Veeqo order number == eBay order id).
  - Any other channel (direct/Dealernet/offline): order_id is a made-up "VEEQO-<number>",
    which matches no eBay sale, so the label shows up in the Ad Fees tab only.

NEVER print Veeqo order JSON: it contains buyer names/addresses and this repo's
Actions logs are public. Log counts only.

Usage:
  python -m traffic_analytics.fetch_veeqo_labels             # last 14 days (daily runs)
  python -m traffic_analytics.fetch_veeqo_labels --backfill  # everything since 2026-11-04
  Add --dry-run to fetch and parse but write nothing (prints aggregates only).
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()

from shared.db import get_connection

BASE_URL = "https://api.veeqo.com"
PAGE_SIZE = 100
REGULAR_DAYS = 14
BACKFILL_START = datetime(2026, 11, 4, tzinfo=timezone.utc)  # first day labels were bought in Veeqo
PACIFIC = ZoneInfo("America/Los_Angeles")
MAX_RETRIES = 5
TIMEOUT = 30


def fetch_and_store(since: datetime, dry_run: bool = False) -> None:
    api_key = os.environ["VEEQO_API_KEY"]

    print(f"[fetch_veeqo_labels] fetching Veeqo orders updated since {since:%Y-%m-%d}...")
    orders = _fetch_orders(api_key, since)
    print(f"[fetch_veeqo_labels] fetched {len(orders)} orders")

    rows = []
    for order in orders:
        rows.extend(_label_rows(order))
    print(f"[fetch_veeqo_labels] {len(rows)} cost-bearing labels")

    if dry_run:
        _print_summary(rows)
        return

    inserted = _insert(rows)
    print(f"[fetch_veeqo_labels] inserted {inserted} new labels ({len(rows) - inserted} already present)")


def _print_summary(rows: list[dict]) -> None:
    """Aggregates + masked order-id tails only: logs can be public, never print PII."""
    direct = [r for r in rows if r["order_id"].startswith("VEEQO-")]
    print(f"[fetch_veeqo_labels] DRY RUN, nothing written. "
          f"{len(rows)} labels, ${sum(r['amount'] for r in rows):.2f} total "
          f"({len(rows) - len(direct)} eBay, {len(direct)} direct)")
    for r in sorted(rows, key=lambda r: str(r["transaction_date"])):
        print(f"  {r['transaction_date']}  ${r['amount']:>7.2f}  "
              f"{'direct' if r['order_id'].startswith('VEEQO-') else 'ebay  '}  ...{r['order_id'][-4:]}")


def _fetch_orders(api_key: str, since: datetime) -> list[dict]:
    """Page through GET /orders until a page comes back short."""
    orders = []
    page = 1
    while True:
        params = {
            "page": page,
            "page_size": PAGE_SIZE,
            "updated_at_min": since.strftime("%Y-%m-%d %H:%M:%S"),
        }
        batch = _get(api_key, "/orders", params)
        orders.extend(batch)
        if len(batch) < PAGE_SIZE:
            return orders
        page += 1


def _get(api_key: str, path: str, params: dict):
    """GET with backoff on 429 (leaky-bucket rate limit) and transient 5xx."""
    for attempt in range(MAX_RETRIES):
        resp = requests.get(
            BASE_URL + path, params=params, headers={"x-api-key": api_key}, timeout=TIMEOUT
        )
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Veeqo GET {path} still failing after {MAX_RETRIES} attempts (HTTP {resp.status_code})")


def _label_rows(order: dict) -> list[dict]:
    """Turn one Veeqo order into order_fees rows, one per cost-bearing, non-cancelled label."""
    number = str(order.get("number") or "").lstrip("#").strip()
    if not number:
        return []
    is_ebay = (order.get("channel") or {}).get("type_code") == "ebay"
    order_id = number if is_ebay else f"VEEQO-{number}"

    rows = []
    for alloc in order.get("allocations") or []:
        shipment = alloc.get("shipment")
        if not shipment:
            continue
        if (shipment.get("tracking_number") or {}).get("cancelled"):
            continue
        cost = _amount((shipment.get("outbound_label_charges") or {}).get("value"))
        if not cost or cost <= 0:
            continue  # marked shipped without buying a Veeqo label
        rows.append({
            "billing_transaction_id": f"VEEQO-{shipment['id']}",
            "order_id":               order_id,
            "transaction_date":       _pacific_date(shipment.get("created_at")),
            "fee_type":               "SHIPPING_LABEL",
            "amount":                 cost,
            "booking_entry":          "DEBIT",
        })
    return rows


def _insert(rows: list[dict]) -> int:
    if not rows:
        return 0
    inserted = 0
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            for row in rows:
                # DO NOTHING (not UPDATE) so a group the user assigned in Ad Fees is never reset.
                cur.execute(
                    """
                    INSERT INTO order_fees (
                        billing_transaction_id, order_id, transaction_date,
                        fee_type, amount, booking_entry
                    ) VALUES (
                        %(billing_transaction_id)s, %(order_id)s, %(transaction_date)s,
                        %(fee_type)s, %(amount)s, %(booking_entry)s
                    )
                    ON CONFLICT (billing_transaction_id) DO NOTHING
                    """,
                    row,
                )
                inserted += cur.rowcount
    finally:
        conn.close()
    return inserted


def _pacific_date(raw: str | None):
    """Veeqo timestamp -> calendar date in Pacific Time (project convention)."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PACIFIC).date()


def _amount(val) -> float | None:
    try:
        return round(float(val), 2) if val is not None else None
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    if "--backfill" in sys.argv:
        print("[fetch_veeqo_labels] BACKFILL MODE: everything since 2026-11-04")
        start = BACKFILL_START
    else:
        start = datetime.now(timezone.utc) - timedelta(days=REGULAR_DAYS)
    fetch_and_store(start, dry_run="--dry-run" in sys.argv)
