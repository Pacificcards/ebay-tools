"""Fetch traffic data from eBay Analytics API and upsert into listing_metrics_raw.

Runs daily. Each run:
  1. Fetches yesterday's data.
  2. Fetches up to CATCHUP_DAYS_PER_RUN of the most-recent missing dates in a
     rolling CATCHUP_WINDOW_DAYS window ending yesterday, working backwards
     until the window is fully populated.

Fetches are scoped to our own known active listing_ids via the `listing_ids`
filter, batched at the API's documented max of 200 per call -- NOT via
unfiltered `dimension=LISTING` + `offset` pagination. Confirmed 2026-08-07 that
`offset` is not honored on this endpoint (every page returns the identical
first ~200 records regardless of offset, and the point where it starts
returning empty pages is inconsistent between back-to-back identical calls),
which had been silently starving ~40% of listings of any data at all.
"""
import os
import time
from datetime import date, timedelta

import requests

from shared.db import get_connection
from shared.ebay_auth import get_access_token

ANALYTICS_URL = "https://api.ebay.com/sell/analytics/v1/traffic_report"
LISTING_ID_BATCH_SIZE = 200  # eBay's documented max for the listing_ids filter (error 50028 above this)
METRICS = ",".join([
    "CLICK_THROUGH_RATE",
    "LISTING_IMPRESSION_TOTAL",
    "LISTING_IMPRESSION_SEARCH_RESULTS_PAGE",
    "LISTING_IMPRESSION_STORE",
    "TOTAL_IMPRESSION_TOTAL",
    "LISTING_VIEWS_TOTAL",
    "LISTING_VIEWS_SOURCE_SEARCH_RESULTS_PAGE",
    "LISTING_VIEWS_SOURCE_STORE",
    "LISTING_VIEWS_SOURCE_DIRECT",
    "LISTING_VIEWS_SOURCE_OFF_EBAY",
    "LISTING_VIEWS_SOURCE_OTHER_EBAY",
    "TRANSACTION",
])

CATCHUP_WINDOW_DAYS = 30
CATCHUP_DAYS_PER_RUN = 5


def fetch_and_store() -> None:
    token = get_access_token(
        os.environ["EBAY_CLIENT_ID"],
        os.environ["EBAY_CLIENT_SECRET"],
        os.environ["EBAY_REFRESH_TOKEN"],
    )

    active_ids = sorted(_get_active_listing_ids())
    if not active_ids:
        print("[fetch_analytics] WARNING: no active listings in listing_metadata — sync_listings may not have run yet. Skipping.")
        return
    print(f"[fetch_analytics] tracking {len(active_ids)} active listings")

    yesterday = date.today() - timedelta(days=1)
    catchup_start = yesterday - timedelta(days=CATCHUP_WINDOW_DAYS - 1)
    catchup_dates = _get_missing_dates(catchup_start, yesterday, CATCHUP_DAYS_PER_RUN)

    windows = [yesterday] + catchup_dates
    if catchup_dates:
        print(f"[fetch_analytics] catch-up: {len(catchup_dates)} dates queued ({catchup_dates[-1]} .. {catchup_dates[0]})")

    total = 0
    skipped = []
    for i, d in enumerate(windows):
        try:
            rows = _fetch_window_with_retry(token, d, d, active_ids)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                print(f"[fetch_analytics] {d}: rate limited after retries, skipping (will retry next run)")
                skipped.append(d)
                continue
            raise
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
    missing = sorted([d for d in all_dates if d not in fetched], reverse=True)
    return missing[:limit]


def _fetch_window_with_retry(token: str, start_date: date, end_date: date, listing_ids: list[str], retries: int = 3) -> list[dict]:
    all_rows = []
    for i in range(0, len(listing_ids), LISTING_ID_BATCH_SIZE):
        batch = listing_ids[i:i + LISTING_ID_BATCH_SIZE]
        for attempt in range(retries):
            try:
                all_rows.extend(_fetch_batch(token, start_date, end_date, batch))
                break
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429 and attempt < retries - 1:
                    wait = 30 * (attempt + 1)
                    print(f"[fetch_analytics] rate limited, waiting {wait}s before retry...")
                    time.sleep(wait)
                else:
                    raise
        if i + LISTING_ID_BATCH_SIZE < len(listing_ids):
            time.sleep(1)
    return all_rows


def _date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _fetch_batch(token: str, start_date: date, end_date: date, listing_ids: list[str]) -> list[dict]:
    id_filter = "|".join(listing_ids)
    filter_str = (
        f"listing_ids:%7B{id_filter}%7D,"
        f"date_range:%5B{start_date.strftime('%Y%m%d')}..{end_date.strftime('%Y%m%d')}%5D"
    )
    url = f"{ANALYTICS_URL}?dimension=LISTING&metric={METRICS}&filter={filter_str}"
    headers = {"Authorization": f"Bearer {token}", "Content-Language": "en-US"}

    response = requests.get(url, headers=headers)
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
            "ctr_ebay_search_page": _float(m.get("CLICK_THROUGH_RATE")),
            "impressions_search_and_store": _int(m.get("LISTING_IMPRESSION_TOTAL")),
            "impressions_search": _int(m.get("LISTING_IMPRESSION_SEARCH_RESULTS_PAGE")),
            "impressions_store":  _int(m.get("LISTING_IMPRESSION_STORE")),
            "impressions_all_sources": _int(m.get("TOTAL_IMPRESSION_TOTAL")),
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
                        ctr_ebay_search_page,
                        impressions_search_and_store, impressions_search, impressions_store,
                        impressions_all_sources,
                        views_total, views_search, views_store,
                        views_direct, views_off_ebay, views_other_ebay,
                        orders
                    ) VALUES (
                        %(listing_id)s, %(date)s,
                        %(ctr_ebay_search_page)s,
                        %(impressions_search_and_store)s, %(impressions_search)s, %(impressions_store)s,
                        %(impressions_all_sources)s,
                        %(views_total)s, %(views_search)s, %(views_store)s,
                        %(views_direct)s, %(views_off_ebay)s, %(views_other_ebay)s,
                        %(orders)s
                    )
                    ON CONFLICT (listing_id, date) DO UPDATE SET
                        ctr_ebay_search_page = EXCLUDED.ctr_ebay_search_page,
                        impressions_search_and_store = EXCLUDED.impressions_search_and_store,
                        impressions_search = EXCLUDED.impressions_search,
                        impressions_store  = EXCLUDED.impressions_store,
                        impressions_all_sources = EXCLUDED.impressions_all_sources,
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
