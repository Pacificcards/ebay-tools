import json
import re
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
EXPECTED_HEADERS = ["Name", "Exclusions", "CategoryID", "Min Price", "Max Price", "Active", "MSRP", "Presale Date", "Release Date", "Type"]


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _parse_active(val) -> bool:
    if isinstance(val, str):
        return val.strip().upper() in ("YES", "Y", "TRUE")
    if isinstance(val, bool):
        return val
    return False


def _build_exclusions(exclusions_str: str) -> str:
    """Convert a CSV exclusion list into eBay search exclusion syntax."""
    if not exclusions_str.strip():
        return ""
    parts = []
    for term in exclusions_str.split(","):
        term = term.strip()
        if not term:
            continue
        if term.startswith('"') and term.endswith('"') and len(term) > 2:
            phrase = term[1:-1].strip()
            if phrase:
                parts.append(f'-"{phrase}"')
        elif " " in term:
            parts.append(f'-"{term}"')
        else:
            parts.append(f"-{term}")
    return " ".join(parts)


_DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"]


def _parse_date(val: str, field: str, name: str) -> str | None:
    """Normalize a date string to YYYY-MM-DD. Logs a warning and returns None on failure."""
    if not val:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(val, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    print(f"[sheets] WARNING: Could not parse {field} '{val}' for '{name}' — skipping field")
    return None


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

        cat_id_raw = str(row.get("CategoryID", "")).strip()
        if not cat_id_raw:
            cat_id = None
        elif cat_id_raw.isdigit():
            cat_id = cat_id_raw
        else:
            print(f"[sheets] ERROR: CategoryID '{cat_id_raw}' for '{name}' is not a valid numeric eBay category ID — skipping category filter")
            cat_id = None

        exclusions = _build_exclusions(str(row.get("Exclusions", "")))
        query = f"{name} {exclusions}".strip() if exclusions else name
        min_p = row.get("Min Price")
        max_p = row.get("Max Price")
        msrp  = row.get("MSRP")
        presale_date = _parse_date(str(row.get("Presale Date", "")).strip(), "Presale Date", name)
        release_date = _parse_date(str(row.get("Release Date", "")).strip(), "Release Date", name)
        type_val     = str(row.get("Type", "")).strip() or None
        queries.append({
            "id":           _slugify(name),
            "name":         name,
            "query":        query,
            "category_id":  cat_id,
            "min_price":    float(min_p) if min_p else None,
            "max_price":    float(max_p) if max_p else None,
            "msrp":         float(msrp) if msrp else None,
            "presale_date": presale_date,
            "release_date": release_date,
            "type":         type_val,
        })
    return queries
