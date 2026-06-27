"""Fetch active eBay listings for each monitored query and snapshot to Supabase.

Run daily. Each run:
  1. Reads query config from Google Sheet ("pcc_sealed_monitor" → "Queries" tab).
  2. For each active query, fetches all matching active eBay listings via Browse API.
  3. Upserts listings into market_snapshot_items (tracks first_seen / last_seen per listing).
  4. Computes aggregate stats and upserts into market_snapshots.
"""
import os
import re
import sys
from datetime import date, timedelta
from statistics import mean, median, quantiles

from psycopg2.extras import execute_values

from dotenv import load_dotenv

load_dotenv()

from listener.ebay import get_app_token, search_all_listings
from market_monitor.sheets import load_queries
from shared.db import get_connection

# Matches multi-unit lot listings by title. Covers the most common seller patterns.
_LOT_RE = re.compile(
    r'\b\d+\s*[x×]\b'                               # 2x, 3 x, 10x
    r'|\b[x×]\s*\d+\b'                               # x2, x3
    r'|\blot\s+of\s+\d+'                             # lot of 2
    r'|\b([2-9]|\d{2,})\s+(booster\s+)?box(es)?\b'   # 2 boxes, 3 booster boxes (not "1 box")
    r'|\bbundle\b'                                   # bundle
    r'|\bcase\s+of\s+\d+'                           # case of 6
    r'|\bpack\s+of\s+\d+'                           # pack of 3
    r'|\bset\s+of\s+\d+'                            # set of 2
    r'|\bqty\s*:?\s*[2-9]',                         # qty 2, qty: 3
    re.IGNORECASE,
)


def _is_lot(title: str) -> bool:
    return bool(_LOT_RE.search(title))


def _get_yesterday_ids(conn, query_id: str, yesterday: date) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT item_id FROM market_snapshot_items WHERE query_id = %s AND last_seen = %s",
            (query_id, yesterday),
        )
        return {row[0] for row in cur.fetchall()}


def _upsert_items(conn, query_id: str, today: date, items: list[dict]) -> None:
    if not items:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO market_snapshot_items
                (query_id, item_id, title, price, buying_format, url, first_seen, last_seen)
            VALUES %s
            ON CONFLICT (query_id, item_id) DO UPDATE SET
                price         = EXCLUDED.price,
                title         = EXCLUDED.title,
                buying_format = EXCLUDED.buying_format,
                url           = EXCLUDED.url,
                last_seen     = EXCLUDED.last_seen
            """,
            [
                (query_id, item["item_id"], item["title"], item["price"],
                 item["buying_format"], item["url"], today, today)
                for item in items
            ],
        )


def _price_stats(prices: list[float]) -> dict:
    if not prices:
        return {k: None for k in ["price_min", "price_max", "price_mean", "price_median", "price_p25", "price_p75"]}
    s = sorted(prices)
    if len(s) >= 2:
        q = quantiles(s, n=4)
        p25, p75 = q[0], q[2]
    else:
        p25 = p75 = s[0]
    return {
        "price_min":    round(s[0], 2),
        "price_max":    round(s[-1], 2),
        "price_mean":   round(mean(s), 2),
        "price_median": round(median(s), 2),
        "price_p25":    round(p25, 2),
        "price_p75":    round(p75, 2),
    }


def _upsert_snapshot(conn, query_id: str, name: str, today: date,
                     listing_count: int, new_count: int, gone_count: int,
                     stats: dict, msrp: float | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO market_snapshots
                (query_id, name, date, listing_count, new_count, gone_count,
                 price_min, price_max, price_mean, price_median, price_p25, price_p75, msrp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (query_id, date) DO UPDATE SET
                name          = EXCLUDED.name,
                listing_count = EXCLUDED.listing_count,
                new_count     = EXCLUDED.new_count,
                gone_count    = EXCLUDED.gone_count,
                price_min     = EXCLUDED.price_min,
                price_max     = EXCLUDED.price_max,
                price_mean    = EXCLUDED.price_mean,
                price_median  = EXCLUDED.price_median,
                price_p25     = EXCLUDED.price_p25,
                price_p75     = EXCLUDED.price_p75,
                msrp          = EXCLUDED.msrp,
                fetched_at    = NOW()
            """,
            (query_id, name, today, listing_count, new_count, gone_count,
             stats["price_min"], stats["price_max"], stats["price_mean"],
             stats["price_median"], stats["price_p25"], stats["price_p75"], msrp),
        )


def run() -> None:
    sheet_id   = os.environ["MARKET_MONITOR_SHEET_ID"]
    creds_json = os.environ["GOOGLE_SHEETS_CREDENTIALS"]
    client_id  = os.environ["EBAY_CLIENT_ID"]
    client_secret = os.environ["EBAY_CLIENT_SECRET"]

    queries = load_queries(sheet_id, creds_json)
    if not queries:
        print("[market_monitor] No active queries found in sheet.")
        sys.exit(0)

    print(f"[market_monitor] {len(queries)} active queries loaded")
    token     = get_app_token(client_id, client_secret)
    today     = date.today()
    yesterday = today - timedelta(days=1)

    conn = get_connection()
    try:
        for q in queries:
            cat_id = q.get("category_id")
            if cat_id:
                print(f"  Category ID: {cat_id}")

            print(f"  Fetching: {q['name']} ...", end=" ", flush=True)
            raw_items = search_all_listings(
                token, q["query"],
                category_id=cat_id,
                min_price=q["min_price"],
                max_price=q["max_price"],
            )
            items = [item for item in raw_items if not _is_lot(item["title"])]
            lot_count = len(raw_items) - len(items)
            print(f"{len(items)} listings ({lot_count} lots filtered)")

            # BIN-only for price stats; all formats for supply counts
            bin_items = [item for item in items if item["buying_format"] == "FIXED_PRICE"]

            yesterday_ids = _get_yesterday_ids(conn, q["id"], yesterday)
            today_ids     = {item["item_id"] for item in items}
            gone_count    = len(yesterday_ids - today_ids)
            new_count     = len(today_ids - yesterday_ids)

            with conn:
                _upsert_items(conn, q["id"], today, items)
                prices = [item["price"] for item in bin_items if item["price"] > 0]
                stats  = _price_stats(prices)
                _upsert_snapshot(conn, q["id"], q["name"], today,
                                 len(items), new_count, gone_count, stats, q.get("msrp"))

            auctions = len(items) - len(bin_items)
            print(f"    → new={new_count}, gone={gone_count}, "
                  f"bin={len(bin_items)}, auctions={auctions}, "
                  f"median=${stats['price_median']} (BIN only)")
    finally:
        conn.close()

    print("[market_monitor] Done.")


if __name__ == "__main__":
    run()
