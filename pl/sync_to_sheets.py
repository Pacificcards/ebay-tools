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
from datetime import date, datetime

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shared.db import get_connection

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# New Entries: date | description | amount | vendor | payment_method | group | status
NEW_ENTRIES_HEADERS = ["date", "description", "amount", "vendor", "payment_method", "group", "status"]

# Sales: order_date | title | gross_sale | net_payout | order_id | group
SALES_HEADERS     = ["order_date", "title", "gross_sale", "net_payout", "order_id", "group"]
PURCHASES_HEADERS = ["purchase_date", "description", "vendor", "total_cost", "source", "id", "group"]
AD_FEES_HEADERS   = ["date", "fee_type", "amount", "transaction_id", "group"]

PLAIN_FORMAT = {
    "backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
    "textFormat": {"bold": False, "foregroundColor": {"red": 0, "green": 0, "blue": 0}},
}


# ── Data fetching ─────────────────────────────────────────────────────────────

def fetch_sales() -> list[list]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    o.order_date::text,
                    COALESCE(lm.title, o.title, o.listing_id) AS title,
                    CAST(o.sale_price + COALESCE(o.shipping_price, 0) AS text) AS gross_sale,
                    CAST(
                        CASE
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
                    o.order_id,
                    ''
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
                ORDER BY o.order_date DESC
            """)
            return [list(row) for row in cur.fetchall()]
    finally:
        conn.close()


def fetch_purchases() -> list[list]:
    conn = get_connection()
    try:
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
    finally:
        conn.close()


def fetch_ad_fees() -> list[list]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    transaction_date::text,
                    COALESCE(fee_type, 'Unknown'),
                    CAST(amount AS text),
                    billing_transaction_id,
                    COALESCE(group_name, '')
                FROM order_fees
                WHERE fee_type != 'SALE'
                ORDER BY transaction_date DESC
            """)
            return [list(row) for row in cur.fetchall()]
    finally:
        conn.close()


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
        for row in existing[1:]:
            order_id  = row[4] if len(row) > 4 else ""
            group_val = row[5] if len(row) > 5 else ""
            if order_id and group_val:
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


def save_sale_groups(groups: dict[str, str]) -> None:
    """Persist group assignments from the Sales tab back to orders_raw."""
    if not groups:
        return
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            for order_id, group_name in groups.items():
                cur.execute(
                    "UPDATE orders_raw SET group_name = %s WHERE order_id = %s",
                    (group_name, order_id),
                )
    finally:
        conn.close()


def save_purchase_groups(groups: dict[str, str]) -> None:
    """Persist group assignments from the Purchases tab back to import_queue."""
    if not groups:
        return
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            for item_id, group_name in groups.items():
                cur.execute(
                    "UPDATE import_queue SET group_name = %s WHERE id = %s",
                    (group_name, int(item_id)),
                )
    finally:
        conn.close()


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
            txn_id    = row[3] if len(row) > 3 else ""
            group_val = row[4] if len(row) > 4 else ""
            if txn_id and group_val:
                groups[txn_id] = group_val
    return groups


def save_ad_fee_groups(groups: dict[str, str]) -> None:
    """Persist group assignments from the Ad Fees tab back to order_fees."""
    if not groups:
        return
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            for txn_id, group_name in groups.items():
                cur.execute(
                    "UPDATE order_fees SET group_name = %s WHERE billing_transaction_id = %s",
                    (group_name, txn_id),
                )
    finally:
        conn.close()


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
    """Create the New Entries input tab with headers if it doesn't exist yet."""
    try:
        doc.worksheet("New Entries")
    except gspread.WorksheetNotFound:
        ws = doc.add_worksheet(title="New Entries", rows=500, cols=10, index=0)
        ws.update([NEW_ENTRIES_HEADERS], value_input_option="USER_ENTERED")
        _reset_header_format(ws)


def process_new_entries(doc: gspread.Spreadsheet) -> int:
    """
    Read blank-status rows from the New Entries tab, insert them into import_queue,
    and stamp each successfully synced row with the sync date.
    Returns the count of rows synced.
    """
    try:
        ws = doc.worksheet("New Entries")
    except gspread.WorksheetNotFound:
        return 0

    all_rows = ws.get_all_values()
    if len(all_rows) <= 1:
        return 0

    today_str = date.today().isoformat()
    entries: list[dict] = []
    sheet_row_numbers: list[int] = []  # 1-indexed row numbers for cell stamping

    for i, row in enumerate(all_rows[1:], start=2):
        # Pad short rows to full header width
        row = list(row) + [""] * (len(NEW_ENTRIES_HEADERS) - len(row))

        status = row[6].strip()
        if status:
            continue  # already synced

        raw_date    = row[0].strip()
        description = row[1].strip()
        if not raw_date or not description:
            continue  # skip blank rows

        try:
            date_val = _normalize_date(raw_date)
        except ValueError as e:
            print(f"  [new_entries] skipping '{description}': {e}")
            continue

        raw_amount = row[2].strip().lstrip("$").replace(",", "") or None
        entries.append({
            "purchase_date":  date_val,
            "description":    description,
            "total_cost":     raw_amount,
            "vendor":         row[3].strip() or None,
            "payment_method": row[4].strip() or None,
            "group_name":     row[5].strip() or None,
        })
        sheet_row_numbers.append(i)

    if not entries:
        return 0

    synced_indices = _insert_manual_entries(entries)

    status_col = len(NEW_ENTRIES_HEADERS)  # last column, 1-indexed
    for idx in synced_indices:
        ws.update_cell(sheet_row_numbers[idx], status_col, f"✓ Synced {today_str}")

    return len(synced_indices)


def _insert_manual_entries(entries: list[dict]) -> list[int]:
    """
    Insert manual entries into import_queue.
    Returns list of successfully inserted indices (into the entries list).
    """
    conn = get_connection()
    synced: list[int] = []
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
                    cur.execute("RELEASE SAVEPOINT sp")
                    synced.append(i)
                except Exception as e:
                    cur.execute("ROLLBACK TO SAVEPOINT sp")
                    print(f"  [new_entries] failed to insert '{entry['description']}': {e}")
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

    # Preserve group values (column F, index 5); order_id is column E (index 4)
    group_by_order_id: dict[str, str] = {}
    if len(existing) > 1:
        for row in existing[1:]:
            order_id  = row[4] if len(row) > 4 else ""
            group_val = row[5] if len(row) > 5 else ""
            if order_id and group_val:
                group_by_order_id[order_id] = group_val

    for row in rows:
        row[5] = group_by_order_id.get(row[4], "")

    ws.clear()
    ws.update([SALES_HEADERS] + rows, value_input_option="USER_ENTERED")
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
    ws.update([PURCHASES_HEADERS] + rows, value_input_option="USER_ENTERED")
    _reset_header_format(ws)


def write_ad_fees_tab(doc: gspread.Spreadsheet, rows: list[list]) -> None:
    ws = _get_or_create_tab(doc, "Ad Fees", index=2)
    existing = ws.get_all_values()

    # Preserve group values (column E, index 4); billing_transaction_id is column D (index 3)
    group_by_txn_id: dict[str, str] = {}
    if len(existing) > 1:
        for row in existing[1:]:
            txn_id    = row[3] if len(row) > 3 else ""
            group_val = row[4] if len(row) > 4 else ""
            if txn_id and group_val:
                group_by_txn_id[txn_id] = group_val

    for row in rows:
        sheet_group = group_by_txn_id.get(row[3], "")
        row[4] = sheet_group if sheet_group else row[4]

    ws.clear()
    ws.update([AD_FEES_HEADERS] + rows, value_input_option="USER_ENTERED")
    _reset_header_format(ws)


def write_pl_tab(doc: gspread.Spreadsheet, sales_row_count: int, purchases_row_count: int, ad_fees_row_count: int) -> None:
    ws = _get_or_create_tab(doc, "P&L by Group", index=3)

    sales_end     = max(sales_row_count + 1, 2)
    purchases_end = max(purchases_row_count + 1, 2)
    ad_fees_end   = max(ad_fees_row_count + 1, 2)

    sg  = f"Sales!F2:F{sales_end}"
    pg  = f"Purchases!G2:G{purchases_end}"
    ag  = f"'Ad Fees'!E2:E{ad_fees_end}"
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
    ws.update(headers + data, value_input_option="USER_ENTERED")
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

    # Persist current group assignments from Sheet back to Supabase
    print("Saving group assignments to Supabase...")
    sale_groups     = _read_sale_groups(doc)
    purchase_groups = _read_purchase_groups(doc)
    ad_fee_groups   = _read_ad_fee_groups(doc)
    save_sale_groups(sale_groups)
    save_purchase_groups(purchase_groups)
    save_ad_fee_groups(ad_fee_groups)
    print(f"  {len(sale_groups)} sale groups, {len(purchase_groups)} purchase groups, {len(ad_fee_groups)} ad fee groups saved")

    print("Fetching sales from Supabase...")
    sales = fetch_sales()
    print(f"  {len(sales)} sales found")

    print("Fetching purchases from Supabase...")
    purchases = fetch_purchases()
    print(f"  {len(purchases)} purchases found")

    print("Fetching ad fees from Supabase...")
    ad_fees = fetch_ad_fees()
    print(f"  {len(ad_fees)} ad fee records found")

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
