"""
TEMPORARY read-only diagnostic — full detail for one order, to investigate
a user-reported discrepancy (gross_sale=97.46, net_payout=722.49 on order
17-15240-30930 after the cancellation-netting fix). No writes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shared.db import get_connection

ORDER_ID = "17-15240-30930"

Q_ORDERS_RAW = """
SELECT order_id, listing_id, order_date, quantity, sale_price, shipping_price, title
FROM orders_raw
WHERE SPLIT_PART(order_id, '_', 1) = %s
ORDER BY order_id;
"""

Q_ALL_FEES = """
SELECT billing_transaction_id, transaction_date, fee_type, booking_entry, amount, order_id, listing_id
FROM order_fees
WHERE order_id = %s
ORDER BY transaction_date;
"""


def main():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            print(f"=== orders_raw rows for base order {ORDER_ID} ===")
            cur.execute(Q_ORDERS_RAW, (ORDER_ID,))
            for row in cur.fetchall():
                print(row)

            print(f"\n=== ALL order_fees rows for order_id = {ORDER_ID} ===")
            cur.execute(Q_ALL_FEES, (ORDER_ID,))
            for row in cur.fetchall():
                print(row)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
