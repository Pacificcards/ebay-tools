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
                       lm.category_name, lm.start_time,
                       r.date, r.impressions_search, r.views_search,
                       -- Non-search views = store + direct + off-eBay + other-eBay. NULL if any
                       -- part is missing (older rows), so a gap shows blank, not a too-low sum.
                       CASE WHEN r.views_store IS NOT NULL AND r.views_direct IS NOT NULL
                                 AND r.views_off_ebay IS NOT NULL AND r.views_other_ebay IS NOT NULL
                            THEN r.views_store + r.views_direct + r.views_off_ebay + r.views_other_ebay
                       END
                FROM listing_metadata lm
                JOIN listing_metrics_raw r ON r.listing_id = lm.listing_id
                WHERE lm.status IN ('active', 'active_hidden')
                  AND r.date >= %s
            """, (window_start,))
            rows = cur.fetchall()

            listings: dict = {}
            for lid, title, price, qty, category, start, d, impr, views, non_search in rows:
                entry = listings.setdefault(lid, {
                    "title": title,
                    "price": float(price) if price is not None else None,
                    "qty": qty,
                    "category": category,
                    "start": start.isoformat() if start else None,
                    "days": {},
                })
                # [search impressions, search views, non-search views]
                entry["days"][d.isoformat()] = [impr, views, non_search]

            # Include active listings with no traffic rows yet (e.g. brand new) so they
            # still show up in the table with qty/price/category, just with an empty trend.
            cur.execute("""
                SELECT listing_id, title, current_price,
                       COALESCE(quantity, 0) - COALESCE(quantity_sold, 0) AS qty_remaining,
                       category_name, start_time
                FROM listing_metadata
                WHERE status IN ('active', 'active_hidden')
            """)
            for lid, title, price, qty, category, start in cur.fetchall():
                if lid not in listings:
                    listings[lid] = {
                        "title": title,
                        "price": float(price) if price is not None else None,
                        "qty": qty,
                        "category": category,
                        "start": start.isoformat() if start else None,
                        "days": {},
                    }

            # The analytics sync time: the dashboard uses it to hold back listings that went
            # live less than 24h before it (see Worst Performers).
            cur.execute("SELECT MAX(last_synced_at) FROM listing_metadata")
            (synced_at,) = cur.fetchone()

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

    data = {
        "listings": listings,
        "sales": sales,
        "generated_at": date.today().isoformat(),
        "synced_at": synced_at.isoformat() if synced_at else None,
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "traffic_data.json")
    with open(out_path, "w") as f:
        json.dump(data, f)

    print(f"[generate_dashboard] Wrote traffic_data.json "
          f"({len(listings)} listings, data through {max_date})")


if __name__ == "__main__":
    generate()
