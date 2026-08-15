"""Google Sheets read/write for the Pokemon listings publisher."""
import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
TAB = "pokemon_draft"

# 1-based column positions matching the sheet layout (32 columns):
# Game | Language | Title | Price | Min Offer Price | Scheduled Start | Character |
# Card Number | Rarity | Finish | Set | Condition | SKU | Quantity | Description |
# Card Name | Card Type | Material | Vintage | Card Size | Convention/Event |
# Manufacturer | Shipping Policy ID | Return Policy ID | Payment Policy ID |
# Weight (oz) | Length (in) | Width (in) | Height (in) | Best Offer |
# Listing ID (31) | Status (32)
COL_SKU = 13
COL_LISTING_ID = 31
COL_STATUS = 32

_ws_cache: dict = {}


def _worksheet(sheet_id: str, creds_path: str):
    if sheet_id not in _ws_cache:
        creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        client = gspread.authorize(creds)
        _ws_cache[sheet_id] = client.open_by_key(sheet_id).worksheet(TAB)
    return _ws_cache[sheet_id]


def read_pending(sheet_id: str, creds_path: str) -> list[dict]:
    """Return rows that have a Character but no Listing ID (not yet published)."""
    ws = _worksheet(sheet_id, creds_path)
    records = ws.get_all_records()
    pending = []
    for i, row in enumerate(records):
        if row.get("Character") and not row.get("Listing ID"):
            row["_row_index"] = i + 2  # header is row 1
            pending.append(row)
    return pending


def write_result(sheet_id: str, creds_path: str, row_idx: int, listing_id: str, status: str, sku: str = ""):
    ws = _worksheet(sheet_id, creds_path)
    updates = [
        {"range": gspread.utils.rowcol_to_a1(row_idx, COL_LISTING_ID), "values": [[listing_id]]},
        {"range": gspread.utils.rowcol_to_a1(row_idx, COL_STATUS), "values": [[status]]},
    ]
    if sku:
        updates.append({"range": gspread.utils.rowcol_to_a1(row_idx, COL_SKU), "values": [[sku]]})
    ws.batch_update(updates)


_REPRICE_KEYWORD = "REPRICE"
_NORMAL_STATUSES = {"", "published"}


def read_reprice(sheet_id: str, creds_path: str) -> tuple[list[dict], list[dict]]:
    """Scan published rows for REPRICE triggers and ambiguous Status values.

    Returns (reprice_rows, ambiguous_rows).
    reprice_rows: Listing ID present AND Status == 'REPRICE'
    ambiguous_rows: Listing ID present AND Status is unexpected
                    (not blank, 'published', 'REPRICE', or 'error:*')
    """
    ws = _worksheet(sheet_id, creds_path)
    records = ws.get_all_records()
    reprice: list[dict] = []
    ambiguous: list[dict] = []
    for i, row in enumerate(records):
        if not row.get("Listing ID"):
            continue
        status = str(row.get("Status", "")).strip()
        row["_row_index"] = i + 2
        if status == _REPRICE_KEYWORD:
            reprice.append(row)
        elif status not in _NORMAL_STATUSES and not status.startswith("error:"):
            ambiguous.append(row)
    return reprice, ambiguous
