"""TEMPORARY read-only diagnostic: one order's rows in orders_raw / order_fees + eBay cancel status.
Prints no titles, names or addresses (public Actions logs)."""
import os, sys
import requests
from shared.db import get_connection
from shared.ebay_auth import get_access_token

BASE = "14-15475-07799"

conn = get_connection()
with conn.cursor() as cur:
    cur.execute("""SELECT order_id, order_date, quantity, sale_price, shipping_price, group_name
                   FROM orders_raw WHERE SPLIT_PART(order_id,'_',1) = %s""", (BASE,))
    print("orders_raw:")
    for r in cur.fetchall(): print("  ", r)
    cur.execute("""SELECT fee_type, booking_entry, amount, date, group_name, billing_transaction_id
                   FROM order_fees WHERE order_id = %s ORDER BY date""", (BASE,))
    print("order_fees:")
    for r in cur.fetchall(): print("  ", r)
conn.close()

try:
    tok = get_access_token(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"], os.environ["EBAY_REFRESH_TOKEN"])
    r = requests.get(f"https://api.ebay.com/sell/fulfillment/v1/order/{BASE}",
                     headers={"Authorization": f"Bearer {tok}"}, timeout=60)
    print("ebay HTTP", r.status_code)
    if r.ok:
        o = r.json()
        print("  cancelStatus:", o.get("cancelStatus"))
        print("  orderPaymentStatus:", o.get("orderPaymentStatus"))
        print("  orderFulfillmentStatus:", o.get("orderFulfillmentStatus"))
        print("  total:", (o.get("pricingSummary") or {}).get("total"))
        print("  refunds:", [(x.get("refundDate"), x.get("amount")) for x in (o.get("paymentSummary") or {}).get("refunds", [])])
except Exception as e:
    print("ebay lookup failed:", type(e).__name__, e)
