"""TEMPORARY read-only diagnostic for the stale check — delete before merging."""
import os
from collections import defaultdict

from listener.main import _parse_last_hit
from listener.sheets import OBSERVED_TAB, WATCHLIST_TAB, _get_spreadsheet

sheet = _get_spreadsheet(os.environ["GOOGLE_SHEET_ID"])

wl = sheet.worksheet(WATCHLIST_TAB)
header = wl.row_values(1)
print("Watchlist header:", header)
col = header.index("Last Hit") + 1 if "Last Hit" in header else None
print("Last Hit column index:", col)
formatted = wl.get_all_values()
unformatted = wl.get_all_values(value_render_option="UNFORMATTED_VALUE")
formulas = wl.get_all_values(value_render_option="FORMULA")
records = wl.get_all_records()
for i, rec in enumerate(records):
    if str(rec.get("Active (Y/N)", "")).strip().upper() != "Y":
        continue
    r = i + 1
    c = col - 1 if col else None
    lh = rec.get("Last Hit")
    print(f"- {rec.get('Description')!r}: record={lh!r} parsed={_parse_last_hit(str(lh or '').strip())} "
          f"formatted={formatted[r][c] if c is not None and c < len(formatted[r]) else None!r} "
          f"unformatted={unformatted[r][c] if c is not None and c < len(unformatted[r]) else None!r} "
          f"formula={formulas[r][c] if c is not None and c < len(formulas[r]) else None!r}")

ob = sheet.worksheet(OBSERVED_TAB)
rows = ob.get_all_values()
print("Observed header:", rows[0] if rows else None, "rows:", len(rows) - 1)
latest = defaultdict(str)
count = defaultdict(int)
for row in rows[1:]:
    if len(row) > 1:
        count[row[1]] += 1
        latest[row[1]] = max(latest[row[1]], row[0])
for d in sorted(count):
    print(f"  observed {d!r}: {count[d]} rows, max timestamp {latest[d]!r}")
print("Last 3 observed timestamps (raw):", [r[0] for r in rows[-3:]])
print("Last 3 observed timestamps (unformatted):",
      [r[0] for r in ob.get_all_values(value_render_option="UNFORMATTED_VALUE")[-3:]])
