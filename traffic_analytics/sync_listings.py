"""Sync active listings from eBay Trading API into listing_metadata.

Fetches all active listings via GetMyeBaySelling, upserts into listing_metadata,
and marks any previously-known listing not in the response as 'ended'.

Run this before fetch_analytics so the active-listing filter is current.
Requires the base OAuth scope: https://api.ebay.com/oauth/api_scope
"""
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from shared.db import get_connection
from shared.ebay_auth import get_access_token

TRADING_API_URL = "https://api.ebay.com/ws/api.dll"
NS = {"e": "urn:ebay:apis:eBLBaseComponents"}
PAGE_SIZE = 200


def sync() -> None:
    token = get_access_token(
        os.environ["EBAY_CLIENT_ID"],
        os.environ["EBAY_CLIENT_SECRET"],
        os.environ["EBAY_REFRESH_TOKEN"],
    )

    all_items = _fetch_all_active(token)
    print(f"[sync_listings] fetched {len(all_items)} active listings from eBay")

    _upsert(all_items)
    _mark_ended(all_items)
    _backfill_categories(token)

    print("[sync_listings] done")


def _backfill_categories(token: str) -> None:
    """Fetch PrimaryCategory via GetItem for any active listing still missing one.

    Category essentially never changes once a listing is published, so this only
    costs one extra Trading API call per listing the first time it's seen -- not
    on every nightly run.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT listing_id FROM listing_metadata WHERE status != 'ended' AND category_name IS NULL"
            )
            missing_ids = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()

    if not missing_ids:
        return

    print(f"[sync_listings] backfilling category for {len(missing_ids)} listing(s)")
    updates = []
    for listing_id in missing_ids:
        cat = _fetch_category(token, listing_id)
        if cat:
            updates.append((listing_id, cat["id"], cat["name"]))

    if not updates:
        return

    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            for listing_id, cat_id, cat_name in updates:
                cur.execute(
                    "UPDATE listing_metadata SET category_id = %s, category_name = %s WHERE listing_id = %s",
                    (cat_id, cat_name, listing_id),
                )
        print(f"[sync_listings] category backfilled for {len(updates)} listing(s)")
    finally:
        conn.close()


def _fetch_category(token: str, listing_id: str) -> dict | None:
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <ItemID>{listing_id}</ItemID>
  <OutputSelector>Item.PrimaryCategory</OutputSelector>
</GetItemRequest>"""
    headers = {
        "X-EBAY-API-CALL-NAME": "GetItem",
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-IAF-TOKEN": token,
        "Content-Type": "text/xml",
    }
    response = requests.post(TRADING_API_URL, data=body.encode("utf-8"), headers=headers)
    if not response.ok:
        print(f"[sync_listings] GetItem HTTP {response.status_code} for {listing_id}: {response.text[:200]}")
        return None

    root = ET.fromstring(response.text)
    cat_id = root.findtext(".//e:PrimaryCategory/e:CategoryID", namespaces=NS)
    cat_name = root.findtext(".//e:PrimaryCategory/e:CategoryName", namespaces=NS)
    if not cat_id:
        return None
    return {"id": cat_id, "name": cat_name}


def _fetch_all_active(token: str) -> list[dict]:
    all_items = []
    page = 1

    while True:
        items, total_pages = _fetch_page(token, page)
        all_items.extend(items)
        print(f"[sync_listings] page {page}/{total_pages}: {len(items)} listings")
        if page >= total_pages:
            break
        page += 1

    return all_items


def _fetch_page(token: str, page: int) -> tuple[list[dict], int]:
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <ActiveList>
    <Include>true</Include>
    <Pagination>
      <EntriesPerPage>{PAGE_SIZE}</EntriesPerPage>
      <PageNumber>{page}</PageNumber>
    </Pagination>
  </ActiveList>
</GetMyeBaySellingRequest>"""

    headers = {
        "X-EBAY-API-CALL-NAME": "GetMyeBaySelling",
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-IAF-TOKEN": token,
        "Content-Type": "text/xml",
    }

    response = requests.post(TRADING_API_URL, data=body.encode("utf-8"), headers=headers)
    if not response.ok:
        print(f"[sync_listings] HTTP {response.status_code}: {response.text}")
        response.raise_for_status()

    return _parse(response.text)


def _parse(xml_text: str) -> tuple[list[dict], int]:
    root = ET.fromstring(xml_text)

    ack = root.findtext("e:Ack", namespaces=NS)
    if ack not in ("Success", "Warning"):
        for err in root.findall(".//e:Errors", namespaces=NS):
            print(f"[sync_listings] eBay error: {err.findtext('e:LongMessage', namespaces=NS)}")
        raise RuntimeError(f"GetMyeBaySelling returned Ack={ack}")

    items = []
    for item_el in root.findall(".//e:ActiveList/e:ItemArray/e:Item", namespaces=NS):
        listing_id = item_el.findtext("e:ItemID", namespaces=NS)
        if not listing_id:
            continue

        hide_text = item_el.findtext("e:HideFromSearch", namespaces=NS)
        hide_from_search = hide_text is not None and hide_text.lower() == "true"

        qty_text = item_el.findtext("e:Quantity", namespaces=NS)
        qty_sold_text = item_el.findtext("e:SellingStatus/e:QuantitySold", namespaces=NS)

        items.append({
            "listing_id": listing_id,
            "title": item_el.findtext("e:Title", namespaces=NS),
            "sku": item_el.findtext("e:SKU", namespaces=NS),
            "current_price": _price(item_el),
            "hide_from_search": hide_from_search,
            "hide_reason": item_el.findtext("e:ReasonHideFromSearch", namespaces=NS),
            "status": "active_hidden" if hide_from_search else "active",
            "quantity": int(qty_text) if qty_text else None,
            "quantity_sold": int(qty_sold_text) if qty_sold_text else 0,
        })

    total_pages_text = root.findtext(
        ".//e:ActiveList/e:PaginationResult/e:TotalNumberOfPages", namespaces=NS
    )
    total_pages = int(total_pages_text) if total_pages_text else 1

    return items, total_pages


def _price(item_el) -> float | None:
    price_text = item_el.findtext("e:SellingStatus/e:CurrentPrice", namespaces=NS)
    try:
        return float(price_text) if price_text else None
    except (ValueError, TypeError):
        return None


def _upsert(items: list[dict]) -> None:
    if not items:
        return
    now = datetime.now(timezone.utc)
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            for item in items:
                cur.execute(
                    """
                    INSERT INTO listing_metadata (
                        listing_id, title, sku, current_price,
                        status, hide_from_search, hide_reason,
                        quantity, quantity_sold,
                        last_synced_at, updated_at
                    ) VALUES (
                        %(listing_id)s, %(title)s, %(sku)s, %(current_price)s,
                        %(status)s, %(hide_from_search)s, %(hide_reason)s,
                        %(quantity)s, %(quantity_sold)s,
                        %(now)s, %(now)s
                    )
                    ON CONFLICT (listing_id) DO UPDATE SET
                        title            = EXCLUDED.title,
                        sku              = EXCLUDED.sku,
                        current_price    = EXCLUDED.current_price,
                        status           = EXCLUDED.status,
                        hide_from_search = EXCLUDED.hide_from_search,
                        hide_reason      = EXCLUDED.hide_reason,
                        quantity         = EXCLUDED.quantity,
                        quantity_sold    = EXCLUDED.quantity_sold,
                        last_synced_at   = EXCLUDED.last_synced_at,
                        updated_at       = EXCLUDED.updated_at
                    """,
                    {**item, "now": now},
                )
    finally:
        conn.close()


def _mark_ended(active_items: list[dict]) -> None:
    """Flip status to 'ended' for any listing in the DB not returned by the active list."""
    active_ids = [item["listing_id"] for item in active_items]
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE listing_metadata
                SET status = 'ended', updated_at = NOW()
                WHERE listing_id != ALL(%s)
                  AND status != 'ended'
                """,
                (active_ids,),
            )
            if cur.rowcount:
                print(f"[sync_listings] marked {cur.rowcount} listings as ended")
    finally:
        conn.close()


if __name__ == "__main__":
    sync()
