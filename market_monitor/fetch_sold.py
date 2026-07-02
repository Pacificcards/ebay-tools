"""Fetch sold eBay listings from sold-comps.com and upsert into market_sold_items.

Run weekly. For each active query, calls the sold-comps.com /v1/scrape endpoint
and stores completed listing data for 30-day sold price analysis.
"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

from market_monitor.fetch_market import _LOT_RE
from market_monitor.sheets import load_queries
from shared.db import get_connection

_API_BASE = "https://api.sold-comps.com/v1/scrape"
_PT = ZoneInfo("America/Los_Angeles")


def _is_lot(title: str) -> bool:
    return bool(_LOT_RE.search(title))


def _parse_ended_at(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(_PT)
    except ValueError:
        return None


def _f(val) -> float | None:
    try:
        return float(val) if val is not None and val != "" else None
    except (TypeError, ValueError):
        return None


def _fetch_sold(api_key: str, query: dict) -> list[dict]:
    params: dict = {
        "keyword":      query["query"],
        "itemLocation": "domestic",
        "count":        240,
    }
    if query.get("category_id"):
        params["categoryId"] = query["category_id"]
    if query.get("min_price") is not None:
        params["minPrice"] = query["min_price"]
    if query.get("max_price") is not None:
        params["maxPrice"] = query["max_price"]

    resp = requests.get(
        _API_BASE,
        headers={"Authorization": f"Bearer {api_key}"},
        params=params,
        timeout=30,
    )
    if not resp.ok:
        print(f"    HTTP {resp.status_code}: {resp.text[:300]}")
        return []
    data = resp.json()
    if isinstance(data, list):
        return data
    return data.get("items", data.get("results", []))


def _upsert(conn, query_id: str, items: list[dict]) -> None:
    if not items:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO market_sold_items
                (query_id, item_id, title, sold_price, shipping_price, total_price,
                 buying_format, condition, ended_at, url, bid_count,
                 seller_username, seller_positive_pct, seller_feedback_score)
            VALUES %s
            ON CONFLICT (query_id, item_id) DO UPDATE SET
                title                 = EXCLUDED.title,
                sold_price            = EXCLUDED.sold_price,
                shipping_price        = EXCLUDED.shipping_price,
                total_price           = EXCLUDED.total_price,
                buying_format         = EXCLUDED.buying_format,
                condition             = EXCLUDED.condition,
                ended_at              = EXCLUDED.ended_at,
                url                   = EXCLUDED.url,
                bid_count             = EXCLUDED.bid_count,
                seller_username       = EXCLUDED.seller_username,
                seller_positive_pct   = EXCLUDED.seller_positive_pct,
                seller_feedback_score = EXCLUDED.seller_feedback_score,
                fetched_at            = NOW()
            """,
            [
                (
                    query_id,
                    str(item.get("itemId") or ""),
                    item.get("title"),
                    _f(item.get("soldPrice")),
                    _f(item.get("shippingPrice")),
                    _f(item.get("totalPrice")),
                    item.get("buyingFormat"),
                    item.get("condition"),
                    _parse_ended_at(str(item.get("endedAt") or "")),
                    item.get("url"),
                    item.get("bidCount"),
                    item.get("sellerUsername"),
                    _f(item.get("sellerPositivePercent")),
                    item.get("sellerFeedbackScore"),
                )
                for item in items
            ],
        )


def run() -> None:
    api_key    = os.environ.get("SOLD_COMPS_API_KEY", "").strip()
    sheet_id   = os.environ["MARKET_MONITOR_SHEET_ID"]
    creds_json = os.environ["GOOGLE_SHEETS_CREDENTIALS"]

    if not api_key:
        print("[fetch_sold] ERROR: SOLD_COMPS_API_KEY not set")
        sys.exit(1)

    queries = load_queries(sheet_id, creds_json)
    if not queries:
        print("[fetch_sold] No active queries found.")
        sys.exit(0)

    print(f"[fetch_sold] {len(queries)} active queries loaded")

    conn = get_connection()
    total_upserted = 0
    try:
        for q in queries:
            print(f"  Fetching sold: {q['name']} ...", end=" ", flush=True)
            raw_items = _fetch_sold(api_key, q)
            items = [i for i in raw_items if not _is_lot(str(i.get("title") or ""))]
            filtered = len(raw_items) - len(items)
            print(f"{len(items)} sold ({filtered} filtered)")

            with conn:
                _upsert(conn, q["id"], items)
            total_upserted += len(items)
    finally:
        conn.close()

    print(f"[fetch_sold] Done. {len(queries)} queries, {total_upserted} sold listings upserted.")


if __name__ == "__main__":
    run()
