"""Read Supabase and write docs/market/market_data.json for the GitHub Pages dashboard."""
import json
import os
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

from shared.db import get_connection

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "market")


def generate() -> None:
    today          = date.today()
    yesterday      = today - timedelta(days=1)
    window_start   = today - timedelta(days=90)

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

            # Yesterday snapshot for DoD comparison
            cur.execute("""
                SELECT query_id, listing_count, price_median
                FROM market_snapshots WHERE date = %s
            """, (yesterday,))
            yesterday_snap = {
                r[0]: {"listing_count": r[1], "price_median": _f(r[2])}
                for r in cur.fetchall()
            }

            # New items today (first_seen = today)
            cur.execute("""
                SELECT query_id, item_id, title, price, buying_format, url
                FROM market_snapshot_items
                WHERE first_seen = %s
                ORDER BY query_id, price
            """, (today,))
            new_rows = cur.fetchall()

            # Gone items today (last_seen = yesterday = not refreshed today)
            cur.execute("""
                SELECT query_id, item_id, title, price, buying_format, first_seen::text
                FROM market_snapshot_items
                WHERE last_seen = %s
                ORDER BY query_id, price
            """, (yesterday,))
            gone_rows = cur.fetchall()

            # Current prices (last_seen = today, for histogram)
            cur.execute("""
                SELECT query_id, price
                FROM market_snapshot_items
                WHERE last_seen = %s AND price > 0
                ORDER BY query_id, price
            """, (today,))
            price_rows = cur.fetchall()
    finally:
        conn.close()

    # Build queries list (unique query_id + name pairs from trend data)
    seen_queries: dict[str, str] = {}
    for r in trend_rows:
        qid, name = r[0], r[1]
        seen_queries[qid] = name
    queries = [{"id": qid, "name": name} for qid, name in seen_queries.items()]

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
        if r[2] == today.isoformat():
            today_snap[qid] = entry

    # New / gone items by query
    new_items: dict[str, list] = {}
    for r in new_rows:
        new_items.setdefault(r[0], []).append(
            {"item_id": r[1], "title": r[2], "price": _f(r[3]), "buying_format": r[4], "url": r[5]}
        )

    gone_items: dict[str, list] = {}
    for r in gone_rows:
        gone_items.setdefault(r[0], []).append(
            {"item_id": r[1], "title": r[2], "price": _f(r[3]), "buying_format": r[4], "first_seen": r[5]}
        )

    # Current prices by query (for histogram)
    prices: dict[str, list] = {}
    for r in price_rows:
        prices.setdefault(r[0], []).append(float(r[1]))

    data = {
        "generated_at": today.isoformat(),
        "queries":      queries,
        "trends":       trends,
        "today":        today_snap,
        "yesterday":    yesterday_snap,
        "new_items":    new_items,
        "gone_items":   gone_items,
        "prices":       prices,
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "market_data.json")
    with open(out_path, "w") as f:
        json.dump(data, f, default=str)

    n_queries = len(queries)
    n_items   = sum(len(v) for v in prices.values())
    print(f"[generate_dashboard] Wrote market_data.json "
          f"({n_queries} queries, {n_items} current listings, {today.isoformat()})")


def _f(val) -> float | None:
    return float(val) if val is not None else None


if __name__ == "__main__":
    generate()
