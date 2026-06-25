import json
import re

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
EXPECTED_HEADERS = ["Name", "Query", "Category", "Min Price", "Max Price", "Active"]


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _parse_active(val) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.upper() == "TRUE"
    return False


def load_queries(sheet_id: str, creds_json: str) -> list[dict]:
    """Read the Queries tab and return active query configs."""
    creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)
    client = gspread.authorize(creds)
    ws = client.open_by_key(sheet_id).worksheet("Queries")
    rows = ws.get_all_records(expected_headers=EXPECTED_HEADERS)

    queries = []
    for row in rows:
        name = str(row.get("Name", "")).strip()
        if not name or not _parse_active(row.get("Active")):
            continue
        min_p = row.get("Min Price")
        max_p = row.get("Max Price")
        queries.append({
            "id":            _slugify(name),
            "name":          name,
            "query":         str(row.get("Query", "")).strip(),
            "category_name": str(row.get("Category", "")).strip() or None,
            "min_price":     float(min_p) if min_p else None,
            "max_price":     float(max_p) if max_p else None,
        })
    return queries
