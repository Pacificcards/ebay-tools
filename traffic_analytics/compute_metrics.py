"""Read raw tables, compute metrics, upsert into listing_metrics_computed.

Only the trailing LOOKBACK_DAYS are recomputed each run (older rows don't change; orders
arrive at most a couple of days late). Set COMPUTE_FULL=1 to rebuild the whole history,
e.g. after a formula change.
"""
import os
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

from shared.db import get_connection

LOOKBACK_DAYS = 14


def compute(full: bool = False) -> None:
    cutoff = date.min if full else date.today() - timedelta(days=LOOKBACK_DAYS)
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO listing_metrics_computed (
                    listing_id, date,
                    ctr_ebay_search_page,
                    ctr_calculated,
                    orders, quantity, revenue,
                    conversion_rate,
                    units_per_view, units_per_1k_impr
                )
                SELECT
                    r.listing_id,
                    r.date,
                    r.ctr_ebay_search_page,
                    CASE WHEN r.impressions_all_sources > 0
                         THEN ROUND(COALESCE(r.views_total, 0)::NUMERIC / r.impressions_all_sources, 4)
                    END                                                             AS ctr_calculated,
                    COALESCE(o.orders, 0)                                          AS orders,
                    COALESCE(o.quantity, 0)                                        AS quantity,
                    COALESCE(o.revenue, 0)                                         AS revenue,
                    CASE WHEN r.views_total > 0
                         THEN ROUND(COALESCE(o.orders, 0)::NUMERIC / r.views_total, 6)
                    END                                                             AS conversion_rate,
                    CASE WHEN r.views_total > 0
                         THEN ROUND(COALESCE(o.quantity, 0)::NUMERIC / r.views_total, 6)
                    END                                                             AS units_per_view,
                    CASE WHEN r.impressions_all_sources > 0
                         THEN ROUND(COALESCE(o.quantity, 0)::NUMERIC / r.impressions_all_sources * 1000, 4)
                    END                                                             AS units_per_1k_impr
                FROM listing_metrics_raw r
                LEFT JOIN (
                    SELECT
                        listing_id,
                        order_date                                       AS date,
                        COUNT(DISTINCT SPLIT_PART(order_id, '_', 1))    AS orders,
                        SUM(quantity)                                    AS quantity,
                        SUM(sale_price)                                  AS revenue
                    FROM orders_raw
                    WHERE order_date >= %s
                    GROUP BY listing_id, order_date
                ) o USING (listing_id, date)
                WHERE r.date >= %s
                ON CONFLICT (listing_id, date) DO UPDATE SET
                    ctr_ebay_search_page = EXCLUDED.ctr_ebay_search_page,
                    ctr_calculated   = EXCLUDED.ctr_calculated,
                    orders           = EXCLUDED.orders,
                    quantity         = EXCLUDED.quantity,
                    revenue          = EXCLUDED.revenue,
                    conversion_rate  = EXCLUDED.conversion_rate,
                    units_per_view   = EXCLUDED.units_per_view,
                    units_per_1k_impr = EXCLUDED.units_per_1k_impr,
                    computed_at      = NOW()
            """, (cutoff, cutoff))
            rowcount = cur.rowcount
        print(f"[compute_metrics] upserted {rowcount} rows into listing_metrics_computed")
    finally:
        conn.close()


if __name__ == "__main__":
    compute(full=bool(os.environ.get("COMPUTE_FULL")))
