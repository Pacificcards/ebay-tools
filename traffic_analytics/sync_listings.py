"""Sync active listings from eBay Trading API into listing_metadata.

Fetches all active listings via GetMyeBaySelling, upserts into listing_metadata,
and marks any previously-known listing not in the response as 'ended'.

Run this before fetch_analytics so the active-listing filter is current.
Requires the base OAuth scope: https://api.ebay.com/oauth/api_scope
"""
import os
from collections import Counter
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from shared.db import get_connection
from shared.ebay_auth import get_access_token
from traffic_analytics.listing_details import (
    DESCRIPTOR_GRADE, DESCRIPTOR_GRADER, DETAILS_VERSION, GRADE_IDS, GRADED_CONDITION_ID, GRADER_IDS,
    fetch_item_xml, parse_details,
)

TRADING_API_URL = "https://api.ebay.com/ws/api.dll"
NS = {"e": "urn:ebay:apis:eBLBaseComponents"}
PAGE_SIZE = 200
# Refuse to sync if the active-listing count falls by more than this fraction versus what's
# already stored: _mark_ended() would otherwise flag everything missing from a bad response as ended.
MAX_ACTIVE_DROP = 0.5


def sync() -> None:
    token = get_access_token(
        os.environ["EBAY_CLIENT_ID"],
        os.environ["EBAY_CLIENT_SECRET"],
        os.environ["EBAY_REFRESH_TOKEN"],
    )

    all_items = _fetch_all_active(token)
    print(f"[sync_listings] fetched {len(all_items)} active listings from eBay")

    _validate_active_count(_count_stored_active(), len(all_items))

    _upsert(all_items)
    _mark_ended(all_items)
    _backfill_details(token)

    print("[sync_listings] done")


def _count_stored_active() -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM listing_metadata WHERE status != 'ended'")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _validate_active_count(stored_active: int, fetched: int) -> None:
    if fetched == 0 and stored_active > 0:
        raise RuntimeError(
            f"GetMyeBaySelling returned 0 active listings but {stored_active} are stored; "
            "refusing to mark them all ended"
        )
    if stored_active >= 20 and fetched < stored_active * (1 - MAX_ACTIVE_DROP):
        raise RuntimeError(
            f"GetMyeBaySelling returned {fetched} active listings, down from {stored_active} stored "
            f"(more than a {MAX_ACTIVE_DROP:.0%} drop); refusing to mark the rest ended"
        )


# Columns filled by the one-time GetItem backfill. Added idempotently so code and schema can't drift.
DETAIL_COLUMNS_DDL = """
    ALTER TABLE listing_metadata
        ADD COLUMN IF NOT EXISTS condition_id      TEXT,
        ADD COLUMN IF NOT EXISTS sport_or_game     TEXT,
        ADD COLUMN IF NOT EXISTS grader            TEXT,
        ADD COLUMN IF NOT EXISTS grade             TEXT,
        ADD COLUMN IF NOT EXISTS details_synced_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS details_version   INTEGER
"""
MAX_DETAIL_FETCHES = 1500


def ensure_detail_columns(conn) -> None:
    with conn, conn.cursor() as cur:
        cur.execute(DETAIL_COLUMNS_DDL)


def _backfill_details(token: str) -> None:
    """Fetch category, sport/game and grader/grade via GetItem for active listings not yet fetched.

    These essentially never change once a listing is published, so this costs one Trading API
    call per listing the first time it's seen -- not on every run.
    """
    conn = get_connection()
    try:
        ensure_detail_columns(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT listing_id FROM listing_metadata WHERE status != 'ended' "
                "AND (category_name IS NULL OR details_synced_at IS NULL OR details_version IS DISTINCT FROM %s) "
                "ORDER BY listing_id LIMIT %s",
                (DETAILS_VERSION, MAX_DETAIL_FETCHES),
            )
            missing_ids = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()

    if not missing_ids:
        return

    print(f"[sync_listings] fetching details for {len(missing_ids)} listing(s)")
    updates = []
    for listing_id in missing_ids:
        xml_text = fetch_item_xml(token, listing_id)
        details = parse_details(xml_text) if xml_text else None
        if details:
            updates.append({**details, "listing_id": listing_id, "version": DETAILS_VERSION})

    if not updates:
        return
    _log_grade_summary(updates)

    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            for d in updates:
                cur.execute(
                    """
                    UPDATE listing_metadata SET
                        category_id = %(category_id)s, category_name = %(category_name)s,
                        condition_id = %(condition_id)s, sport_or_game = %(sport_or_game)s,
                        grader = %(grader)s, grade = %(grade)s, details_synced_at = NOW(),
                        details_version = %(version)s
                    WHERE listing_id = %(listing_id)s
                    """,
                    d,
                )
        print(f"[sync_listings] details stored for {len(updates)} listing(s)")
    finally:
        conn.close()


def _log_grade_summary(updates: list[dict]) -> None:
    """Public listing data only: counts per grader/grade, plus any descriptor IDs not in the maps."""
    graded = [d for d in updates if d.get("condition_id") == GRADED_CONDITION_ID]
    combos = Counter((d["grader"] or "(blank)", d["grade"] or "(blank)") for d in graded)
    print(f"[sync_listings] graded listings fetched: {len(graded)}; grader/grade -> count: {dict(combos)}")
    unknown = Counter(
        (k, v) for d in graded for k, v in d.get("_descriptors", {}).items()
        if (k == DESCRIPTOR_GRADER and v not in GRADER_IDS) or (k == DESCRIPTOR_GRADE and v not in GRADE_IDS)
    )
    if unknown:
        print(f"::warning title=Unmapped grader/grade IDs::{dict(unknown)} - add them to listing_details.py")


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

    response = requests.post(TRADING_API_URL, data=body.encode("utf-8"), headers=headers, timeout=60)
    if not response.ok:
        print(f"[sync_listings] HTTP {response.status_code}: {response.text}")
        response.raise_for_status()

    # Bytes, not response.text: no charset header, so .text would garble UTF-8 titles (é -> Ã©).
    return _parse(response.content)


def _parse(xml_text: str | bytes) -> tuple[list[dict], int]:
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
        start_text = item_el.findtext("e:ListingDetails/e:StartTime", namespaces=NS)

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
            "start_time": _parse_time(start_text),
        })

    total_pages_text = root.findtext(
        ".//e:ActiveList/e:PaginationResult/e:TotalNumberOfPages", namespaces=NS
    )
    total_pages = int(total_pages_text) if total_pages_text else 1

    return items, total_pages


def _parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


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
                        quantity, quantity_sold, start_time,
                        last_synced_at, updated_at
                    ) VALUES (
                        %(listing_id)s, %(title)s, %(sku)s, %(current_price)s,
                        %(status)s, %(hide_from_search)s, %(hide_reason)s,
                        %(quantity)s, %(quantity_sold)s, %(start_time)s,
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
                        start_time       = EXCLUDED.start_time,
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
