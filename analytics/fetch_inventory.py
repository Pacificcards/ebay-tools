"""Fetch inventory items from eBay Inventory API and upsert into listing_metadata."""
import os

import requests

from shared.db import get_connection
from shared.ebay_auth import get_access_token

INVENTORY_URL = "https://api.ebay.com/sell/inventory/v1/inventory_item"
PAGE_LIMIT = 200


def fetch_and_store() -> None:
    token = get_access_token(
        os.environ["EBAY_CLIENT_ID"],
        os.environ["EBAY_CLIENT_SECRET"],
        os.environ["EBAY_REFRESH_TOKEN"],
    )

    items = _paginate(token)
    rows = [_to_row(item) for item in items]
    rows = [r for r in rows if r]
    _upsert(rows)
    print(f"[fetch_inventory] upserted {len(rows)} rows")


def _paginate(token: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    items = []
    offset = 0

    while True:
        params = {"limit": PAGE_LIMIT, "offset": offset}
        response = requests.get(INVENTORY_URL, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        batch = data.get("inventoryItems", [])
        items.extend(batch)

        total = data.get("total", 0)
        offset += len(batch)
        if offset >= total or not batch:
            break

    return items


def _to_row(item: dict) -> dict | None:
    # eBay inventory items use SKU as the primary key; listing_id comes from offers
    sku = item.get("sku")
    if not sku:
        return None

    product = item.get("product", {})
    title = product.get("title")
    category = _category(item)

    # Price lives on the offer, not the item — store what we have here
    price = None
    if "price" in item:
        price = float(item["price"].get("value", 0)) or None

    return {
        "listing_id": sku,  # will be updated when offers are fetched
        "title": title,
        "sku": sku,
        "category": category,
        "current_price": price,
    }


def _category(item: dict) -> str | None:
    aspects = item.get("product", {}).get("aspects", {})
    # Best-effort category from product type aspect
    for key in ("Type", "Sport", "Category"):
        val = aspects.get(key)
        if val:
            return val[0] if isinstance(val, list) else val
    return None


def _upsert(rows: list[dict]) -> None:
    if not rows:
        return
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    """
                    INSERT INTO listing_metadata (listing_id, title, sku, category, current_price)
                    VALUES (%(listing_id)s, %(title)s, %(sku)s, %(category)s, %(current_price)s)
                    ON CONFLICT (listing_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        sku = EXCLUDED.sku,
                        category = EXCLUDED.category,
                        current_price = EXCLUDED.current_price,
                        updated_at = NOW()
                    """,
                    row,
                )
    finally:
        conn.close()


if __name__ == "__main__":
    fetch_and_store()
