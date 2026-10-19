"""
TEMPORARY read-only diagnostic — full list of order-linked REFUND/CREDIT/
ADJUSTMENT transactions that would be netted against revenue under the
proposed cancellation-revenue fix. No writes. Safe to delete after use.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shared.db import get_connection

Q_FULL_LIST = """
SELECT
    f.transaction_date,
    f.order_id,
    STRING_AGG(DISTINCT o.title, ' / ') AS titles,
    STRING_AGG(DISTINCT COALESCE(o.group_name, ''), ' / ') AS groups,
    f.fee_type,
    f.booking_entry,
    f.amount,
    f.billing_transaction_id,
    COUNT(o.*) AS matched_line_items
FROM order_fees f
JOIN orders_raw o ON SPLIT_PART(o.order_id, '_', 1) = f.order_id
WHERE f.fee_type IN ('REFUND', 'CREDIT', 'ADJUSTMENT')
  AND f.order_id IS NOT NULL
GROUP BY f.transaction_date, f.order_id, f.fee_type, f.booking_entry, f.amount, f.billing_transaction_id
ORDER BY f.transaction_date;
"""


def main():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(Q_FULL_LIST)
            rows = cur.fetchall()
            print(f"=== Full list: {len(rows)} matched transactions ===")
            print("date | order_id | titles | groups | fee_type | booking_entry | amount | txn_id | matched_line_items")
            for row in rows:
                print(" | ".join(str(c) for c in row))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
