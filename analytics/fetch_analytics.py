"""Fetch traffic data from eBay Analytics API and upsert into listing_metrics_raw.

Runs daily, fetching yesterday's data for all listings (up to 200).
On first run, pass BACKFILL=1 env var to fetch the full 90-day history in weekly chunks.
"""
import os
from datetime import date, timedelta

import requests

from shared.db import get_connection
from shared.ebay_auth import get_access_token

ANALYTICS_URL = "https://api.ebay.com/sell/analytics/v1/traffic_report"
MARKETPLACE = "EBAY_US"


def fetch_and_store() -> None:
    token = get_access_token(
        os.environ["EBAY_CLIENT_ID"],
        os.environ["EBAY_CLIENT_SECRET"],
        os.environ["EBAY_REFRESH_TOKEN"],
    )

    if os.environ.get("BACKFILL"):
        # Fetch 90 days in 7-day chunks to avoid the 200-listing cap cutting off data
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=89)
        windows = []
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + timedelta(days=6), end)
            windows.append((cursor, chunk_end))
            cursor = chunk_end + timedelta(days=1)
    else:
        yesterday = date.today() - timedelta(days=1)
        windows = [(yesterday, yesterday)]

    total = 0
    for start_date, end_date in windows:
        rows = _fetch_window(token, start_date, end_date)
        _upsert(rows)
        total += len(rows)
        print(f"[fetch_analytics] {start_date}..{end_date}: {len(rows)} rows")

    print(f"[fetch_analytics] total upserted: {total}")


def _fetch_window(token: str, start_date: date, end_date: date) -> list[dict]:
    # URL-encode { } and [ ] as required by eBay docs
    filter_str = (
        f"marketplace_ids:%7B{MARKETPLACE}%7D,"
        f"date_range:%5B{start_date.strftime('%Y%m%d')}..{end_date.strftime('%Y%m%d')}%5D"
    )
    # Build URL manually: commas in metric must not be encoded, filter is pre-encoded
    url = (
        f"{ANALYTICS_URL}"
        f"?dimension=LISTING"
        f"&metric=CLICK_THROUGH_RATE,LISTING_IMPRESSION_TOTAL,LISTING_VIEWS_TOTAL"
        f"&filter={filter_str}"
    )

    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Language": "en-US"},
    )
    if not response.ok:
        print(f"[fetch_analytics] HTTP {response.status_code}: {response.text}")
        response.raise_for_status()

    data = response.json()
    return _parse(data, end_date)


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

        metrics = dict(zip(metric_keys, [m.get("value") for m in metric_values]))

        rows.append({
            "listing_id": listing_id,
            "date": as_of_date.isoformat(),
            "impressions": _int(metrics.get("LISTING_IMPRESSION_TOTAL")),
            "clicks": _int(metrics.get("CLICK_THROUGH_RATE")),
            "page_views": _int(metrics.get("LISTING_VIEWS_TOTAL")),
        })

    return rows


def _int(val) -> int | None:
    try:
        return int(float(val)) if val is not None else None
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
                    INSERT INTO listing_metrics_raw (listing_id, date, impressions, clicks, page_views)
                    VALUES (%(listing_id)s, %(date)s, %(impressions)s, %(clicks)s, %(page_views)s)
                    ON CONFLICT (listing_id, date) DO UPDATE SET
                        impressions = EXCLUDED.impressions,
                        clicks = EXCLUDED.clicks,
                        page_views = EXCLUDED.page_views,
                        fetched_at = NOW()
                    """,
                    row,
                )
    finally:
        conn.close()


if __name__ == "__main__":
    fetch_and_store()
