"""
Pulls sales, purchases, and ad fees from Supabase and writes them into a Google Sheet.

Tabs written:
  - Sales        : all orders from orders_raw (with net_payout), editable 'group' column
  - Purchases    : all import_queue items (non-ignored), editable 'group' column
  - Ad Fees      : NON_SALE_CHARGE transactions (priority ads, etc.) by date
  - P&L by Group : formula-driven summary: gross | net | costs | profit per group

Run:
  python pl/sync_to_sheets.py

Required env vars (in .env or environment):
  DATABASE_URL      — Supabase Postgres connection string
  SHEETS_DOC_ID     — Google Sheet document ID (from the URL)
  GOOGLE_CREDS_PATH — Path to service account JSON (default: pl/credentials/service_account.json)
"""

import os
import sys

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

# Sales: order_date | title | gross_sale | net_payout | order_id | group
SALES_HEADERS     = ["order_date", "title", "gross_sale", "net_payout", "order_id", "group"]
PURCHASES_HEADERS = ["purchase_date", "description", "total_cost", "source", "id", "group"]
AD_FEES_HEADERS   = ["date", "fee_type", "amount", "transaction_id"]

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
                    CAST(total_cost AS text),
                    source,
                    id::text,
                    ''
                FROM import_queue
                WHERE status != 'ignored'
                ORDER BY purchase_date DESC
            """)
            return [list(row) for row in cur.fetchall()]
    finally:
        conn.close()


def fetch_ad_fees() -> list[list]:
    """Fetch NON_SALE_CHARGE transactions (priority ads, etc.)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    transaction_date::text,
                    COALESCE(fee_type, 'Unknown'),
                    CAST(amount AS text),
                    billing_transaction_id
                FROM order_fees
                WHERE fee_type != 'SALE'
                ORDER BY transaction_date DESC
            """)
            return [list(row) for row in cur.fetchall()]
    finally:
        conn.close()


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

    # Preserve group values (column F, index 5); order_id is now column E (index 4)
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

    # Preserve group values (column F, index 5); id is column E (index 4)
    group_by_id: dict[str, str] = {}
    if len(existing) > 1:
        for row in existing[1:]:
            item_id   = row[4] if len(row) > 4 else ""
            group_val = row[5] if len(row) > 5 else ""
            if item_id and group_val:
                group_by_id[item_id] = group_val

    for row in rows:
        row[5] = group_by_id.get(row[4], "")

    ws.clear()
    ws.update([PURCHASES_HEADERS] + rows, value_input_option="USER_ENTERED")
    _reset_header_format(ws)


def write_ad_fees_tab(doc: gspread.Spreadsheet, rows: list[list]) -> None:
    ws = _get_or_create_tab(doc, "Ad Fees", index=2)
    ws.clear()
    ws.update([AD_FEES_HEADERS] + rows, value_input_option="USER_ENTERED")
    _reset_header_format(ws)


def write_pl_tab(doc: gspread.Spreadsheet, sales_row_count: int, purchases_row_count: int) -> None:
    ws = _get_or_create_tab(doc, "P&L by Group", index=3)

    sales_end     = max(sales_row_count + 1, 2)
    purchases_end = max(purchases_row_count + 1, 2)

    # Sales columns:     A=order_date B=title C=sale_price D=net_payout E=order_id F=group
    # Purchases columns: A=date       B=desc  C=total_cost D=source     E=id       F=group
    #
    # P&L columns:
    #   A: group name   — UNIQUE across Sales!F and Purchases!F
    #   B: gross_revenue — SUMIF on Sales!F → Sales!C (sale_price)
    #   C: net_revenue   — SUMIF on Sales!F → Sales!D (net_payout)
    #   D: costs         — SUMIF on Purchases!F → Purchases!C (total_cost)
    #   E: profit        — net_revenue - costs (C - D)

    headers = [["group", "gross_revenue", "net_revenue", "costs", "profit"]]
    data = [
        [
            f'=IFERROR(UNIQUE(FILTER({{Sales!F2:F{sales_end};Purchases!F2:F{purchases_end}}},{{Sales!F2:F{sales_end};Purchases!F2:F{purchases_end}}}<>"")),"")',
            f'=IFERROR(ARRAYFORMULA(SUMIF(Sales!F2:F{sales_end},A2:A,Sales!C2:C{sales_end})),"")',
            f'=IFERROR(ARRAYFORMULA(SUMIF(Sales!F2:F{sales_end},A2:A,Sales!D2:D{sales_end})),"")',
            f'=IFERROR(ARRAYFORMULA(SUMIF(Purchases!F2:F{purchases_end},A2:A,Purchases!C2:C{purchases_end})),"")',
            '=IFERROR(C2:C-D2:D,"")',
        ]
    ]

    ws.clear()
    ws.update(headers + data, value_input_option="USER_ENTERED")
    _reset_header_format(ws)


# ── Entrypoint ────────────────────────────────────────────────────────────────

def sync(doc_id: str, creds_path: str) -> None:
    print("Fetching sales from Supabase...")
    sales = fetch_sales()
    print(f"  {len(sales)} sales found")

    print("Fetching purchases from Supabase...")
    purchases = fetch_purchases()
    print(f"  {len(purchases)} purchases found")

    print("Fetching ad fees from Supabase...")
    ad_fees = fetch_ad_fees()
    print(f"  {len(ad_fees)} ad fee records found")

    print(f"Opening Google Sheet {doc_id}...")
    doc = _open_sheet(doc_id, creds_path)

    print("Writing Sales tab...")
    write_sales_tab(doc, sales)

    print("Writing Purchases tab...")
    write_purchases_tab(doc, purchases)

    print("Writing Ad Fees tab...")
    write_ad_fees_tab(doc, ad_fees)

    print("Writing P&L by Group tab...")
    write_pl_tab(doc, len(sales), len(purchases))

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
