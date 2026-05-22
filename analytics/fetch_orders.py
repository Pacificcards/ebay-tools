"""Fetch orders from eBay Fulfillment API and upsert into orders_raw."""
import os
from datetime import date, timedelta, timezone, datetime

import requests

from shared.db import get_connection
from shared.ebay_auth import get_access_token

ORDERS_URL = "https://api.ebay.com/sell/fulfillment/v1/order"
LOOKBACK_DAYS = 90
PAGE_LIMIT = 200


def fetch_and_store() -> None:
    token = get_access_token(
        os.environ["EBAY_CLIENT_ID"],
        os.environ["EBAY_CLIENT_SECRET"],
        os.environ["EBAY_REFRESH_TOKEN"],
    )

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=LOOKBACK_DAYS)
    filter_str = (
        f"creationdate:[{cutoff.strftime('%Y-%m-%dT%H:%M:%S.000Z')}"
        f"..{now.strftime('%Y-%m-%dT%H:%M:%S.000Z')}]"
    )

    orders = _paginate(token, filter_str)
    rows = [_to_row(o) for o in orders if _to_row(o) is not None]
    _upsert([r for r in rows if r])
    print(f"[fetch_orders] upserted {len(rows)} rows")


def _paginate(token: str, filter_str: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    orders = []
    offset = 0

    while True:
        params = {
            "filter": filter_str,
            "limit": PAGE_LIMIT,
            "offset": offset,
        }
        response = requests.get(ORDERS_URL, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        batch = data.get("orders", [])
        orders.extend(batch)

        total = data.get("total", 0)
        offset += len(batch)
        if offset >= total or not batch:
            break

    return orders


def _to_row(order: dict) -> dict | None:
    order_id = order.get("orderId")
    creation_date = order.get("creationDate", "")[:10]

    line_items = order.get("lineItems", [])
    if not line_items:
        return None

    # One row per line item, each with its own listing ID
    rows = []
    for item in line_items:
        listing_id = item.get("legacyItemId") or item.get("lineItemId")
        quantity = item.get("quantity", 1)
        sale_price = item.get("lineItemCost", {}).get("value")

        if not listing_id or sale_price is None:
            continue

        rows.append({
            "order_id": f"{order_id}_{item.get('lineItemId', '')}",
            "listing_id": str(listing_id),
            "order_date": creation_date,
            "quantity": quantity,
            "sale_price": float(sale_price),
        })

    return rows if rows else None


def _upsert(all_rows) -> None:
    # _to_row returns a list or None; flatten
    flat = []
    for r in all_rows:
        if isinstance(r, list):
            flat.extend(r)
        elif r:
            flat.append(r)

    if not flat:
        return

    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            for row in flat:
                cur.execute(
                    """
                    INSERT INTO orders_raw (order_id, listing_id, order_date, quantity, sale_price)
                    VALUES (%(order_id)s, %(listing_id)s, %(order_date)s, %(quantity)s, %(sale_price)s)
                    ON CONFLICT (order_id) DO UPDATE SET
                        quantity = EXCLUDED.quantity,
                        sale_price = EXCLUDED.sale_price,
                        fetched_at = NOW()
                    """,
                    row,
                )
    finally:
        conn.close()


if __name__ == "__main__":
    fetch_and_store()
