"""
TEMPORARY read-only diagnostic — runs the REAL fetch_sales()/fetch_ad_fees()
and verifies the refund-netting change: per-order payout reconciliation and
profit neutrality. No writes. Delete after use.
"""
import os
import sys
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shared.db import get_connection
import pl.sync_to_sheets as sts


def D(x):
    return Decimal(x) if x not in (None, "") else None


def main():
    conn = get_connection()
    try:
        sales = sts.fetch_sales(conn)
        fees = sts.fetch_ad_fees(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT f.order_id,
                       (SELECT SUM(amount) FROM order_fees s WHERE s.order_id = f.order_id
                          AND s.fee_type='SALE' AND s.booking_entry='CREDIT') AS payout,
                       SUM(f.amount) AS refund
                FROM order_fees f
                WHERE f.fee_type IN ('REFUND','CREDIT','ADJUSTMENT') AND f.booking_entry='DEBIT'
                  AND EXISTS (SELECT 1 FROM orders_raw o WHERE SPLIT_PART(o.order_id,'_',1)=f.order_id)
                GROUP BY f.order_id ORDER BY f.order_id
            """)
            refunds = cur.fetchall()
    finally:
        conn.close()

    by_order = defaultdict(lambda: [Decimal(0), Decimal(0), 0, 0])
    for r in sales:
        o = by_order[r[6]]
        o[0] += D(r[2]) or 0          # gross
        if r[3] not in (None, ""):
            o[1] += D(r[3])           # net_payout
        else:
            o[3] += 1                 # NULL payout lines
        o[2] += 1

    print("=== per-order reconciliation (DEBIT refunds on orders we have) ===")
    print("order | payout | refund | expected_net | actual_net | gross_now | null_lines | OK?")
    bad = 0
    for order_id, payout, refund in refunds:
        g, n, lines, nulls = by_order[order_id]
        expected = (payout or 0) - refund
        ok = "OK" if (payout is not None and abs(n - expected) <= Decimal("0.05") * lines) else "CHECK"
        if ok != "OK":
            bad += 1
        print(f"{order_id} | {payout} | {refund} | {expected} | {n} | {g} | {nulls} | {ok}")
    print(f"orders needing attention: {bad} of {len(refunds)}")

    print("\n=== Ad Fees category totals (as the sheet will show them) ===")
    tot = defaultdict(lambda: [0, Decimal(0)])
    adj = {"CREDIT": Decimal(0), "DEBIT": Decimal(0)}
    for r in fees:
        cat, be, amt = r[8], r[9], D(r[2]) or 0
        tot[cat][0] += 1
        tot[cat][1] += amt
        if cat == "Adjustment" and be in adj:
            adj[be] += amt
    for cat, (c, a) in sorted(tot.items()):
        print(f"{cat}: {c} rows, ${a}")
    print(f"P&L adjustments (CREDIT - DEBIT, category=Adjustment): {adj['CREDIT'] - adj['DEBIT']}")
    refund_total = sum(r[2] for r in refunds)
    print(f"Order Refund rows total (moved out of Adjustments): ${refund_total}")


if __name__ == "__main__":
    main()
