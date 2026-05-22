"""Fetch traffic data from eBay Analytics API and upsert into listing_metrics_raw."""
import os
from datetime import date, timedelta

import requests

from shared.db import get_connection
from shared.ebay_auth import get_access_token

ANALYTICS_URL = "https://api.ebay.com/sell/analytics/v1/traffic_report"
LOOKBACK_DAYS = 90


def fetch_and_store() -> None:
    token = get_access_token(
        os.environ["EBAY_CLIENT_ID"],
        os.environ["EBAY_CLIENT_SECRET"],
        os.environ["EBAY_REFRESH_TOKEN"],
    )

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=LOOKBACK_DAYS - 1)

    # eBay Analytics API requires dates as YYYYMMDD with no time component
    params = {
        "dimension": "LISTING",
        "metric": "CLICK_THROUGH_RATE,LISTING_IMPRESSION_TOTAL,LISTING_VIEWS_TOTAL",
        "filter": f"date:[{start_date.strftime('%Y%m%d')}..{end_date.strftime('%Y%m%d')}],granularityBucket:DAY",
    }

    # Build URL manually to avoid requests percent-encoding commas in metric/filter values
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    response = requests.get(
        f"{ANALYTICS_URL}?{qs}",
        headers={"Authorization": f"Bearer {token}", "Content-Language": "en-US"},
    )
    if not response.ok:
        print(f"[fetch_analytics] HTTP {response.status_code}: {response.text}")
        response.raise_for_status()
    data = response.json()

    rows = _parse_traffic_report(data)
    _upsert(rows)
    print(f"[fetch_analytics] upserted {len(rows)} rows")


def _parse_traffic_report(data: dict) -> list[dict]:
    """Parse the traffic report response into flat row dicts."""
    rows = []

    metric_headers = [m["metricKey"] for m in data.get("metricKeys", [])]
    dimension_headers = [d["dimensionKey"] for d in data.get("dimensionKeys", [])]

    for record in data.get("records", []):
        dimension_values = record.get("dimensionValues", [])
        metric_values = record.get("metricValues", [])

        dims = dict(zip(dimension_headers, [d.get("value") for d in dimension_values]))
        metrics = dict(zip(metric_headers, [m.get("value") for m in metric_values]))

        listing_id = dims.get("LISTING")
        record_date = dims.get("DATE")

        if not listing_id or not record_date:
            continue

        rows.append({
            "listing_id": listing_id,
            "date": record_date[:10],  # trim to YYYY-MM-DD
            "impressions": _int(metrics.get("LISTING_IMPRESSION_TOTAL")),
            "clicks": _int(metrics.get("CLICK_THROUGH_RATE")),  # API returns raw clicks here
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
