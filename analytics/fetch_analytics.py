"""Fetch traffic data from eBay Analytics API and upsert into listing_metrics_raw.

Runs daily. Each run:
  1. Fetches yesterday's data.
  2. Fetches up to CATCHUP_DAYS_PER_RUN of the oldest missing dates in the
     catch-up window [CATCHUP_START .. CATCHUP_END], defined as dates with
     zero rows in listing_metrics_raw.
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

CATCHUP_START = date(2026, 3, 15)
CATCHUP_END   = date(2026, 5, 21)
CATCHUP_DAYS_PER_RUN = 5


def fetch_and_store() -> None:
    token = get_access_token(
        os.environ["EBAY_CLIENT_ID"],
        os.environ["EBAY_CLIENT_SECRET"],
        os.environ["EBAY_REFRESH_TOKEN"],
    )

    active_ids = _get_active_listing_ids()
    if active_ids:
        print(f"[fetch_analytics] filtering to {len(active_ids)} active listings")
    else:
        print("[fetch_analytics] WARNING: no active listings in listing_metadata — sync_listings may not have run yet. Fetching all listings.")

    yesterday = date.today() - timedelta(days=1)
    catchup_dates = _get_missing_dates(CATCHUP_START, CATCHUP_END, CATCHUP_DAYS_PER_RUN)

    windows = [yesterday] + catchup_dates
    if catchup_dates:
        print(f"[fetch_analytics] catch-up: {len(catchup_dates)} dates queued ({catchup_dates[0]} .. {catchup_dates[-1]})")

    total = 0
    skipped = []
    for i, d in enumerate(windows):
        try:
            rows = _fetch_window_with_retry(token, d, d)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                print(f"[fetch_analytics] {d}: rate limited after retries, skipping (will retry next run)")
                skipped.append(d)
                continue
            raise
        if active_ids:
            rows = [r for r in rows if r["listing_id"] in active_ids]
        _upsert(rows)
        total += len(rows)
        print(f"[fetch_analytics] {d}: {len(rows)} rows")
        if i < len(windows) - 1:
            time.sleep(5)

    if skipped:
        print(f"[fetch_analytics] skipped {len(skipped)} date(s) due to rate limiting: {skipped}")

    print(f"[fetch_analytics] total upserted: {total}")


def _get_missing_dates(start: date, end: date, limit: int) -> list[date]:
    """Return up to `limit` dates in [start, end] with no rows in listing_metrics_raw."""
    all_dates = list(_date_range(start, end))
    if not all_dates:
        return []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT date FROM listing_metrics_raw WHERE date BETWEEN %s AND %s",
                (start, end),
            )
            fetched = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()
    missing = [d for d in all_dates if d not in fetched]
    return missing[:limit]


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


PAGE_SIZE = 200


def _fetch_window(token: str, start_date: date, end_date: date) -> list[dict]:
    filter_str = (
        f"marketplace_ids:%7B{MARKETPLACE}%7D,"
        f"date_range:%5B{start_date.strftime('%Y%m%d')}..{end_date.strftime('%Y%m%d')}%5D"
    )
    base_url = (
        f"{ANALYTICS_URL}"
        f"?dimension=LISTING"
        f"&metric={METRICS}"
        f"&filter={filter_str}"
        f"&limit={PAGE_SIZE}"
    )
    headers = {"Authorization": f"Bearer {token}", "Content-Language": "en-US"}

    all_rows = []
    offset = 0

    while True:
        url = f"{base_url}&offset={offset}"
        response = requests.get(url, headers=headers)
        if not response.ok:
            print(f"[fetch_analytics] HTTP {response.status_code}: {response.text}")
            response.raise_for_status()

        data = response.json()
        rows = _parse(data, end_date)
        all_rows.extend(rows)

        # eBay returns `warnings` when there's a next page; stop when we get fewer than a full page
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return all_rows


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


def _get_active_listing_ids() -> set[str]:
    """Return listing IDs from listing_metadata where status is not 'ended'."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT listing_id FROM listing_metadata WHERE status != 'ended'"
            )
            return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


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
