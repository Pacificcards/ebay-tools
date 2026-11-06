"""TEMPORARY read-only Veeqo dedupe probe. Prints aggregates and masked ids only (public repo logs)."""
import datetime as dt, json, os, time, urllib.request
import psycopg2

KEY = os.environ["VEEQO_API_KEY"]


def get(path):
    for attempt in range(5):
        try:
            req = urllib.request.Request("https://api.veeqo.com" + path, headers={"x-api-key": KEY, "Accept": "application/json", "User-Agent": "pcc-probe"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r), dict(r.headers)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 * (attempt + 1)); continue
            raise
    raise RuntimeError("rate limited")


since = (dt.datetime.utcnow() - dt.timedelta(days=60)).strftime("%Y-%m-%d 00:00:00")
orders, page = [], 1
while True:
    batch, h = get(f"/orders?page_size=100&page={page}&updated_at_min={since.replace(' ', '%20')}")
    orders += batch
    if len(batch) < 100 or page >= 60:
        break
    page += 1
print("orders pulled (updated last 60d):", len(orders))

labels = []  # (channel, order_number, shipment_id, cost, created, cancelled, carrier)
for o in orders:
    for a in o.get("allocations", []):
        s = a.get("shipment")
        if not s:
            continue
        v = (s.get("outbound_label_charges") or {}).get("value")
        tn = s.get("tracking_number") or {}
        labels.append((((o.get("channel") or {}).get("type_code")), str(o.get("number")), s["id"], v, (s.get("created_at") or "")[:10], bool(tn.get("cancelled")), s.get("sub_carrier_id")))

withcost = [l for l in labels if l[3]]
print("shipments:", len(labels), "| with label cost:", len(withcost), "| total $", round(sum(l[3] for l in withcost), 2))
from collections import Counter
print("by channel (with cost):", Counter(l[0] for l in withcost))
print("cancelled-tracking labels:", [(l[0], l[3], l[4]) for l in labels if l[5]][:10])
print("shipments per order >1:", sum(1 for c in Counter(l[1] for l in labels).values() if c > 1))
print("cost-bearing, created date range:", min((l[4] for l in withcost), default=None), max((l[4] for l in withcost), default=None))

conn = psycopg2.connect(os.environ["SUPABASE_DB_URL"])
cur = conn.cursor()
ebay = [l for l in withcost if l[0] == "ebay"]
both = veeqo_only = 0
diffs = []
for l in ebay:
    cur.execute("SELECT COALESCE(SUM(amount),0), COUNT(*), MIN(transaction_date)::text FROM order_fees WHERE order_id=%s AND fee_type='SHIPPING_LABEL' AND booking_entry='DEBIT'", (l[1],))
    tot, n, d = cur.fetchone()
    if n:
        both += 1
        diffs.append((l[1][-5:], float(l[3]), float(tot), n, l[4], d))
    else:
        veeqo_only += 1
print(f"eBay-channel Veeqo labels: {len(ebay)} | also have eBay SHIPPING_LABEL row: {both} | no eBay label row: {veeqo_only}")
print("overlap sample (order tail, veeqo$, ebay$, ebay rows, veeqo date, ebay date):")
for d in diffs[:15]:
    print("  ", d)
# ebay finances lag check: veeqo-only labels by age
today = dt.date.today()
ages = Counter((today - dt.date.fromisoformat(l[4])).days // 3 * 3 for l in ebay
               if not cur.execute("SELECT 1 FROM order_fees WHERE order_id=%s AND fee_type='SHIPPING_LABEL'", (l[1],)) and not cur.fetchone())
print("veeqo-only age buckets (days):", sorted(ages.items()))
cur.execute("SELECT fee_type, COUNT(*) FROM order_fees WHERE fee_type ILIKE '%SHIP%' GROUP BY 1")
print("order_fees shipping types:", cur.fetchall())
