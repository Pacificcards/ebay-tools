"""Read Supabase and write docs/traffic/traffic_data.json for the GitHub Pages dashboard."""
import json
import os
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

from shared.db import get_connection

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "traffic")
LOOKBACK_DAYS = 30


def generate() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(date) FROM listing_metrics_raw")
            (max_date,) = cur.fetchone()
            if not max_date:
                print("[generate_dashboard] No traffic data found.")
                return
            window_start = max_date - timedelta(days=LOOKBACK_DAYS - 1)

            cur.execute("""
                SELECT lm.listing_id, lm.title, lm.current_price,
                       COALESCE(lm.quantity, 0) - COALESCE(lm.quantity_sold, 0) AS qty_remaining,
                       lm.category_name,
                       r.date, r.impressions_all_sources, r.views_total, r.impressions_search_and_store
                FROM listing_metadata lm
                JOIN listing_metrics_raw r ON r.listing_id = lm.listing_id
                WHERE lm.status IN ('active', 'active_hidden')
                  AND r.date >= %s
            """, (window_start,))
            rows = cur.fetchall()

            listings: dict = {}
            for lid, title, price, qty, category, d, impr, views, impr_narrow in rows:
                entry = listings.setdefault(lid, {
                    "title": title,
                    "price": float(price) if price is not None else None,
                    "qty": qty,
                    "category": category,
                    "days": {},
                })
                entry["days"][d.isoformat()] = [impr, views, impr_narrow]

            # Include active listings with no traffic rows yet (e.g. brand new) so they
            # still show up in the table with qty/price/category, just with an empty trend.
            cur.execute("""
                SELECT listing_id, title, current_price,
                       COALESCE(quantity, 0) - COALESCE(quantity_sold, 0) AS qty_remaining,
                       category_name
                FROM listing_metadata
                WHERE status IN ('active', 'active_hidden')
            """)
            for lid, title, price, qty, category in cur.fetchall():
                if lid not in listings:
                    listings[lid] = {
                        "title": title,
                        "price": float(price) if price is not None else None,
                        "qty": qty,
                        "category": category,
                        "days": {},
                    }

            cur.execute("""
                SELECT order_date::text,
                       COUNT(DISTINCT SPLIT_PART(order_id, '_', 1)) AS orders,
                       SUM(quantity) AS qty,
                       SUM(sale_price) AS revenue
                FROM orders_raw
                WHERE order_date >= %s
                GROUP BY order_date
            """, (max_date - timedelta(days=1),))
            sales = {
                row[0]: {"orders": row[1], "qty": int(row[2] or 0), "revenue": float(row[3] or 0)}
                for row in cur.fetchall()
            }
    finally:
        conn.close()

    data = {"listings": listings, "sales": sales, "generated_at": date.today().isoformat()}

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "traffic_data.json")
    with open(out_path, "w") as f:
        json.dump(data, f)

    print(f"[generate_dashboard] Wrote traffic_data.json "
          f"({len(listings)} listings, data through {max_date})")


if __name__ == "__main__":
    generate()
