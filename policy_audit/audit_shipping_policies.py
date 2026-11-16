"""Read-only audit of the account's eBay shipping (fulfillment) policies.

For every shipping policy: its settings, how many ACTIVE listings use it, and how many
publisher-sheet rows reference it. Flags unused policies and groups of policies whose
shipping settings are identical. Changes nothing on eBay or in the sheets.

Run from the repo root:
    .venv/bin/python -m policy_audit.audit_shipping_policies
    .venv/bin/python -m policy_audit.audit_shipping_policies --skip-listings   # policies + sheets only, fast

Needs in .env: EBAY_CLIENT_ID, EBAY_CLIENT_SECRET,
  EBAY_REFRESH_TOKEN_ACCOUNT (sell.account.readonly; mint with policy_audit/get_account_token.py),
  EBAY_REFRESH_TOKEN (Trading API, for listing lookups),
  optional LISTINGS_SHEET_ID / POKEMON_SHEET_ID / GOOGLE_CREDS_PATH for the sheet counts.

Writes policy_audit/output/shipping_policies.csv (gitignored).
"""
import argparse
import csv
import os
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv

from shared.ebay_auth import get_access_token

ACCOUNT_API = "https://api.ebay.com/sell/account/v1/fulfillment_policy"
TRADING_API_URL = "https://api.ebay.com/ws/api.dll"
NS = {"e": "urn:ebay:apis:eBLBaseComponents"}
OUT_DIR = os.path.join(os.path.dirname(__file__), "output")
SHEETS = [("LISTINGS_SHEET_ID", "Listings", "sports"), ("POKEMON_SHEET_ID", "pokemon_draft", "pokemon")]
GETITEM_WORKERS = 4


# ---------- eBay: policies ----------

def fetch_policies(token: str, marketplace: str = "EBAY_US") -> list[dict]:
    r = requests.get(
        ACCOUNT_API,
        params={"marketplace_id": marketplace},
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    if not r.ok:
        raise SystemExit(f"fulfillment_policy HTTP {r.status_code}: {r.text[:300]}")
    return r.json().get("fulfillmentPolicies", [])


def _money(m: dict | None) -> str:
    return f"{float(m['value']):.2f}" if m and m.get("value") is not None else ""


def summarize_services(policy: dict, option_type: str) -> str:
    """'USPSGroundAdvantage $0.00 (+$0.00 ea); ...' for DOMESTIC or INTERNATIONAL."""
    parts = []
    for opt in policy.get("shippingOptions", []):
        if opt.get("optionType") != option_type:
            continue
        cost_type = opt.get("costType", "")
        services = sorted(opt.get("shippingServices", []), key=lambda s: s.get("sortOrder", 0))
        for s in services:
            code = s.get("shippingServiceCode", "?")
            if s.get("freeShipping"):
                cost = "free"
            elif cost_type == "CALCULATED":
                cost = "calculated"
            else:
                cost = f"${_money(s.get('shippingCost')) or '?'}"
                add = _money(s.get("additionalShippingCost"))
                if add:
                    cost += f" (+${add} ea)"
            parts.append(f"{code} {cost}")
    return "; ".join(parts)


def signature(policy: dict) -> tuple:
    """Everything that affects what a buyer is charged / how it ships. Name/description excluded."""
    handling = policy.get("handlingTime") or {}
    return (
        handling.get("value"), handling.get("unit"),
        summarize_services(policy, "DOMESTIC"),
        summarize_services(policy, "INTERNATIONAL"),
        bool(policy.get("globalShipping")),
        bool(policy.get("localPickup")),
        bool(policy.get("freightShipping")),
        tuple(sorted(r.get("regionName", "") for r in
                     (policy.get("shipToLocations") or {}).get("regionExcluded", []))),
    )


def duplicate_groups(policies: list[dict]) -> dict[str, int]:
    """policy_id -> group number, only for policies sharing a signature with another policy."""
    by_sig = defaultdict(list)
    for p in policies:
        by_sig[signature(p)].append(p["fulfillmentPolicyId"])
    out, n = {}, 0
    for ids in by_sig.values():
        if len(ids) > 1:
            n += 1
            for pid in ids:
                out[pid] = n
    return out


# ---------- eBay: which policy each active listing uses ----------

def fetch_active_listings(token: str) -> list[dict]:
    from traffic_analytics.sync_listings import _fetch_all_active
    return _fetch_all_active(token)


def fetch_listing_policy(token: str, listing_id: str) -> str | None:
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <ItemID>{listing_id}</ItemID>
  <OutputSelector>Item.SellerProfiles</OutputSelector>
</GetItemRequest>"""
    headers = {
        "X-EBAY-API-CALL-NAME": "GetItem",
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "1349",
        "X-EBAY-API-IAF-TOKEN": token,
        "Content-Type": "text/xml",
    }
    try:
        r = requests.post(TRADING_API_URL, data=body.encode(), headers=headers, timeout=60)
    except requests.RequestException as e:
        print(f"  GetItem {listing_id}: {e}")
        return None
    if not r.ok:
        print(f"  GetItem {listing_id}: HTTP {r.status_code}")
        return None
    return parse_shipping_profile_id(r.content)


def parse_shipping_profile_id(xml: bytes) -> str | None:
    root = ET.fromstring(xml)
    return root.findtext(
        ".//e:SellerProfiles/e:SellerShippingProfile/e:ShippingProfileID", namespaces=NS
    ) or None


# ---------- Sheets ----------

def sheet_policy_counts() -> dict[str, Counter]:
    """policy_id -> Counter({'sports pending': n, 'sports published': n, ...})."""
    out: dict[str, Counter] = defaultdict(Counter)
    configured = [(os.environ.get(env), tab, label) for env, tab, label in SHEETS if os.environ.get(env)]
    if not configured:
        print("Sheets: no LISTINGS_SHEET_ID / POKEMON_SHEET_ID set - skipping sheet counts")
        return out
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(
        os.environ.get("GOOGLE_CREDS_PATH", "pl/credentials/service_account.json"),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    client = gspread.authorize(creds)
    for sheet_id, tab, label in configured:
        records = client.open_by_key(sheet_id).worksheet(tab).get_all_records()
        for row in records:
            pid = str(row.get("Shipping Policy ID", "")).strip()
            if not pid:
                continue
            state = "published" if str(row.get("Listing ID", "")).strip() else "pending"
            out[pid][f"{label} {state}"] += 1
        print(f"Sheets: read {len(records)} rows from {label} ({tab})")
    return out


# ---------- Report ----------

def build_rows(policies, listing_counts: Counter | None, sheet_counts, titles) -> list[dict]:
    dups = duplicate_groups(policies)
    rows = []
    for p in policies:
        pid = p["fulfillmentPolicyId"]
        handling = p.get("handlingTime") or {}
        n_listings = listing_counts.get(pid, 0) if listing_counts is not None else None
        n_sheet = sum(sheet_counts.get(pid, Counter()).values())
        flags = []
        if n_listings == 0 and n_sheet == 0:
            flags.append("UNUSED")
        elif n_listings == 0:
            flags.append("no active listings")
        if pid in dups:
            flags.append(f"DUPLICATE-{dups[pid]}")
        if any(c.get("default") for c in p.get("categoryTypes", [])):
            flags.append("default")
        rows.append({
            "policy_id": pid,
            "name": p.get("name", ""),
            "flags": ", ".join(flags),
            "active_listings": "" if n_listings is None else n_listings,
            "sheet_rows": "; ".join(f"{k}: {v}" for k, v in sorted(sheet_counts.get(pid, {}).items())),
            "handling": f"{handling.get('value', '')} {handling.get('unit', '')}".strip(),
            "domestic": summarize_services(p, "DOMESTIC"),
            "international": summarize_services(p, "INTERNATIONAL"),
            "description": p.get("description", ""),
            "example_listings": " | ".join(titles.get(pid, [])[:3]),
        })
    rows.sort(key=lambda r: (-(r["active_listings"] or 0), r["name"].lower()))
    return rows


def main():
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-listings", action="store_true",
                    help="don't look up each active listing's policy (one GetItem per listing)")
    args = ap.parse_args()

    cid, secret = os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"]
    account_refresh = os.environ.get("EBAY_REFRESH_TOKEN_ACCOUNT")
    if not account_refresh:
        raise SystemExit("EBAY_REFRESH_TOKEN_ACCOUNT missing - run policy_audit/get_account_token.py first")

    policies = fetch_policies(get_access_token(cid, secret, account_refresh))
    print(f"Shipping policies: {len(policies)}")
    known = {p["fulfillmentPolicyId"] for p in policies}

    listing_counts, titles, missing = None, defaultdict(list), 0
    if not args.skip_listings:
        token = get_access_token(cid, secret, os.environ["EBAY_REFRESH_TOKEN"])
        listings = fetch_active_listings(token)
        print(f"Looking up the shipping policy of {len(listings)} active listings ...")
        with ThreadPoolExecutor(GETITEM_WORKERS) as pool:
            pids = list(pool.map(lambda l: fetch_listing_policy(token, l["listing_id"]), listings))
        listing_counts = Counter()
        for listing, pid in zip(listings, pids):
            if pid is None:
                missing += 1
                continue
            listing_counts[pid] += 1
            titles[pid].append(listing.get("title") or listing["listing_id"])

    sheet_counts = sheet_policy_counts()
    rows = build_rows(policies, listing_counts, sheet_counts, titles)

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "shipping_policies.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["policy_id"])
        w.writeheader()
        w.writerows(rows)

    print()
    for r in rows:
        print(f"{r['policy_id']:>14}  {r['active_listings']!s:>5}  {r['name'][:40]:<40}  {r['flags']}")
    print()
    if listing_counts is not None:
        print(f"Listings with no shipping policy found: {missing}")
        orphans = {pid: n for pid, n in listing_counts.items() if pid not in known}
        if orphans:
            print(f"Listings on policy IDs not in the policy list: {orphans}")
    stale = sorted(pid for pid in sheet_counts if pid not in known)
    if stale:
        print(f"Sheet rows reference policy IDs that no longer exist: {stale}")
    print(f"Unused: {sum('UNUSED' in r['flags'] for r in rows)}  |  "
          f"In a duplicate group: {sum('DUPLICATE' in r['flags'] for r in rows)}")
    print(f"Full report: {path}")


if __name__ == "__main__":
    main()
