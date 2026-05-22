"""Fetch traffic data from eBay Analytics API and upsert into listing_metrics_raw.

Runs daily, fetching yesterday's data for all listings (up to 200).
Set BACKFILL=1 to fetch historical data. Set BACKFILL_START=YYYY-MM-DD to control
the start date (defaults to 2026-01-01).
"""
import os
import time
from datetime import date, timedelta

import requests

from shared.db import get_connection
from shared.ebay_auth import get_access_token

ANALYTICS_URL = "https://api.ebay.com/sell/analytics/v1/traffic_report"
MARKETPLACE = "EBAY_US"
METRICS = ",".join([
    "CLICK_THROUGH_RATE",
    "LISTING_IMPRESSION_TOTAL",
    "LISTING_IMPRESSION_SEARCH_RESULTS_PAGE",
    "LISTING_IMPRESSION_STORE",
    "LISTING_VIEWS_TOTAL",
    "LISTING_VIEWS_SOURCE_SEARCH_RESULTS_PAGE",
    "LISTING_VIEWS_SOURCE_STORE",
    "LISTING_VIEWS_SOURCE_DIRECT",
    "LISTING_VIEWS_SOURCE_OFF_EBAY",
    "LISTING_VIEWS_SOURCE_OTHER_EBAY",
    "TRANSACTION",
])


def fetch_and_store() -> None:
    token = get_access_token(
        os.environ["EBAY_CLIENT_ID"],
        os.environ["EBAY_CLIENT_SECRET"],
        os.environ["EBAY_REFRESH_TOKEN"],
    )

    if os.environ.get("BACKFILL"):
        end = date.today() - timedelta(days=1)
        start = date.fromisoformat(os.environ.get("BACKFILL_START", "2026-01-01"))
        windows = [(d, d) for d in _date_range(start, end)]
    else:
        yesterday = date.today() - timedelta(days=1)
        windows = [(yesterday, yesterday)]

    total = 0
    for start_date, end_date in windows:
        rows = _fetch_window_with_retry(token, start_date, end_date)
        _upsert(rows)
        total += len(rows)
        print(f"[fetch_analytics] {start_date}..{end_date}: {len(rows)} rows")
        time.sleep(0.5)

    print(f"[fetch_analytics] total upserted: {total}")


def _fetch_window_with_retry(token: str, start_date: date, end_date: date, retries: int = 3) -> list[dict]:
    for attempt in range(retries):
        try:
            return _fetch_window(token, start_date, end_date)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429 and attempt < retries - 1:
                wait = 30 * (attempt + 1)
                print(f"[fetch_analytics] rate limited, waiting {wait}s before retry...")
                time.sleep(wait)
            else:
                raise
    return []


def _date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _fetch_window(token: str, start_date: date, end_date: date) -> list[dict]:
    filter_str = (
        f"marketplace_ids:%7B{MARKETPLACE}%7D,"
        f"date_range:%5B{start_date.strftime('%Y%m%d')}..{end_date.strftime('%Y%m%d')}%5D"
    )
    url = (
        f"{ANALYTICS_URL}"
        f"?dimension=LISTING"
        f"&metric={METRICS}"
        f"&filter={filter_str}"
    )

    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Language": "en-US"},
    )
    if not response.ok:
        print(f"[fetch_analytics] HTTP {response.status_code}: {response.text}")
        response.raise_for_status()

    return _parse(response.json(), end_date)


def _parse(data: dict, as_of_date: date) -> list[dict]:
    header = data.get("header", {})
    metric_keys = [m["key"] for m in header.get("metrics", [])]

    rows = []
    for record in data.get("records", []):
        dim_values = record.get("dimensionValues", [])
        metric_values = record.get("metricValues", [])

        listing_id = dim_values[0]["value"] if dim_values else None
        if not listing_id:
            continue

        m = dict(zip(metric_keys, [mv.get("value") for mv in metric_values]))

        rows.append({
            "listing_id": listing_id,
            "date": as_of_date.isoformat(),
            "ctr":                _float(m.get("CLICK_THROUGH_RATE")),
            "impressions_total":  _int(m.get("LISTING_IMPRESSION_TOTAL")),
            "impressions_search": _int(m.get("LISTING_IMPRESSION_SEARCH_RESULTS_PAGE")),
            "impressions_store":  _int(m.get("LISTING_IMPRESSION_STORE")),
            "views_total":        _int(m.get("LISTING_VIEWS_TOTAL")),
            "views_search":       _int(m.get("LISTING_VIEWS_SOURCE_SEARCH_RESULTS_PAGE")),
            "views_store":        _int(m.get("LISTING_VIEWS_SOURCE_STORE")),
            "views_direct":       _int(m.get("LISTING_VIEWS_SOURCE_DIRECT")),
            "views_off_ebay":     _int(m.get("LISTING_VIEWS_SOURCE_OFF_EBAY")),
            "views_other_ebay":   _int(m.get("LISTING_VIEWS_SOURCE_OTHER_EBAY")),
            "orders":             _int(m.get("TRANSACTION")),
        })

    return rows


def _int(val) -> int | None:
    try:
        return int(float(val)) if val is not None else None
    except (ValueError, TypeError):
        return None


def _float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def _upsert(rows: list[dict]) -> None:
    if not rows:
        return
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    """
                    INSERT INTO listing_metrics_raw (
                        listing_id, date,
                        ctr,
                        impressions_total, impressions_search, impressions_store,
                        views_total, views_search, views_store,
                        views_direct, views_off_ebay, views_other_ebay,
                        orders
                    ) VALUES (
                        %(listing_id)s, %(date)s,
                        %(ctr)s,
                        %(impressions_total)s, %(impressions_search)s, %(impressions_store)s,
                        %(views_total)s, %(views_search)s, %(views_store)s,
                        %(views_direct)s, %(views_off_ebay)s, %(views_other_ebay)s,
                        %(orders)s
                    )
                    ON CONFLICT (listing_id, date) DO UPDATE SET
                        ctr               = EXCLUDED.ctr,
                        impressions_total  = EXCLUDED.impressions_total,
                        impressions_search = EXCLUDED.impressions_search,
                        impressions_store  = EXCLUDED.impressions_store,
                        views_total        = EXCLUDED.views_total,
                        views_search       = EXCLUDED.views_search,
                        views_store        = EXCLUDED.views_store,
                        views_direct       = EXCLUDED.views_direct,
                        views_off_ebay     = EXCLUDED.views_off_ebay,
                        views_other_ebay   = EXCLUDED.views_other_ebay,
                        orders             = EXCLUDED.orders,
                        fetched_at         = NOW()
                    """,
                    row,
                )
    finally:
        conn.close()


if __name__ == "__main__":
    fetch_and_store()
