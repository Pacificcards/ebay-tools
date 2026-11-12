"""Publish selected active eBay listings to a view-only, shareable Google Sheet.

The sheet is shared publicly ("anyone with the link → Viewer"), so it must only ever contain
information a buyer can already see on eBay. The SQL below selects only those fields; nothing
private (SKU, quantity sold, traffic, fees) is ever read, so it can't leak into the file even
via hidden columns, downloads or "Make a copy".

Rows: listing_metadata where status = 'active' (hidden-from-search excluded), the category is
on the allowlist in categories.json, and remaining quantity > 0.

Env:
  SUPABASE_DB_URL            — Postgres connection string
  GOOGLE_SHEETS_CREDENTIALS  — service account JSON (same secret the listener uses)
  PUBLIC_LISTINGS_SHEET_ID   — target spreadsheet; if unset the script skips (exit 0)

Usage:
  python -m public_listings.sync_public_sheet                   # write the sheet
  python -m public_listings.sync_public_sheet --dry-run         # print counts, write nothing
  python -m public_listings.sync_public_sheet --list-categories # every active category + count
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from shared.db import get_connection

PT = ZoneInfo("America/Los_Angeles")
CATEGORIES_PATH = Path(__file__).with_name("categories.json")
HEADER = ["Title", "Price", "Listed", "Category", "Link"]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_MAX_RETRIES = 4
_RETRY_BASE_DELAY = 2  # seconds: 2, 4, 8, 16

# Only these columns are ever selected. Keep this list public-only.
FETCH_SQL = """
    SELECT listing_id, title, current_price, start_time, category_id
    FROM listing_metadata
    WHERE status = 'active'
      AND category_id = ANY(%s)
      AND COALESCE(quantity, 0) - COALESCE(quantity_sold, 0) > 0
    ORDER BY start_time DESC NULLS LAST, listing_id
"""

LIST_CATEGORIES_SQL = """
    SELECT category_id, category_name, COUNT(*)
    FROM listing_metadata
    WHERE status = 'active'
      AND COALESCE(quantity, 0) - COALESCE(quantity_sold, 0) > 0
    GROUP BY category_id, category_name
    ORDER BY COUNT(*) DESC
"""


def load_allowlist(path: Path = CATEGORIES_PATH) -> dict[str, str]:
    """Return {category_id: display label}."""
    data = json.loads(path.read_text())
    return {str(c["id"]): c["label"] for c in data["categories"]}


def fetch_rows(conn, allowlist: dict[str, str]) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(FETCH_SQL, (list(allowlist),))
        return cur.fetchall()


def _cell_text(value: str | None) -> str:
    """Neutralize text a viewer-facing sheet would otherwise evaluate as a formula."""
    text = (value or "").strip()
    if text[:1] in ("=", "+", "-", "@"):
        return "'" + text
    return text


def build_values(rows: list[tuple], allowlist: dict[str, str], updated_at: datetime) -> list[list]:
    stamp = updated_at.astimezone(PT).strftime("%b %-d, %Y %-I:%M %p PT")
    values = [[f"Last updated: {stamp}", "", "", "", ""], HEADER]
    for listing_id, title, price, start_time, category_id in rows:
        listed = start_time.astimezone(PT).strftime("%Y-%m-%d") if start_time else ""
        values.append([
            _cell_text(title),
            float(price) if price is not None else "",
            listed,
            allowlist.get(str(category_id), ""),
            f"https://www.ebay.com/itm/{listing_id}",
        ])
    return values


def _retry(fn, *args, **kwargs):
    import gspread

    for attempt in range(_MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            transient = status is not None and 500 <= status < 600
            if not transient or attempt == _MAX_RETRIES - 1:
                raise
            delay = _RETRY_BASE_DELAY * (2 ** attempt)
            print(f"  [public_listings] transient API error ({e}), retrying in {delay}s")
            time.sleep(delay)


def _open_worksheet(sheet_id: str):
    import gspread
    from google.oauth2.service_account import Credentials

    creds_info = json.loads(os.environ["GOOGLE_SHEETS_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    return _retry(client.open_by_key, sheet_id).sheet1


def write_sheet(ws, values: list[list]) -> None:
    """Overwrite in place, then clear leftover rows below — the sheet never shows empty mid-update."""
    _retry(ws.update, values, "A1", value_input_option="USER_ENTERED")
    _retry(ws.batch_clear, [f"A{len(values) + 1}:E"])
    _retry(ws.freeze, rows=2)
    _retry(ws.format, "A2:E2", {"textFormat": {"bold": True}})
    _retry(ws.format, "B3:B", {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}})


def list_categories(conn, allowlist: dict[str, str]) -> None:
    with conn.cursor() as cur:
        cur.execute(LIST_CATEGORIES_SQL)
        for category_id, name, count in cur.fetchall():
            mark = "INCLUDED" if str(category_id) in allowlist else "        "
            print(f"{mark}  {category_id or '(none)':>8}  {count:>5}  {name or '(no category yet)'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list-categories", action="store_true")
    args = parser.parse_args(argv)

    allowlist = load_allowlist()
    if not allowlist:
        print("[public_listings] categories.json allowlist is empty; nothing to publish")
        return 1

    conn = get_connection()
    try:
        # Printed every run so a new category that should be allowlisted is visible in the log.
        list_categories(conn, allowlist)
        if args.list_categories:
            return 0
        rows = fetch_rows(conn, allowlist)
    finally:
        conn.close()

    counts: dict[str, int] = {}
    for row in rows:
        label = allowlist.get(str(row[4]), "?")
        counts[label] = counts.get(label, 0) + 1
    print(f"[public_listings] {len(rows)} listings: {counts}")

    if args.dry_run:
        for v in build_values(rows[:3], allowlist, datetime.now(timezone.utc))[2:]:
            print("  ", v)
        return 0

    # A bad sync (e.g. sync_listings failing) must not blank a sheet people are looking at.
    if not rows:
        print("::warning title=Public listings sheet not updated::0 matching listings; left the sheet as is")
        return 0

    sheet_id = os.environ.get("PUBLIC_LISTINGS_SHEET_ID", "").strip()
    if not sheet_id:
        print("[public_listings] PUBLIC_LISTINGS_SHEET_ID not set; skipping write")
        return 0

    write_sheet(_open_worksheet(sheet_id), build_values(rows, allowlist, datetime.now(timezone.utc)))
    print("[public_listings] sheet updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
