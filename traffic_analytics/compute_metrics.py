"""Read raw tables, compute metrics, upsert into listing_metrics_computed."""
from dotenv import load_dotenv

load_dotenv()

from shared.db import get_connection


def compute() -> None:
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO listing_metrics_computed (
                    listing_id, date,
                    ctr,
                    orders, quantity, revenue,
                    conversion_rate,
                    units_per_view, units_per_1k_impr
                )
                SELECT
                    r.listing_id,
                    r.date,
                    r.ctr,
                    COALESCE(o.orders, 0)                                          AS orders,
                    COALESCE(o.quantity, 0)                                        AS quantity,
                    COALESCE(o.revenue, 0)                                         AS revenue,
                    CASE WHEN r.views_total > 0
                         THEN ROUND(COALESCE(o.orders, 0)::NUMERIC / r.views_total, 6)
                    END                                                             AS conversion_rate,
                    CASE WHEN r.views_total > 0
                         THEN ROUND(COALESCE(o.quantity, 0)::NUMERIC / r.views_total, 6)
                    END                                                             AS units_per_view,
                    CASE WHEN r.impressions_total > 0
                         THEN ROUND(COALESCE(o.quantity, 0)::NUMERIC / r.impressions_total * 1000, 4)
                    END                                                             AS units_per_1k_impr
                FROM listing_metrics_raw r
                LEFT JOIN (
                    SELECT
                        listing_id,
                        order_date          AS date,
                        COUNT(*)            AS orders,
                        SUM(quantity)       AS quantity,
                        SUM(sale_price)     AS revenue
                    FROM orders_raw
                    GROUP BY listing_id, order_date
                ) o USING (listing_id, date)
                ON CONFLICT (listing_id, date) DO UPDATE SET
                    ctr              = EXCLUDED.ctr,
                    orders           = EXCLUDED.orders,
                    quantity         = EXCLUDED.quantity,
                    revenue          = EXCLUDED.revenue,
                    conversion_rate  = EXCLUDED.conversion_rate,
                    units_per_view   = EXCLUDED.units_per_view,
                    units_per_1k_impr = EXCLUDED.units_per_1k_impr,
                    computed_at      = NOW()
            """)
            rowcount = cur.rowcount
        print(f"[compute_metrics] upserted {rowcount} rows into listing_metrics_computed")
    finally:
        conn.close()


if __name__ == "__main__":
    compute()
