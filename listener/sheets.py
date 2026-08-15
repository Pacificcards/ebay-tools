import json
import os

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
WATCHLIST_TAB = "Watchlist"
OBSERVED_TAB = "Observed Listings"
PRICE_CHECK_TAB = "Price Check"

# Price Check tab column positions (1-based):
# Description | Clearing Price | Holding Price | # Listings | Last Checked
_PC_COL_CLEARING = 2
_PC_COL_HOLDING = 3
_PC_COL_N = 4
_PC_COL_CHECKED = 5

# 1-based column positions in the Watchlist tab
# A=Active, B=Description, C=Category, D=Market Price, E=Max Price, F=Min Price, G=Hint URL(s), H=EPID, I=EPID Status
COL_EPID = 8
COL_EPID_STATUS = 9

_spreadsheet_cache: dict = {}


def _get_spreadsheet(sheet_id: str):
    if sheet_id not in _spreadsheet_cache:
        creds_info = json.loads(os.environ["GOOGLE_SHEETS_CREDENTIALS"])
        creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        client = gspread.authorize(creds)
        _spreadsheet_cache[sheet_id] = client.open_by_key(sheet_id)
    return _spreadsheet_cache[sheet_id]


def load_watchlist(sheet_id: str) -> list[dict]:
    """Return all rows from the Watchlist tab as dicts, with '_row_index' (1-based, header=1)."""
    ws = _get_spreadsheet(sheet_id).worksheet(WATCHLIST_TAB)
    records = ws.get_all_records()
    for i, row in enumerate(records):
        row["_row_index"] = i + 2  # header is row 1
    return records


def update_epid_in_sheet(sheet_id: str, row_index: int, epid: str, status: str):
    ws = _get_spreadsheet(sheet_id).worksheet(WATCHLIST_TAB)
    ws.update_cell(row_index, COL_EPID, epid)
    ws.update_cell(row_index, COL_EPID_STATUS, status)


def append_watchlist_row(sheet_id: str, entry: dict) -> None:
    """Append a new row to the Watchlist tab. Active defaults to Y."""
    ws = _get_spreadsheet(sheet_id).worksheet(WATCHLIST_TAB)
    # Column order: Active | Description | Category | Market Price | Max Price | Min Price | Hint URL | EPID | EPID Status | Last Hit
    ws.append_row([
        "Y",
        entry.get("description", ""),
        entry.get("category", ""),
        entry.get("market_price", ""),
        entry.get("max_price", ""),
        entry.get("min_price", ""),
        "",  # Hint URL
        "",  # EPID
        "",  # EPID Status
        "",  # Last Hit
    ], value_input_option="USER_ENTERED")


def read_price_check(sheet_id: str) -> list[dict]:
    """Return rows from the Price Check tab that have a Description filled in."""
    ws = _get_spreadsheet(sheet_id).worksheet(PRICE_CHECK_TAB)
    records = ws.get_all_records()
    rows = []
    for i, row in enumerate(records):
        if str(row.get("Description", "")).strip():
            row["_row_index"] = i + 2  # header is row 1
            rows.append(row)
    return rows


def write_price_check_row(
    sheet_id: str,
    row_idx: int,
    clearing: float | None,
    holding: float | None,
    n_listings: int,
) -> None:
    """Write Clearing Price, Holding Price, # Listings, Last Checked back to the sheet."""
    from datetime import datetime, timezone
    ws = _get_spreadsheet(sheet_id).worksheet(PRICE_CHECK_TAB)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    clearing_str = f"${clearing:.2f}" if clearing is not None else "—"
    holding_str = f"${holding:.2f}" if holding is not None else "—"
    ws.update([[clearing_str, holding_str, n_listings, now]], f"B{row_idx}:E{row_idx}")


def append_observed_listing(sheet_id: str, data: dict):
    ws = _get_spreadsheet(sheet_id).worksheet(OBSERVED_TAB)
    ws.append_row([
        data["timestamp"],
        data["description"],
        data["title"],
        data["price"],
        f"{data['pct_below']}%",
        data["item_id"],
        data["url"],
    ], value_input_option="USER_ENTERED")
