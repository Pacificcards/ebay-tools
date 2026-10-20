"""
TEMPORARY read-only diagnostic — verify the corrected gross_sale refund
netting against order 17-15240-30930 by running the actual fetch_sales()
query logic for just this order. No writes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shared.db import get_connection

ORDER_ID = "17-15240-30930"

Q = """
SELECT
    o.order_id,
    o.sale_price,
    o.shipping_price,
    f.amount AS order_net_payout,
    rf.total_refund,
    CASE
        WHEN rf.total_refund IS NULL THEN (o.sale_price + COALESCE(o.shipping_price, 0))
        WHEN f.amount IS NULL OR f.amount = 0 THEN (o.sale_price + COALESCE(o.shipping_price, 0))
        ELSE (o.sale_price + COALESCE(o.shipping_price, 0)) - ROUND(
            rf.total_refund * (o.sale_price + COALESCE(o.shipping_price, 0)) / f.amount,
        2)
    END AS corrected_gross_sale
FROM orders_raw o
LEFT JOIN order_fees f
    ON f.order_id = SPLIT_PART(o.order_id, '_', 1)
    AND f.booking_entry = 'CREDIT' AND f.fee_type = 'SALE'
LEFT JOIN (
    SELECT order_id, SUM(amount) AS total_refund
    FROM order_fees
    WHERE fee_type IN ('REFUND', 'CREDIT', 'ADJUSTMENT')
    GROUP BY order_id
) rf ON rf.order_id = SPLIT_PART(o.order_id, '_', 1)
WHERE SPLIT_PART(o.order_id, '_', 1) = %s;
"""


def main():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(Q, (ORDER_ID,))
            for row in cur.fetchall():
                print(row)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
