"""TEMPORARY read-only diagnostic for the stale check — delete before merging.

Runs the new stale logic against live Supabase + the live Watchlist, prints the result,
sends nothing to Discord and writes nothing.
"""
import os
from datetime import datetime, timezone

from listener.main import _last_hits, _stale_cards
from listener.sheets import load_watchlist
from shared.db import get_connection

watchlist = load_watchlist(os.environ["GOOGLE_SHEET_ID"])
conn = get_connection()
hits = _last_hits(conn)
conn.close()
today = datetime.now(timezone.utc).date()
for row in watchlist:
    if str(row.get("Active (Y/N)", "")).strip().upper() == "Y":
        d = row.get("Description", "").strip()
        print(f"- {d!r}: last alert in DB = {hits.get(d.lower())}, sheet Last Hit = {row.get('Last Hit')!r}")
print("Would flag as stale:", _stale_cards(watchlist, hits, today))
