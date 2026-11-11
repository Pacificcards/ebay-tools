"""
Tests for the listener's daily stale-card check.

The check reads each card's most recent alert from listener_seen_items (Supabase), not the
sheet's Last Hit column — rows added via Discord ingestion had no formula there, so every
such card was reported stale even when it had fresh hits. All DB calls are mocked.
"""

import os
import sys
import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from listener import main

TODAY = date(2026, 11, 11)


def _row(desc, active="Y", last_hit_cell=""):
    return {"Active (Y/N)": active, "Description": desc, "Last Hit": last_hit_cell}


class TestStaleCards(unittest.TestCase):

    def test_recent_hit_not_stale_even_with_blank_sheet_cell(self):
        # The original bug: blank Last Hit cell, but the card alerted today.
        hits = {"blastoise ex 200": TODAY}
        self.assertEqual(main._stale_cards([_row("Blastoise ex 200")], hits, TODAY), [])

    def test_never_hit_is_stale(self):
        self.assertEqual(main._stale_cards([_row("Charizard SWSH262")], {}, TODAY), ["Charizard SWSH262"])

    def test_threshold_is_three_days(self):
        rows = [_row("a"), _row("b")]
        hits = {"a": date(2026, 11, 8), "b": date(2026, 11, 7)}  # 3 days ago ok, 4 days ago stale
        self.assertEqual(main._stale_cards(rows, hits, TODAY), ["b"])

    def test_inactive_and_blank_rows_skipped(self):
        rows = [_row("old card", active="N"), _row("  ")]
        self.assertEqual(main._stale_cards(rows, {}, TODAY), [])

    def test_description_match_ignores_case_and_spaces(self):
        hits = {"pikachu 173": TODAY}
        self.assertEqual(main._stale_cards([_row("  Pikachu 173 ")], hits, TODAY), [])


class TestLastHits(unittest.TestCase):

    def test_reads_latest_alert_per_description(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [
            ("Blastoise ex 200", datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)),
            ("pikachu 173 ", datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc)),
        ]
        self.assertEqual(main._last_hits(conn), {
            "blastoise ex 200": date(2026, 11, 11),
            "pikachu 173": date(2026, 8, 24),
        })
        self.assertIn("MAX(first_seen_at)", cur.execute.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
