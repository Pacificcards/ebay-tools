"""Read Supabase and write docs/market/market_data.json for the GitHub Pages dashboard."""
import json
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from shared.db import get_connection

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "market")


def generate() -> None:
    window_start = datetime.now(ZoneInfo("America/Los_Angeles")).date() - timedelta(days=90)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Trend data: last 90 days, all queries
            cur.execute("""
                SELECT query_id, name, date::text,
                       listing_count, new_count, gone_count,
                       price_min, price_max, price_mean, price_median, price_p25, price_p75
                FROM market_snapshots
                WHERE date >= %s
                ORDER BY query_id, date
            """, (window_start,))
            trend_rows = cur.fetchall()

            # Use the most recent snapshot date as "today" — pipeline stores dates in PT
            # so this stays consistent regardless of when/where the generator runs
            latest_date_str = max((r[2] for r in trend_rows), default=None)
            if not latest_date_str:
                print("[generate_dashboard] No data found.")
                return
            latest_date = date.fromisoformat(latest_date_str)
            prev_date   = latest_date - timedelta(days=1)

            # Previous-day snapshot for DoD comparison
            cur.execute("""
                SELECT query_id, listing_count, price_median
                FROM market_snapshots WHERE date = %s
            """, (prev_date,))
            yesterday_snap = {
                r[0]: {"listing_count": r[1], "price_median": _f(r[2])}
                for r in cur.fetchall()
            }

            # Gone items: last_seen = prev_date (not refreshed in latest run)
            cur.execute("""
                SELECT query_id, item_id, title, price, buying_format, first_seen::text
                FROM market_snapshot_items
                WHERE last_seen = %s
                ORDER BY query_id, price
            """, (prev_date,))
            gone_rows = cur.fetchall()

            # Current BIN listings: last_seen = latest_date, price > 0
            cur.execute("""
                SELECT query_id, title, price, url
                FROM market_snapshot_items
                WHERE last_seen = %s AND buying_format = 'FIXED_PRICE' AND price > 0
                ORDER BY query_id, price
            """, (latest_date,))
            bin_rows = cur.fetchall()

            # Current auction listings: last_seen = latest_date
            cur.execute("""
                SELECT query_id, title, price, url, end_time::text
                FROM market_snapshot_items
                WHERE last_seen = %s AND buying_format = 'AUCTION'
                ORDER BY query_id, end_time NULLS LAST
            """, (latest_date,))
            auction_rows = cur.fetchall()

            # Latest MSRP per query (use MAX to get most recent non-null value)
            cur.execute("""
                SELECT query_id, MAX(msrp)
                FROM market_snapshots
                WHERE msrp IS NOT NULL
                GROUP BY query_id
            """)
            msrp_map: dict[str, float] = {r[0]: float(r[1]) for r in cur.fetchall()}

            # Median price of new BIN listings today (first_seen = latest_date)
            cur.execute("""
                SELECT query_id,
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price)
                FROM market_snapshot_items
                WHERE first_seen = %s AND buying_format = 'FIXED_PRICE' AND price > 0
                GROUP BY query_id
            """, (latest_date,))
            new_median_map: dict[str, float] = {r[0]: float(r[1]) for r in cur.fetchall()}

            # Median price of gone BIN listings (last_seen = prev_date, proxy: sold/ended)
            cur.execute("""
                SELECT query_id,
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price)
                FROM market_snapshot_items
                WHERE last_seen = %s AND buying_format = 'FIXED_PRICE' AND price > 0
                GROUP BY query_id
            """, (prev_date,))
            gone_median_map: dict[str, float] = {r[0]: float(r[1]) for r in cur.fetchall()}

            # Timestamp of the last pipeline run (for computing auction time-remaining)
            cur.execute("""
                SELECT MAX(fetched_at)::text FROM market_snapshots WHERE date = %s
            """, (latest_date,))
            fetched_at_row = cur.fetchone()
            fetched_at = fetched_at_row[0] if fetched_at_row else None
    finally:
        conn.close()

    # Build queries list — only include queries that ran on the latest snapshot date
    # (excludes deactivated queries that have historical rows but didn't run in the latest batch)
    seen_queries: dict[str, str] = {}
    for r in trend_rows:
        qid, name, row_date = r[0], r[1], r[2]
        if row_date == latest_date_str:
            seen_queries[qid] = name
    queries = [{"id": qid, "name": name, "msrp": msrp_map.get(qid)} for qid, name in seen_queries.items()]

    # Trend data by query
    trends: dict[str, list] = {}
    today_snap: dict[str, dict] = {}
    for r in trend_rows:
        qid = r[0]
        entry = {
            "date": r[2], "listing_count": r[3], "new_count": r[4], "gone_count": r[5],
            "price_min": _f(r[6]), "price_max": _f(r[7]), "price_mean": _f(r[8]),
            "price_median": _f(r[9]), "price_p25": _f(r[10]), "price_p75": _f(r[11]),
        }
        trends.setdefault(qid, []).append(entry)
        if r[2] == latest_date_str:
            today_snap[qid] = entry

    # Gone items by query
    gone_items: dict[str, list] = {}
    for r in gone_rows:
        gone_items.setdefault(r[0], []).append(
            {"item_id": r[1], "title": r[2], "price": _f(r[3]), "buying_format": r[4], "first_seen": r[5]}
        )

    # Current BIN listings by query (also used for histogram)
    bin_listings: dict[str, list] = {}
    for r in bin_rows:
        bin_listings.setdefault(r[0], []).append(
            {"title": r[1], "price": _f(r[2]), "url": r[3]}
        )

    # Current auction listings by query
    auction_listings: dict[str, list] = {}
    for r in auction_rows:
        auction_listings.setdefault(r[0], []).append(
            {"title": r[1], "price": _f(r[2]), "url": r[3], "end_time": r[4]}
        )

    # Prices list (floats) derived from BIN listings for histogram
    prices: dict[str, list] = {
        qid: [item["price"] for item in items if item["price"] is not None]
        for qid, items in bin_listings.items()
    }

    data = {
        "generated_at":    latest_date_str,
        "fetched_at":      fetched_at,
        "queries":         queries,
        "trends":          trends,
        "today":           today_snap,
        "yesterday":       yesterday_snap,
        "new_medians":     new_median_map,
        "gone_medians":    gone_median_map,
        "gone_items":      gone_items,
        "bin_listings":    bin_listings,
        "auction_listings": auction_listings,
        "prices":          prices,
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "market_data.json")
    with open(out_path, "w") as f:
        json.dump(data, f, default=str)

    n_queries  = len(queries)
    n_bin      = sum(len(v) for v in bin_listings.values())
    n_auctions = sum(len(v) for v in auction_listings.values())
    print(f"[generate_dashboard] Wrote market_data.json "
          f"({n_queries} queries, {n_bin} BIN + {n_auctions} auction listings, {latest_date_str})")


def _f(val) -> float | None:
    return float(val) if val is not None else None


if __name__ == "__main__":
    generate()
