"""
Pulls sales, purchases, and ad fees from Supabase and writes them into a Google Sheet.
Also processes manually entered expenses from the "New Entries" tab.

Tabs:
  - New Entries  : user input for manual expenses; rows stamped after sync to Supabase
  - Sales        : all orders from orders_raw (with net_payout), editable 'group' column
  - Purchases    : all import_queue items (non-ignored), editable 'group' column
  - Ad Fees      : all non-SALE transactions by date
  - P&L by Group : formula-driven summary: gross | net | costs | profit per group

Group assignments are persisted to Supabase on each sync so the Sheet is fully regenerable.

Run:
  python pl/sync_to_sheets.py

Required env vars (in .env or environment):
  SUPABASE_DB_URL   — Supabase Postgres connection string
  SHEETS_DOC_ID     — Google Sheet document ID (from the URL)
  GOOGLE_CREDS_PATH — Path to service account JSON (default: pl/credentials/service_account.json)
"""

import os
import sys
import uuid
from datetime import date, datetime

import gspread
from google.oauth2.service_account import Credentials
from psycopg2.extras import execute_values
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shared.db import get_connection

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# New Entries: date | description | type | amount | vendor | payment_method | group | status | record_id
NEW_ENTRIES_HEADERS = ["date", "description", "type", "amount", "vendor", "payment_method", "group", "status", "record_id"]

_STATUS_COL    = NEW_ENTRIES_HEADERS.index("status") + 1     # col 8, 1-indexed for gspread
_RECORD_ID_COL = NEW_ENTRIES_HEADERS.index("record_id") + 1  # col 9

# Sales: order_date | title | gross_sale | net_payout | shipping_cost | order_id | ebay_order_id | group | source
SALES_HEADERS     = ["order_date", "title", "gross_sale", "net_payout", "shipping_cost", "order_id", "ebay_order_id", "group", "source"]
PURCHASES_HEADERS = ["purchase_date", "description", "vendor", "total_cost", "source", "id", "group"]
AD_FEES_HEADERS   = ["date", "fee_type", "amount", "order_id", "listing_id", "title", "transaction_id", "group"]

PLAIN_FORMAT = {
    "backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
    "textFormat": {"bold": False, "foregroundColor": {"red": 0, "green": 0, "blue": 0}},
}


# ── Data fetching ─────────────────────────────────────────────────────────────

def fetch_sales(conn) -> list[list]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                o.order_date::text,
                COALESCE(lm.title, o.title, o.listing_id) AS title,
                CASE WHEN o.order_id LIKE 'MANUAL-%' THEN ''
                     ELSE CAST(o.sale_price + COALESCE(o.shipping_price, 0) AS text)
                END AS gross_sale,
                CAST(
                    CASE
                        WHEN o.order_id LIKE 'MANUAL-%' THEN o.sale_price
                        WHEN f.amount IS NULL THEN NULL
                        WHEN order_totals.total_gross <= 0 THEN NULL
                        ELSE (
                            CASE
                                WHEN ROUND(
                                         f.amount * (o.sale_price + COALESCE(o.shipping_price, 0))
                                         / order_totals.total_gross,
                                     2) > (o.sale_price + COALESCE(o.shipping_price, 0)) * 1.10
                                THEN NULL
                                ELSE ROUND(
                                         f.amount * (o.sale_price + COALESCE(o.shipping_price, 0))
                                         / order_totals.total_gross,
                                     2)
                            END
                        )
                    END
                AS text) AS net_payout,
                CAST(
                    CASE
                        WHEN o.order_id LIKE 'MANUAL-%' THEN NULL
                        WHEN sl.total_shipping IS NULL THEN NULL
                        WHEN order_totals.total_gross <= 0 THEN NULL
                        ELSE ROUND(
                            sl.total_shipping * (o.sale_price + COALESCE(o.shipping_price, 0))
                            / order_totals.total_gross,
                        2)
                    END
                AS text) AS shipping_cost,
                o.order_id,
                CASE WHEN o.order_id LIKE 'MANUAL-%' THEN ''
                     ELSE SPLIT_PART(o.order_id, '_', 1)
                END AS ebay_order_id,
                COALESCE(o.group_name, ''),
                CASE WHEN o.order_id LIKE 'MANUAL-%' THEN 'Manual' ELSE 'eBay' END AS source
            FROM orders_raw o
            LEFT JOIN listing_metadata lm USING (listing_id)
            LEFT JOIN order_fees f
                ON f.order_id = SPLIT_PART(o.order_id, '_', 1)
                AND f.booking_entry = 'CREDIT'
                AND f.fee_type = 'SALE'
            LEFT JOIN (
                SELECT SPLIT_PART(order_id, '_', 1) AS ebay_order_id,
                       SUM(sale_price + COALESCE(shipping_price, 0)) AS total_gross
                FROM orders_raw
                GROUP BY ebay_order_id
            ) order_totals ON order_totals.ebay_order_id = SPLIT_PART(o.order_id, '_', 1)
            LEFT JOIN (
                SELECT order_id, SUM(amount) AS total_shipping
                FROM order_fees
                WHERE fee_type = 'SHIPPING_LABEL' AND booking_entry = 'DEBIT'
                GROUP BY order_id
            ) sl ON sl.order_id = SPLIT_PART(o.order_id, '_', 1)
            ORDER BY o.order_date DESC
        """)
        return [list(row) for row in cur.fetchall()]


def fetch_purchases(conn) -> list[list]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                purchase_date::text,
                description,
                CASE WHEN source = 'ebay_purchase' THEN 'eBay' ELSE COALESCE(vendor, '') END,
                CAST(total_cost AS text),
                source,
                id::text,
                COALESCE(group_name, '')
            FROM import_queue
            WHERE status != 'ignored'
            ORDER BY purchase_date DESC
        """)
        return [list(row) for row in cur.fetchall()]


def fetch_ad_fees(conn) -> list[list]:
    with conn.cursor() as cur:
        cur.execute("""
            WITH fee_listing AS (
                SELECT
                    f.*,
                    COALESCE(
                        f.listing_id,
                        (SELECT o.listing_id
                         FROM orders_raw o
                         WHERE SPLIT_PART(o.order_id, '_', 1) = f.order_id
                         LIMIT 1)
                    ) AS resolved_listing_id
                FROM order_fees f
                WHERE f.fee_type != 'SALE'
            )
            SELECT
                fl.transaction_date::text,
                COALESCE(fl.fee_type, 'Unknown'),
                CAST(fl.amount AS text),
                COALESCE(fl.order_id, ''),
                COALESCE(fl.resolved_listing_id, ''),
                COALESCE(
                    (SELECT lm.title FROM listing_metadata lm
                     WHERE lm.listing_id = fl.resolved_listing_id),
                    (SELECT o.title FROM orders_raw o
                     WHERE o.listing_id = fl.resolved_listing_id
                     LIMIT 1),
                    ''
                ) AS title,
                fl.billing_transaction_id,
                COALESCE(fl.group_name, '')
            FROM fee_listing fl
            ORDER BY fl.transaction_date DESC
        """)
        return [list(row) for row in cur.fetchall()]


# ── Group persistence ─────────────────────────────────────────────────────────

def _read_sale_groups(doc: gspread.Spreadsheet) -> dict[str, str]:
    """Read current group assignments from the Sales tab."""
    try:
        ws = doc.worksheet("Sales")
    except gspread.WorksheetNotFound:
        return {}
    existing = ws.get_all_values()
    groups: dict[str, str] = {}
    if len(existing) > 1:
        header = existing[0]
        try:
            order_id_col = header.index("order_id")
            group_col    = header.index("group")
        except ValueError:
            order_id_col, group_col = 4, 5  # old 6-col schema fallback
        for row in existing[1:]:
            order_id  = row[order_id_col] if len(row) > order_id_col else ""
            group_val = row[group_col]     if len(row) > group_col    else ""
            if order_id and group_val and group_val != order_id.split("_")[0]:
                groups[order_id] = group_val
    return groups


def _read_purchase_groups(doc: gspread.Spreadsheet) -> dict[str, str]:
    """Read current group assignments from the Purchases tab."""
    try:
        ws = doc.worksheet("Purchases")
    except gspread.WorksheetNotFound:
        return {}
    existing = ws.get_all_values()
    groups: dict[str, str] = {}
    if len(existing) > 1:
        for row in existing[1:]:
            item_id   = row[5] if len(row) > 5 else ""
            group_val = row[6] if len(row) > 6 else ""
            if item_id and group_val:
                groups[item_id] = group_val
    return groups


def save_sale_groups(groups: dict[str, str], conn) -> None:
    """Persist group assignments from the Sales tab back to orders_raw."""
    # Reject values matching the base eBay order ID (XX-XXXXX-XXXXX) — corruption guard.
    clean = [(k, v) for k, v in groups.items() if v != k.split("_")[0]]
    if not clean:
        return
    with conn, conn.cursor() as cur:
        execute_values(
            cur,
            """
            UPDATE orders_raw AS t SET group_name = d.group_name
            FROM (VALUES %s) AS d(order_id, group_name)
            WHERE t.order_id = d.order_id
            """,
            clean,
        )


def save_purchase_groups(groups: dict[str, str], conn) -> None:
    """Persist group assignments from the Purchases tab back to import_queue."""
    if not groups:
        return
    with conn, conn.cursor() as cur:
        execute_values(
            cur,
            """
            UPDATE import_queue AS t SET group_name = d.group_name
            FROM (VALUES %s) AS d(id, group_name)
            WHERE t.id = d.id::integer
            """,
            [(int(k), v) for k, v in groups.items()],
        )


def _read_ad_fee_groups(doc: gspread.Spreadsheet) -> dict[str, str]:
    """Read current group assignments from the Ad Fees tab."""
    try:
        ws = doc.worksheet("Ad Fees")
    except gspread.WorksheetNotFound:
        return {}
    existing = ws.get_all_values()
    groups: dict[str, str] = {}
    if len(existing) > 1:
        for row in existing[1:]:
            # Support old 5-col schema (txn_id at 3) and new 8-col schema (txn_id at 6)
            if len(row) >= 8:
                txn_id    = row[6]
                group_val = row[7]
            else:
                txn_id    = row[3] if len(row) > 3 else ""
                group_val = row[4] if len(row) > 4 else ""
            if txn_id and group_val:
                groups[txn_id] = group_val
    return groups


def save_ad_fee_groups(groups: dict[str, str], conn) -> None:
    """Persist group assignments from the Ad Fees tab back to order_fees."""
    if not groups:
        return
    with conn, conn.cursor() as cur:
        execute_values(
            cur,
            """
            UPDATE order_fees AS t SET group_name = d.group_name
            FROM (VALUES %s) AS d(txn_id, group_name)
            WHERE t.billing_transaction_id = d.txn_id
            """,
            list(groups.items()),
        )


# ── Manual entry processing ───────────────────────────────────────────────────

def _normalize_date(val: str) -> str:
    """Convert common date formats to ISO (YYYY-MM-DD). Raises ValueError if unrecognised."""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m/%d"):
        try:
            d = datetime.strptime(val, fmt)
            if fmt == "%m/%d":
                d = d.replace(year=date.today().year)
            return d.strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date format: '{val}'")


def _ensure_new_entries_tab(doc: gspread.Spreadsheet) -> None:
    """Create the New Entries tab if it doesn't exist; add any missing header columns."""
    try:
        ws = doc.worksheet("New Entries")
    except gspread.WorksheetNotFound:
        ws = doc.add_worksheet(title="New Entries", rows=500, cols=10, index=0)
        ws.update([NEW_ENTRIES_HEADERS], value_input_option="USER_ENTERED")
        _reset_header_format(ws)
        return

    # Add any header columns that were introduced after the tab was first created
    existing_headers = ws.row_values(1)
    for col_name in NEW_ENTRIES_HEADERS:
        if col_name not in existing_headers:
            next_col = len(existing_headers) + 1
            ws.update_cell(1, next_col, col_name)
            existing_headers.append(col_name)


def _backfill_record_ids(ws: gspread.Worksheet, all_rows: list[list]) -> None:
    """
    For previously-synced New Entries rows that have a blank record_id, look up
    the matching DB record by (type, description, date, amount) and populate col 9.
    Skips rows where the match is ambiguous (multiple results).
    """
    backfill_needed = []
    for i, row in enumerate(all_rows[1:], start=2):
        row = list(row) + [""] * (len(NEW_ENTRIES_HEADERS) - len(row))
        status    = row[_STATUS_COL - 1].strip()
        record_id = row[_RECORD_ID_COL - 1].strip()
        if status.startswith("✓") and not record_id:
            backfill_needed.append((i, row))

    if not backfill_needed:
        return

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for i, row in backfill_needed:
                entry_type  = row[2].strip().lower() or "purchase"
                description = row[1].strip()
                raw_date    = row[0].strip()
                raw_amount  = row[3].strip().lstrip("$").replace(",", "") or None
                try:
                    date_val = _normalize_date(raw_date)
                except ValueError:
                    continue

                if entry_type == "sale":
                    cur.execute(
                        """
                        SELECT order_id FROM orders_raw
                        WHERE order_id LIKE 'MANUAL-%%' AND title = %s
                          AND order_date = %s AND sale_price = %s
                        """,
                        (description, date_val, raw_amount),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id::text FROM import_queue
                        WHERE source = 'manual' AND description = %s
                          AND purchase_date = %s AND total_cost = %s
                        """,
                        (description, date_val, raw_amount),
                    )
                results = cur.fetchall()
                if len(results) == 1:
                    ws.update_cell(i, _RECORD_ID_COL, results[0][0])
                    print(f"  [new_entries] backfilled record_id {results[0][0]} for row {i}")
    finally:
        conn.close()


def process_new_entries(doc: gspread.Spreadsheet) -> int:
    """
    Read the New Entries tab and:
      - Insert blank-status rows into the DB, then stamp status + record_id.
      - Delete rows where status is "Marked for Deletion" using the stored record_id.
      - Backfill record_id for previously-synced rows that predate this feature.
    Returns the count of rows inserted (deletions not counted).
    """
    try:
        ws = doc.worksheet("New Entries")
    except gspread.WorksheetNotFound:
        return 0

    all_rows = ws.get_all_values()
    if len(all_rows) <= 1:
        return 0

    _backfill_record_ids(ws, all_rows)

    today_str = date.today().isoformat()
    purchase_entries: list[dict] = []
    purchase_row_numbers: list[int] = []
    sale_entries: list[dict] = []
    sale_row_numbers: list[int] = []

    for i, row in enumerate(all_rows[1:], start=2):
        row = list(row) + [""] * (len(NEW_ENTRIES_HEADERS) - len(row))

        status = row[_STATUS_COL - 1].strip()

        # Deletion: user set status to "Marked for Deletion"
        if status.lower() == "marked for deletion":
            record_id = row[_RECORD_ID_COL - 1].strip()
            if not record_id:
                ws.update_cell(i, _STATUS_COL, "✗ No Record ID — entry predates delete workflow")
                continue
            success, msg = _delete_manual_entry(record_id)
            ws.update_cell(i, _STATUS_COL, f"Deleted {today_str}" if success else f"✗ {msg}")
            continue

        if status:
            continue  # already processed

        raw_date    = row[0].strip()
        description = row[1].strip()
        if not raw_date or not description:
            continue

        try:
            date_val = _normalize_date(raw_date)
        except ValueError as e:
            print(f"  [new_entries] skipping '{description}': {e}")
            continue

        entry_type = row[2].strip().lower()
        if entry_type not in ("sale", "purchase", ""):
            ws.update_cell(i, _STATUS_COL, f"✗ Invalid type: '{row[2].strip()}'")
            print(f"  [new_entries] invalid type '{row[2].strip()}' for '{description}' — valid values: sale, purchase")
            continue
        if not entry_type:
            entry_type = "purchase"

        raw_amount = row[3].strip().lstrip("$").replace(",", "") or None
        entry = {
            "purchase_date":  date_val,
            "description":    description,
            "total_cost":     raw_amount,
            "vendor":         row[4].strip() or None,
            "payment_method": row[5].strip() or None,
            "group_name":     row[6].strip() or None,
        }
        if entry_type == "sale":
            sale_entries.append(entry)
            sale_row_numbers.append(i)
        else:
            purchase_entries.append(entry)
            purchase_row_numbers.append(i)

    if not purchase_entries and not sale_entries:
        return 0

    synced_count = 0

    if purchase_entries:
        for idx, record_id in _insert_manual_entries(purchase_entries).items():
            ws.update_cell(purchase_row_numbers[idx], _STATUS_COL, f"✓ Synced {today_str}")
            ws.update_cell(purchase_row_numbers[idx], _RECORD_ID_COL, record_id)
            synced_count += 1

    if sale_entries:
        for idx, record_id in _insert_manual_sales(sale_entries).items():
            ws.update_cell(sale_row_numbers[idx], _STATUS_COL, f"✓ Synced {today_str}")
            ws.update_cell(sale_row_numbers[idx], _RECORD_ID_COL, record_id)
            synced_count += 1

    return synced_count


def _delete_manual_entry(record_id: str) -> tuple[bool, str]:
    """
    Hard-delete a manually created entry by its record_id.
    Sales have MANUAL- prefixed order_ids; purchases have numeric import_queue ids.
    Refuses to delete eBay-sourced rows (safety guard).
    Returns (success, error_message).
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if record_id.startswith("MANUAL-"):
                cur.execute(
                    "DELETE FROM orders_raw WHERE order_id = %s AND order_id LIKE 'MANUAL-%%'",
                    (record_id,),
                )
            else:
                try:
                    purchase_id = int(record_id)
                except ValueError:
                    return False, f"Invalid Record ID: '{record_id}'"
                cur.execute(
                    "DELETE FROM import_queue WHERE id = %s AND source = 'manual'",
                    (purchase_id,),
                )
            if cur.rowcount == 0:
                return False, "Not found or not a manual entry"
        conn.commit()
        return True, ""
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def _insert_manual_entries(entries: list[dict]) -> dict[int, str]:
    """
    Insert manual entries into import_queue.
    Returns {index: record_id} for each successfully inserted entry.
    """
    conn = get_connection()
    synced: dict[int, str] = {}
    try:
        with conn.cursor() as cur:
            for i, entry in enumerate(entries):
                try:
                    cur.execute("SAVEPOINT sp")
                    cur.execute(
                        """
                        INSERT INTO import_queue (
                            source, status, purchase_date, description,
                            total_cost, vendor, payment_method, group_name
                        ) VALUES ('manual', 'pending', %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            entry["purchase_date"],
                            entry["description"],
                            entry["total_cost"],
                            entry["vendor"],
                            entry["payment_method"],
                            entry["group_name"],
                        ),
                    )
                    synced[i] = str(cur.fetchone()[0])
                    cur.execute("RELEASE SAVEPOINT sp")
                except Exception as e:
                    cur.execute("ROLLBACK TO SAVEPOINT sp")
                    print(f"  [new_entries] failed to insert '{entry['description']}': {e}")
        conn.commit()
    finally:
        conn.close()
    return synced


def _insert_manual_sales(entries: list[dict]) -> dict[int, str]:
    """
    Insert manual sales into orders_raw.
    Returns {index: order_id} for each successfully inserted entry.
    """
    conn = get_connection()
    synced: dict[int, str] = {}
    try:
        with conn.cursor() as cur:
            for i, entry in enumerate(entries):
                try:
                    cur.execute("SAVEPOINT sp")
                    order_id = f"MANUAL-{uuid.uuid4().hex[:16]}"
                    cur.execute(
                        """
                        INSERT INTO orders_raw (
                            order_id, listing_id, order_date, title, sale_price, group_name
                        ) VALUES (%s, 'manual', %s, %s, %s, %s)
                        """,
                        (
                            order_id,
                            entry["purchase_date"],
                            entry["description"],
                            entry["total_cost"],
                            entry["group_name"],
                        ),
                    )
                    synced[i] = order_id
                    cur.execute("RELEASE SAVEPOINT sp")
                except Exception as e:
                    cur.execute("ROLLBACK TO SAVEPOINT sp")
                    print(f"  [new_entries] failed to insert sale '{entry['description']}': {e}")
        conn.commit()
    finally:
        conn.close()
    return synced


# ── Sheet writing ─────────────────────────────────────────────────────────────

def _open_sheet(doc_id: str, creds_path: str) -> gspread.Spreadsheet:
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(doc_id)


def _get_or_create_tab(doc: gspread.Spreadsheet, title: str, index: int) -> gspread.Worksheet:
    try:
        return doc.worksheet(title)
    except gspread.WorksheetNotFound:
        return doc.add_worksheet(title=title, rows=1000, cols=20, index=index)


def _reset_header_format(ws: gspread.Worksheet) -> None:
    ws.format("1:1", PLAIN_FORMAT)


def write_sales_tab(doc: gspread.Spreadsheet, rows: list[list]) -> None:
    ws = _get_or_create_tab(doc, "Sales", index=0)
    existing = ws.get_all_values()

    group_by_order_id: dict[str, str] = {}
    if len(existing) > 1:
        header = existing[0]
        try:
            order_id_col = header.index("order_id")
            group_col    = header.index("group")
        except ValueError:
            order_id_col, group_col = 4, 5  # old 6-col schema fallback
        for row in existing[1:]:
            order_id  = row[order_id_col] if len(row) > order_id_col else ""
            group_val = row[group_col]     if len(row) > group_col    else ""
            if order_id and group_val and group_val != order_id.split("_")[0]:
                group_by_order_id[order_id] = group_val

    order_id_col = SALES_HEADERS.index("order_id")
    group_col    = SALES_HEADERS.index("group")
    for row in rows:
        sheet_group = group_by_order_id.get(row[order_id_col], "")
        row[group_col] = sheet_group if sheet_group else row[group_col]

    ws.clear()
    ws.update([SALES_HEADERS] + rows, 'A1', value_input_option="USER_ENTERED")
    _reset_header_format(ws)


def write_purchases_tab(doc: gspread.Spreadsheet, rows: list[list]) -> None:
    ws = _get_or_create_tab(doc, "Purchases", index=1)
    existing = ws.get_all_values()

    # Preserve group values (column G, index 6); id is column F (index 5)
    group_by_id: dict[str, str] = {}
    if len(existing) > 1:
        for row in existing[1:]:
            item_id   = row[5] if len(row) > 5 else ""
            group_val = row[6] if len(row) > 6 else ""
            if item_id and group_val:
                group_by_id[item_id] = group_val

    for row in rows:
        sheet_group = group_by_id.get(row[5], "")
        row[6] = sheet_group if sheet_group else row[6]

    ws.clear()
    ws.update([PURCHASES_HEADERS] + rows, 'A1', value_input_option="USER_ENTERED")
    _reset_header_format(ws)


def write_ad_fees_tab(doc: gspread.Spreadsheet, rows: list[list]) -> None:
    ws = _get_or_create_tab(doc, "Ad Fees", index=2)
    existing = ws.get_all_values()

    # Preserve group values (column H, index 7); billing_transaction_id is column G (index 6)
    # Also support old 5-col schema on first sync after column additions
    group_by_txn_id: dict[str, str] = {}
    if len(existing) > 1:
        for row in existing[1:]:
            if len(row) >= 8:
                txn_id    = row[6]
                group_val = row[7]
            else:
                txn_id    = row[3] if len(row) > 3 else ""
                group_val = row[4] if len(row) > 4 else ""
            if txn_id and group_val:
                group_by_txn_id[txn_id] = group_val

    for row in rows:
        sheet_group = group_by_txn_id.get(row[6], "")
        row[7] = sheet_group if sheet_group else row[7]

    ws.clear()
    ws.update([AD_FEES_HEADERS] + rows, 'A1', value_input_option="USER_ENTERED")
    _reset_header_format(ws)


def write_pl_tab(doc: gspread.Spreadsheet, sales_row_count: int, purchases_row_count: int, ad_fees_row_count: int) -> None:
    ws = _get_or_create_tab(doc, "P&L by Group", index=3)

    sales_end     = max(sales_row_count + 1, 2)
    purchases_end = max(purchases_row_count + 1, 2)
    ad_fees_end   = max(ad_fees_row_count + 1, 2)

    sg  = f"Sales!H2:H{sales_end}"
    pg  = f"Purchases!G2:G{purchases_end}"
    ag  = f"'Ad Fees'!H2:H{ad_fees_end}"
    sc  = f"Sales!C2:C{sales_end}"
    sd  = f"Sales!D2:D{sales_end}"
    pc  = f"Purchases!D2:D{purchases_end}"
    ac  = f"'Ad Fees'!C2:C{ad_fees_end}"

    headers = [["group", "gross_revenue", "net_revenue", "costs", "profit"]]
    data = [
        [
            f'=IFERROR(UNIQUE(FILTER({{{sg};{pg};{ag}}},{{{sg};{pg};{ag}}}<>"")),"")',
            f'=IFERROR(ARRAYFORMULA(SUMIF({sg},A2:A,{sc})),"")',
            f'=IFERROR(ARRAYFORMULA(SUMIF({sg},A2:A,{sd})),"")',
            f'=IFERROR(ARRAYFORMULA(SUMIF({pg},A2:A,{pc})+SUMIF({ag},A2:A,{ac})),"")',
            '=IFERROR(C2:C-D2:D,"")',
        ]
    ]

    ws.clear()
    ws.update(headers + data, 'A1', value_input_option="USER_ENTERED")
    _reset_header_format(ws)


# ── Entrypoint ────────────────────────────────────────────────────────────────

def sync(doc_id: str, creds_path: str) -> None:
    print(f"Opening Google Sheet {doc_id}...")
    doc = _open_sheet(doc_id, creds_path)

    # Ensure the New Entries input tab exists before anything else
    _ensure_new_entries_tab(doc)

    # Write any pending manual entries to Supabase and stamp them
    print("Processing new manual entries...")
    synced_count = process_new_entries(doc)
    print(f"  {synced_count} new entr{'y' if synced_count == 1 else 'ies'} synced to Supabase")

    # Persist current group assignments from Sheet back to Supabase, then fetch all data.
    # One connection for the entire DB phase.
    print("Saving group assignments to Supabase...")
    sale_groups     = _read_sale_groups(doc)
    purchase_groups = _read_purchase_groups(doc)
    ad_fee_groups   = _read_ad_fee_groups(doc)

    conn = get_connection()
    try:
        save_sale_groups(sale_groups, conn)
        save_purchase_groups(purchase_groups, conn)
        save_ad_fee_groups(ad_fee_groups, conn)
        print(f"  {len(sale_groups)} sale groups, {len(purchase_groups)} purchase groups, {len(ad_fee_groups)} ad fee groups saved")

        print("Fetching sales from Supabase...")
        sales = fetch_sales(conn)
        print(f"  {len(sales)} sales found")

        print("Fetching purchases from Supabase...")
        purchases = fetch_purchases(conn)
        print(f"  {len(purchases)} purchases found")

        print("Fetching ad fees from Supabase...")
        ad_fees = fetch_ad_fees(conn)
        print(f"  {len(ad_fees)} ad fee records found")
    finally:
        conn.close()

    print("Writing Sales tab...")
    write_sales_tab(doc, sales)

    print("Writing Purchases tab...")
    write_purchases_tab(doc, purchases)

    print("Writing Ad Fees tab...")
    write_ad_fees_tab(doc, ad_fees)

    print("Writing P&L by Group tab...")
    write_pl_tab(doc, len(sales), len(purchases), len(ad_fees))

    print(f"Done. Open: https://docs.google.com/spreadsheets/d/{doc_id}")


if __name__ == "__main__":
    doc_id     = os.environ.get("SHEETS_DOC_ID", "").strip()
    creds_path = os.environ.get("GOOGLE_CREDS_PATH", "pl/credentials/service_account.json")

    if not doc_id:
        print("ERROR: SHEETS_DOC_ID not set in .env")
        sys.exit(1)
    if not os.path.exists(creds_path):
        print(f"ERROR: credentials file not found at {creds_path}")
        print("See pl/README.md for setup instructions.")
        sys.exit(1)

    sync(doc_id, creds_path)
