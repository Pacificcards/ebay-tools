"""Read raw tables, compute metrics, upsert into listing_metrics_computed."""
from dotenv import load_dotenv

load_dotenv()

from shared.db import get_connection


def compute() -> None:
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO listing_metrics_computed
                    (listing_id, date, ctr, orders_per_1k_impr, revenue_per_impression,
                     conversion_rate, orders, revenue)
                SELECT
                    r.listing_id,
                    r.date,
                    CASE WHEN r.impressions > 0
                         THEN ROUND(r.clicks::NUMERIC / r.impressions, 4) END           AS ctr,
                    CASE WHEN r.impressions > 0
                         THEN ROUND(COALESCE(o.orders, 0)::NUMERIC / r.impressions * 1000, 4) END
                                                                                          AS orders_per_1k_impr,
                    CASE WHEN r.impressions > 0
                         THEN ROUND(COALESCE(o.revenue, 0) / r.impressions, 6) END       AS revenue_per_impression,
                    CASE WHEN r.page_views > 0
                         THEN ROUND(COALESCE(o.orders, 0)::NUMERIC / r.page_views, 4) END
                                                                                          AS conversion_rate,
                    COALESCE(o.orders, 0)                                                 AS orders,
                    COALESCE(o.revenue, 0)                                                AS revenue
                FROM listing_metrics_raw r
                LEFT JOIN (
                    SELECT
                        listing_id,
                        order_date AS date,
                        COUNT(*)           AS orders,
                        SUM(sale_price)    AS revenue
                    FROM orders_raw
                    GROUP BY listing_id, order_date
                ) o USING (listing_id, date)
                ON CONFLICT (listing_id, date) DO UPDATE SET
                    ctr                    = EXCLUDED.ctr,
                    orders_per_1k_impr     = EXCLUDED.orders_per_1k_impr,
                    revenue_per_impression = EXCLUDED.revenue_per_impression,
                    conversion_rate        = EXCLUDED.conversion_rate,
                    orders                 = EXCLUDED.orders,
                    revenue                = EXCLUDED.revenue,
                    computed_at            = NOW()
            """)
            rowcount = cur.rowcount
        print(f"[compute_metrics] upserted {rowcount} rows into listing_metrics_computed")
    finally:
        conn.close()


if __name__ == "__main__":
    compute()
