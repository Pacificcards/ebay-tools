"""Fetch Promoted Listings (cost-per-click / Priority) results by listing and day into ad_listing_daily.

Runs in analytics-ingest.yml. Each run re-reads the last TRAILING_DAYS whole days (Pacific time) through
yesterday, one listing-performance report per WINDOW_DAYS (eBay's limit with the day dimension is 7), and
replaces those days in the table, because eBay keeps crediting ad sales to earlier days after the fact. The PCC Seller Hub's Ad Campaigns tab reads
this table instead of making its own report each time the period changes.

Same report as the Seller Hub's old Refresh Ads (pcc-seller-hub/src/lib/ebay/ads.ts): ON_SITE, COST_PER_CLICK,
dimensions listing_id + campaign_id + ad_group_id + day; ad groups are added together per listing/campaign/day.

Needs a refresh token with the sell.marketing scope (the campaign scheduler's EBAY_REFRESH_TOKEN; the
read-only analytics token gets 403 on report tasks).

Actions logs are public: print dates and counts only, never spend, sales or titles.

Backfill: python -m traffic_analytics.fetch_ads --from 2026-09-01 --to 2026-11-26
(split into windows of WINDOW_DAYS days, one report each).
"""
import argparse
import base64
import gzip
import os
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import requests

from shared.db import get_connection

BASE = "https://api.ebay.com/sell/marketing/v1"
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SCOPE = "https://api.ebay.com/oauth/api_scope/sell.marketing"
PACIFIC = ZoneInfo("America/Los_Angeles")
TRAILING_DAYS = 14
WINDOW_DAYS = 7  # eBay: a report with the day dimension may span at most 7 days (400 otherwise)
POLL_SECONDS = 5
MAX_WAIT_SECONDS = 6 * 60

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ad_listing_daily (
    day            DATE        NOT NULL,
    listing_id     TEXT        NOT NULL,
    campaign_id    TEXT        NOT NULL,
    listing_title  TEXT,
    impressions    INTEGER     NOT NULL DEFAULT 0,
    clicks         INTEGER     NOT NULL DEFAULT 0,
    ad_fees        NUMERIC(12,2) NOT NULL DEFAULT 0,
    sale_amount    NUMERIC(12,2) NOT NULL DEFAULT 0,
    sold           INTEGER     NOT NULL DEFAULT 0,
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (day, listing_id, campaign_id)
);
ALTER TABLE ad_listing_daily ENABLE ROW LEVEL SECURITY;
"""


def access_token() -> str:
    auth = base64.b64encode(f"{os.environ['EBAY_CLIENT_ID']}:{os.environ['EBAY_CLIENT_SECRET']}".encode()).decode()
    r = requests.post(
        TOKEN_URL,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "refresh_token", "refresh_token": os.environ["EBAY_REFRESH_TOKEN"], "scope": SCOPE},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"eBay sign-in failed ({r.status_code}): {r.json().get('error', 'no detail')}")
    return r.json()["access_token"]


def pacific_midnight(d: date) -> str:
    """Midnight Pacific at the start of day d, as UTC ISO (zoneinfo handles the clock changes)."""
    return datetime(d.year, d.month, d.day, tzinfo=PACIFIC).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def money(v: str) -> Decimal:
    """eBay sends money as "USD 1.23"."""
    v = (v or "").strip()
    if not v:
        return Decimal("0")
    return Decimal(v.split()[-1].replace(",", ""))


def report_day(v: str) -> date | None:
    """eBay's day column: "Nov 17, 2026"."""
    try:
        return datetime.strptime((v or "").strip(), "%b %d, %Y").date()
    except ValueError:
        return None


def fetch_report(token: str, start: date, end: date) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "reportType": "LISTING_PERFORMANCE_REPORT",
        "fundingModels": ["COST_PER_CLICK"],
        "channels": ["ON_SITE"],
        "marketplaceId": "EBAY_US",
        "reportFormat": "TSV_GZIP",
        "dateFrom": pacific_midnight(start),
        "dateTo": pacific_midnight(end + timedelta(days=1)),
        "dimensions": [
            {"dimensionKey": "listing_id", "annotationKeys": ["listing_title"]},
            {"dimensionKey": "campaign_id"},
            {"dimensionKey": "ad_group_id"},
            {"dimensionKey": "day"},
        ],
        "metricKeys": [
            "cpc_impressions", "cpc_clicks", "cpc_ad_fees_listingsite_currency",
            "cpc_sale_amount_listingsite_currency", "cpc_attributed_sales",
        ],
    }
    r = requests.post(f"{BASE}/ad_report_task", headers={**headers, "Content-Type": "application/json"}, json=body, timeout=60)
    location = r.headers.get("location")
    if not r.ok or not location:
        detail = (r.json().get("errors") or [{}])[0].get("message", "no detail") if r.content else "no detail"
        raise RuntimeError(f"eBay wouldn't make the ad report ({r.status_code}): {detail}")
    started = time.time()
    while True:
        time.sleep(POLL_SECONDS)
        task = requests.get(location, headers=headers, timeout=60).json()
        status = task.get("reportTaskStatus")
        if status == "SUCCESS" and task.get("reportId"):
            f = requests.get(f"{BASE}/ad_report/{task['reportId']}", headers=headers, timeout=120)
            f.raise_for_status()
            print(f"[fetch_ads] report ready after {round(time.time() - started)}s")
            return gzip.decompress(f.content).decode("utf-8")
        if status == "FAILED":
            raise RuntimeError(f"eBay couldn't make the ad report: {task.get('reportTaskStatusMessage', 'no reason given')}")
        if time.time() - started > MAX_WAIT_SECONDS:
            raise RuntimeError("eBay took more than 6 minutes to make the ad report")


def parse(tsv: str, start: date, end: date) -> dict:
    """Rows keyed (day, listing_id, campaign_id), ad groups added together; days outside start..end dropped
    (eBay also sends part of today)."""
    lines = [l for l in tsv.splitlines() if l.strip()]
    if not lines:
        return {}
    head = lines[0].split("\t")
    at = {name: head.index(name) for name in head}
    out: dict = {}
    for line in lines[1:]:
        c = line.split("\t")
        get = lambda name: c[at[name]].strip() if name in at and at[name] < len(c) else ""
        listing_id, campaign_id, day = get("listing_id"), get("campaign_id"), report_day(get("day"))
        if not listing_id or not day or not (start <= day <= end):
            continue
        key = (day, listing_id, campaign_id)
        row = out.setdefault(key, {"title": get("listing_title"), "imp": 0, "clicks": 0, "fees": Decimal("0"), "sales": Decimal("0"), "sold": 0})
        row["imp"] += int(get("cpc_impressions") or 0)
        row["clicks"] += int(get("cpc_clicks") or 0)
        row["fees"] += money(get("cpc_ad_fees_listingsite_currency"))
        row["sales"] += money(get("cpc_sale_amount_listingsite_currency"))
        row["sold"] += int(get("cpc_attributed_sales") or 0)
    return out


def store(conn, rows: dict, start: date, end: date) -> None:
    """Replace start..end in one transaction, so a failed run leaves the old numbers in place."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM ad_listing_daily WHERE day BETWEEN %s AND %s", (start, end))
        cur.executemany(
            """INSERT INTO ad_listing_daily (day, listing_id, campaign_id, listing_title, impressions, clicks, ad_fees, sale_amount, sold)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [(d, l, c, r["title"], r["imp"], r["clicks"], r["fees"], r["sales"], r["sold"]) for (d, l, c), r in rows.items()],
        )
    conn.commit()


def span_hours(start: date, stop: date) -> float:
    """Hours eBay is asked for: midnight Pacific at the start of `start` to midnight after `stop`."""
    a = datetime(start.year, start.month, start.day, tzinfo=PACIFIC)
    nxt = stop + timedelta(days=1)
    b = datetime(nxt.year, nxt.month, nxt.day, tzinfo=PACIFIC)
    utc = ZoneInfo("UTC")  # same-zone subtraction ignores the clock change
    return (b.astimezone(utc) - a.astimezone(utc)).total_seconds() / 3600


def windows(start: date, end: date):
    """Report windows of at most WINDOW_DAYS days and at most 7 x 24 hours: the week the clocks go back (early
    November) is 7 days + 1 hour, which eBay refuses, so that window is one day shorter."""
    while start <= end:
        stop = min(end, start + timedelta(days=WINDOW_DAYS - 1))
        while stop > start and span_hours(start, stop) > 7 * 24:
            stop -= timedelta(days=1)
        yield start, stop
        start = stop + timedelta(days=1)


def run(start: date, end: date) -> None:
    token = access_token()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(TABLE_SQL)
        conn.commit()
        for a, b in windows(start, end):
            rows = parse(fetch_report(token, a, b), a, b)
            store(conn, rows, a, b)
            print(f"[fetch_ads] {a} .. {b}: {len(rows)} listing-days saved")
    finally:
        conn.close()


def main() -> None:
    yesterday = datetime.now(PACIFIC).date() - timedelta(days=1)
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="start", type=date.fromisoformat, default=yesterday - timedelta(days=TRAILING_DAYS - 1))
    p.add_argument("--to", dest="end", type=date.fromisoformat, default=yesterday)
    a = p.parse_args()
    if a.end > yesterday:
        raise SystemExit("--to must be yesterday (Pacific) or earlier: eBay's numbers for today are partial")
    if a.start > a.end:
        raise SystemExit("--from is after --to")
    run(a.start, a.end)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    main()
