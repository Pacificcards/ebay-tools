"""Fetch buyer purchase history from eBay Trading API (GetMyeBayBuying WonList).

Upserts into ebay_purchases_raw, then inserts any new records into import_queue
as 'pending' items for reconciliation.

Usage:
  python -m analytics.fetch_ebay_purchases            # 14-day window (weekly runs)
  python -m analytics.fetch_ebay_purchases --backfill  # 60-day window (one-time historical pull)
"""
import os
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone

import requests

from shared.db import get_connection
from shared.ebay_auth import get_access_token

TRADING_API_URL = "https://api.ebay.com/ws/api.dll"
NS = {"e": "urn:ebay:apis:eBLBaseComponents"}
PAGE_SIZE = 200

BACKFILL_DAYS = 60
REGULAR_DAYS = 14


def fetch_and_store(duration_days: int) -> None:
    token = get_access_token(
        os.environ["EBAY_CLIENT_ID"],
        os.environ["EBAY_CLIENT_SECRET"],
        os.environ["EBAY_REFRESH_TOKEN"],
    )

    print(f"[fetch_ebay_purchases] fetching last {duration_days} days of purchases...")
    purchases = _fetch_all(token, duration_days)
    print(f"[fetch_ebay_purchases] fetched {len(purchases)} purchases from eBay")

    if not purchases:
        print("[fetch_ebay_purchases] no purchases found, nothing to upsert")
        return

    new_refs = _upsert_raw(purchases)
    print(f"[fetch_ebay_purchases] upserted {len(purchases)} rows to ebay_purchases_raw ({len(new_refs)} new)")

    if new_refs:
        _enqueue_new(purchases, new_refs)
        print(f"[fetch_ebay_purchases] added {len(new_refs)} new items to import_queue")
    else:
        print("[fetch_ebay_purchases] no new purchases to enqueue")

    print("[fetch_ebay_purchases] done")


def _fetch_all(token: str, duration_days: int) -> list[dict]:
    all_purchases = []
    page = 1

    while True:
        purchases, total_pages = _fetch_page(token, duration_days, page)
        all_purchases.extend(purchases)
        print(f"[fetch_ebay_purchases] page {page}/{total_pages}: {len(purchases)} purchases")
        if page >= total_pages:
            break
        page += 1

    return all_purchases


def _fetch_page(token: str, duration_days: int, page: int) -> tuple[list[dict], int]:
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetMyeBayBuyingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <WonList>
    <Include>true</Include>
    <DurationInDays>{duration_days}</DurationInDays>
    <Pagination>
      <EntriesPerPage>{PAGE_SIZE}</EntriesPerPage>
      <PageNumber>{page}</PageNumber>
    </Pagination>
  </WonList>
</GetMyeBayBuyingRequest>"""

    headers = {
        "X-EBAY-API-CALL-NAME": "GetMyeBayBuying",
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-IAF-TOKEN": token,
        "Content-Type": "text/xml",
    }

    response = requests.post(TRADING_API_URL, data=body.encode("utf-8"), headers=headers)
    if not response.ok:
        print(f"[fetch_ebay_purchases] HTTP {response.status_code}: {response.text}")
        response.raise_for_status()

    return _parse(response.text)


def _parse(xml_text: str) -> tuple[list[dict], int]:
    root = ET.fromstring(xml_text)

    ack = root.findtext("e:Ack", namespaces=NS)
    if ack not in ("Success", "Warning"):
        for err in root.findall(".//e:Errors", namespaces=NS):
            print(f"[fetch_ebay_purchases] eBay error: {err.findtext('e:LongMessage', namespaces=NS)}")
        raise RuntimeError(f"GetMyeBayBuying returned Ack={ack}")

    purchases = []
    for txn_el in root.findall(
        ".//e:WonList/e:OrderTransactionArray/e:OrderTransaction/e:Transaction",
        namespaces=NS,
    ):
        item_el = txn_el.find("e:Item", namespaces=NS)
        if item_el is None:
            continue

        ebay_item_id = item_el.findtext("e:ItemID", namespaces=NS)
        transaction_id = txn_el.findtext("e:TransactionID", namespaces=NS) or "0"
        if not ebay_item_id:
            continue

        item_cost = _decimal(txn_el.findtext("e:TotalTransactionPrice", namespaces=NS))
        total_price = _decimal(txn_el.findtext("e:TotalPrice", namespaces=NS))
        shipping_cost = (
            round(total_price - item_cost, 2)
            if item_cost is not None and total_price is not None
            else None
        )
        total_cost = total_price if total_price is not None else item_cost

        raw_date = txn_el.findtext("e:CreatedDate", namespaces=NS)
        purchase_date = _parse_date(raw_date)

        purchases.append({
            "ebay_item_id":  ebay_item_id,
            "transaction_id": transaction_id,
            "title":         item_el.findtext("e:Title", namespaces=NS),
            "seller_id":     item_el.findtext("e:Seller/e:UserID", namespaces=NS),
            "purchase_date": purchase_date,
            "quantity":      _int(txn_el.findtext("e:QuantityPurchased", namespaces=NS)) or 1,
            "item_cost":     item_cost,
            "shipping_cost": shipping_cost,
            "total_cost":    total_cost,
        })

    total_pages_text = root.findtext(
        ".//e:WonList/e:PaginationResult/e:TotalNumberOfPages", namespaces=NS
    )
    total_pages = int(total_pages_text) if total_pages_text else 1

    return purchases, total_pages


def _upsert_raw(purchases: list[dict]) -> set[tuple[str, str]]:
    """Upsert into ebay_purchases_raw. Returns set of (ebay_item_id, transaction_id) that were INSERTed (new)."""
    conn = get_connection()
    new_refs: set[tuple[str, str]] = set()
    try:
        with conn, conn.cursor() as cur:
            for p in purchases:
                cur.execute(
                    """
                    INSERT INTO ebay_purchases_raw (
                        ebay_item_id, transaction_id, title, seller_id,
                        purchase_date, quantity, item_cost, shipping_cost, total_cost
                    ) VALUES (
                        %(ebay_item_id)s, %(transaction_id)s, %(title)s, %(seller_id)s,
                        %(purchase_date)s, %(quantity)s, %(item_cost)s, %(shipping_cost)s, %(total_cost)s
                    )
                    ON CONFLICT (ebay_item_id, transaction_id) DO NOTHING
                    """,
                    p,
                )
                if cur.rowcount == 1:
                    new_refs.add((p["ebay_item_id"], p["transaction_id"]))
    finally:
        conn.close()
    return new_refs


def _enqueue_new(purchases: list[dict], new_refs: set[tuple[str, str]]) -> None:
    """Insert new purchases into import_queue as pending items."""
    to_enqueue = [
        p for p in purchases
        if (p["ebay_item_id"], p["transaction_id"]) in new_refs
    ]
    if not to_enqueue:
        return

    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            for p in to_enqueue:
                unit_cost = (
                    round(p["total_cost"] / p["quantity"], 2)
                    if p["total_cost"] is not None and p["quantity"]
                    else p["total_cost"]
                )
                cur.execute(
                    """
                    INSERT INTO import_queue (
                        source, status, purchase_date, description,
                        source_ref, quantity, unit_cost, total_cost
                    ) VALUES (
                        'ebay_purchase', 'pending', %(purchase_date)s, %(description)s,
                        %(source_ref)s, %(quantity)s, %(unit_cost)s, %(total_cost)s
                    )
                    """,
                    {
                        "purchase_date": p["purchase_date"],
                        "description":   p["title"] or f"eBay item {p['ebay_item_id']}",
                        "source_ref":    p["ebay_item_id"],
                        "quantity":      p["quantity"],
                        "unit_cost":     unit_cost,
                        "total_cost":    p["total_cost"],
                    },
                )
    finally:
        conn.close()


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        return None


def _decimal(val: str | None) -> float | None:
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def _int(val: str | None) -> int | None:
    try:
        return int(val) if val is not None else None
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    backfill = "--backfill" in sys.argv
    days = BACKFILL_DAYS if backfill else REGULAR_DAYS
    if backfill:
        print("[fetch_ebay_purchases] BACKFILL MODE: pulling full 60-day history")
    fetch_and_store(days)
