"""TEMPORARY read-only probe of Veeqo's API. Remove after use. Redacts PII before printing."""
import json, os, re, urllib.request
import psycopg2

KEY = os.environ["VEEQO_API_KEY"]
PII = re.compile(r"name|address|email|phone|street|city|zip|post|country|state|company|note|customer|buyer|first|last", re.I)


def get(path):
    req = urllib.request.Request("https://api.veeqo.com" + path, headers={"x-api-key": KEY, "Accept": "application/json", "User-Agent": "pcc-probe"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r), dict(r.headers)


def redact(o, key=""):
    if isinstance(o, dict):
        return {k: redact(v, k) for k, v in o.items()}
    if isinstance(o, list):
        return [redact(v, key) for v in o[:3]]
    if o is not None and PII.search(key):
        return "<redacted>"
    return o


orders, hdrs = get("/orders?status=shipped&page_size=15&page=1")
print("total shipped orders:", hdrs.get("X-Total-Count"))
print("order-level keys:", sorted(orders[0].keys()))
print("channel sample:", json.dumps(redact(orders[0].get("channel")))[:600])

conn = psycopg2.connect(os.environ["SUPABASE_DB_URL"])
cur = conn.cursor()
shown = 0
for o in orders:
    shipments = [(a.get("id"), a.get("shipment")) for a in o.get("allocations", []) if a.get("shipment")]
    cands = {str(o.get("number")), str(o.get("id"))}
    for k in ("channel_order_id", "remote_id", "remote_order_id", "channel_order_number"):
        if o.get(k):
            cands.add(str(o[k]))
    for li in o.get("line_items", []):
        if li.get("remote_id"):
            cands.add(str(li["remote_id"]))
    cur.execute("SELECT order_id FROM orders_raw WHERE " + " OR ".join(["order_id LIKE %s"] * len(cands)), [f"%{c}%" for c in cands]) if cands else None
    matches = [r[0] for r in cur.fetchall()]
    print(f"\n--- veeqo order id={o.get('id')} number={o.get('number')} channel_type={(o.get('channel') or {}).get('type_code')} status={o.get('status')}")
    print("candidate ids:", sorted(cands))
    print("orders_raw matches:", matches[:5])
    print("top-level id-ish fields:", {k: o[k] for k in o if re.search(r"(^|_)(id|number|ref)$|remote|channel_order", k) and not isinstance(o[k], (dict, list))})
    for aid, s in shipments[:2]:
        print(f"allocation {aid} shipment:", json.dumps(redact(s))[:2500])
        shown += 1
    if shown >= 4 and shown:
        pass

# Try a couple of other likely sources of label cost on one shipped order
oid = orders[0]["id"]
for path in (f"/orders/{oid}", "/shipments?page_size=3"):
    try:
        d, _ = get(path)
        print(f"\nGET {path} -> top keys:", sorted(d.keys()) if isinstance(d, dict) else f"list[{len(d)}] first keys {sorted(d[0].keys()) if d else []}")
    except Exception as e:
        print(f"\nGET {path} failed: {e}")
