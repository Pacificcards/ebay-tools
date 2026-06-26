import json
import re

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
EXPECTED_HEADERS = ["Name", "Exclusions", "Category", "Min Price", "Max Price", "Active", "MSRP"]


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _parse_active(val) -> bool:
    if isinstance(val, str):
        return val.strip().upper() in ("YES", "Y", "TRUE")
    if isinstance(val, bool):
        return val
    return False


def _build_exclusions(exclusions_str: str) -> str:
    """Convert a CSV exclusion list into eBay search exclusion syntax.

    Each comma-separated term becomes a -term or -"phrase" exclusion.
    Terms already wrapped in quotes in the cell, or terms containing spaces,
    are treated as phrase exclusions.

    Example: 'lot, bundle, "hobby box"' → '-lot -bundle -"hobby box"'
    """
    if not exclusions_str.strip():
        return ""
    parts = []
    for term in exclusions_str.split(","):
        term = term.strip()
        if not term:
            continue
        # Explicitly quoted: "hobby box" → -"hobby box"
        if term.startswith('"') and term.endswith('"') and len(term) > 2:
            phrase = term[1:-1].strip()
            if phrase:
                parts.append(f'-"{phrase}"')
        # Multi-word without quotes: hobby box → -"hobby box"
        elif " " in term:
            parts.append(f'-"{term}"')
        # Single word: lot → -lot
        else:
            parts.append(f"-{term}")
    return " ".join(parts)


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
        exclusions = _build_exclusions(str(row.get("Exclusions", "")))
        query = f"{name} {exclusions}".strip() if exclusions else name
        min_p = row.get("Min Price")
        max_p = row.get("Max Price")
        msrp  = row.get("MSRP")
        queries.append({
            "id":            _slugify(name),
            "name":          name,
            "query":         query,
            "category_name": str(row.get("Category", "")).strip() or None,
            "min_price":     float(min_p) if min_p else None,
            "max_price":     float(max_p) if max_p else None,
            "msrp":          float(msrp) if msrp else None,
        })
    return queries
