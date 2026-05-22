"""Fetch orders from eBay Fulfillment API and upsert into orders_raw.

Runs daily, fetching the last 2 days to catch any late-arriving orders.
Set BACKFILL=1 to fetch historical data from BACKFILL_START (default 2026-01-01).
"""
import os
from datetime import date, timedelta, timezone, datetime

import requests

from shared.db import get_connection
from shared.ebay_auth import get_access_token

ORDERS_URL = "https://api.ebay.com/sell/fulfillment/v1/order"
PAGE_LIMIT = 200


def fetch_and_store() -> None:
    token = get_access_token(
        os.environ["EBAY_CLIENT_ID"],
        os.environ["EBAY_CLIENT_SECRET"],
        os.environ["EBAY_REFRESH_TOKEN"],
    )

    now = datetime.now(timezone.utc)

    if os.environ.get("BACKFILL"):
        start = date.fromisoformat(os.environ.get("BACKFILL_START", "2026-01-01"))
        cutoff = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    else:
        cutoff = now - timedelta(days=2)

    filter_str = (
        f"creationdate:[{cutoff.strftime('%Y-%m-%dT%H:%M:%S.000Z')}"
        f"..{now.strftime('%Y-%m-%dT%H:%M:%S.000Z')}]"
    )

    orders = _paginate(token, filter_str)
    flat_rows = _flatten([_to_rows(o) for o in orders])
    _upsert(flat_rows)
    print(f"[fetch_orders] upserted {len(flat_rows)} line items from {len(orders)} orders")


def _paginate(token: str, filter_str: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    orders = []
    offset = 0

    while True:
        params = {"filter": filter_str, "limit": PAGE_LIMIT, "offset": offset}
        response = requests.get(ORDERS_URL, headers=headers, params=params)
        if not response.ok:
            print(f"[fetch_orders] HTTP {response.status_code}: {response.text}")
            response.raise_for_status()

        data = response.json()
        batch = data.get("orders", [])
        orders.extend(batch)

        total = data.get("total", 0)
        offset += len(batch)
        if offset >= total or not batch:
            break

    return orders


def _to_rows(order: dict) -> list[dict]:
    order_id = order.get("orderId")
    creation_date = order.get("creationDate", "")[:10]
    rows = []

    for item in order.get("lineItems", []):
        listing_id = item.get("legacyItemId") or item.get("lineItemId")
        quantity = item.get("quantity", 1)
        sale_price = item.get("lineItemCost", {}).get("value")

        if not listing_id or sale_price is None:
            continue

        rows.append({
            "order_id":   f"{order_id}_{item.get('lineItemId', '')}",
            "listing_id": str(listing_id),
            "order_date": creation_date,
            "quantity":   quantity,
            "sale_price": float(sale_price),
        })

    return rows


def _flatten(nested: list[list[dict]]) -> list[dict]:
    return [row for rows in nested for row in rows]


def _upsert(rows: list[dict]) -> None:
    if not rows:
        return
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    """
                    INSERT INTO orders_raw (order_id, listing_id, order_date, quantity, sale_price)
                    VALUES (%(order_id)s, %(listing_id)s, %(order_date)s, %(quantity)s, %(sale_price)s)
                    ON CONFLICT (order_id) DO UPDATE SET
                        quantity   = EXCLUDED.quantity,
                        sale_price = EXCLUDED.sale_price,
                        fetched_at = NOW()
                    """,
                    row,
                )
    finally:
        conn.close()


if __name__ == "__main__":
    fetch_and_store()
