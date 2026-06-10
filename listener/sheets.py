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
