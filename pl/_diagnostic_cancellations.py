"""
TEMPORARY read-only diagnostic — estimates the scope of the cancellation/
revenue-overstatement fix (order-linked REFUND/CREDIT/ADJUSTMENT transactions
that currently net $0 profit via the Adjustments column, but are NOT netted
out of gross_sale/net_payout on the Sales/Monthly tabs).

Not part of the regular pipeline. Safe to delete after use — no writes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shared.db import get_connection

Q_SUMMARY = """
WITH matched AS (
    SELECT
        f.billing_transaction_id,
        f.order_id,
        f.fee_type,
        f.booking_entry,
        f.amount,
        f.transaction_date,
        o.order_id AS orders_raw_order_id
    FROM order_fees f
    JOIN orders_raw o ON SPLIT_PART(o.order_id, '_', 1) = f.order_id
    WHERE f.fee_type IN ('REFUND', 'CREDIT', 'ADJUSTMENT')
      AND f.order_id IS NOT NULL
)
SELECT fee_type, booking_entry, COUNT(DISTINCT billing_transaction_id) AS txn_count,
       SUM(amount) AS total_amount
FROM matched
GROUP BY fee_type, booking_entry
ORDER BY fee_type, booking_entry;
"""

Q_OVERALL = """
WITH matched AS (
    SELECT DISTINCT f.billing_transaction_id, f.order_id, f.amount, f.transaction_date
    FROM order_fees f
    WHERE f.fee_type IN ('REFUND', 'CREDIT', 'ADJUSTMENT')
      AND f.order_id IS NOT NULL
      AND EXISTS (SELECT 1 FROM orders_raw o WHERE SPLIT_PART(o.order_id, '_', 1) = f.order_id)
)
SELECT
    COUNT(*) AS txn_count,
    COUNT(DISTINCT order_id) AS distinct_orders,
    SUM(ABS(amount)) AS total_abs_amount,
    MIN(transaction_date) AS earliest,
    MAX(transaction_date) AS latest
FROM matched;
"""

Q_BY_YEAR = """
WITH matched AS (
    SELECT DISTINCT f.billing_transaction_id, f.order_id, f.amount, f.transaction_date
    FROM order_fees f
    WHERE f.fee_type IN ('REFUND', 'CREDIT', 'ADJUSTMENT')
      AND f.order_id IS NOT NULL
      AND EXISTS (SELECT 1 FROM orders_raw o WHERE SPLIT_PART(o.order_id, '_', 1) = f.order_id)
)
SELECT EXTRACT(YEAR FROM transaction_date) AS yr, COUNT(*) AS txn_count, SUM(ABS(amount)) AS total_abs_amount
FROM matched
GROUP BY yr
ORDER BY yr;
"""

Q_MULTI_LINE = """
-- orders where the matched order_id has >1 line item in orders_raw (proportional-split cases)
WITH matched_orders AS (
    SELECT DISTINCT f.order_id
    FROM order_fees f
    WHERE f.fee_type IN ('REFUND', 'CREDIT', 'ADJUSTMENT')
      AND f.order_id IS NOT NULL
),
line_counts AS (
    SELECT SPLIT_PART(order_id, '_', 1) AS base_order_id, COUNT(*) AS line_count
    FROM orders_raw
    GROUP BY base_order_id
)
SELECT
    COUNT(*) FILTER (WHERE lc.line_count > 1) AS multi_line_matched_orders,
    COUNT(*) AS total_matched_orders
FROM matched_orders mo
JOIN line_counts lc ON lc.base_order_id = mo.order_id;
"""

Q_SAMPLE = """
WITH matched AS (
    SELECT DISTINCT f.billing_transaction_id, f.order_id, f.fee_type, f.booking_entry,
           f.amount, f.transaction_date
    FROM order_fees f
    WHERE f.fee_type IN ('REFUND', 'CREDIT', 'ADJUSTMENT')
      AND f.order_id IS NOT NULL
      AND EXISTS (SELECT 1 FROM orders_raw o WHERE SPLIT_PART(o.order_id, '_', 1) = f.order_id)
)
SELECT billing_transaction_id, order_id, fee_type, booking_entry, amount, transaction_date
FROM matched
ORDER BY ABS(amount) DESC
LIMIT 15;
"""


def main():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            print("=== Breakdown by fee_type / booking_entry ===")
            cur.execute(Q_SUMMARY)
            for row in cur.fetchall():
                print(row)

            print("\n=== Overall totals (distinct transactions matched to a real order) ===")
            cur.execute(Q_OVERALL)
            print(cur.fetchone())

            print("\n=== By year ===")
            cur.execute(Q_BY_YEAR)
            for row in cur.fetchall():
                print(row)

            print("\n=== Multi-line-item orders among matches (need proportional split) ===")
            cur.execute(Q_MULTI_LINE)
            print(cur.fetchone())

            print("\n=== Top 15 largest matched transactions ===")
            cur.execute(Q_SAMPLE)
            for row in cur.fetchall():
                print(row)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
