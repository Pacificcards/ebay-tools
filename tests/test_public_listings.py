"""Tests for the public (view-only, shareable) listings sheet. All DB and Sheets calls are mocked."""

import os
import re
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from public_listings import sync_public_sheet as sps

ALLOW = {"261328": "Sports Cards", "183454": "TCG Cards"}
NOW = datetime(2026, 11, 12, 11, 5, tzinfo=timezone.utc)


def _conn(rows):
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = rows
    return conn, cur


class TestFetch(unittest.TestCase):

    def test_selects_only_public_columns(self):
        select = re.search(r"SELECT(.*?)FROM", sps.FETCH_SQL, re.S).group(1)
        cols = {c.strip() for c in select.split(",")}
        self.assertEqual(cols, {"listing_id", "title", "current_price", "start_time", "category_id"})
        for private in ("sku", "quantity_sold", "hide_reason", "impressions", "views"):
            self.assertNotIn(private, select)

    def test_filters_active_allowlisted_in_stock(self):
        sql = sps.FETCH_SQL
        self.assertIn("status = 'active'", sql)
        self.assertIn("category_id = ANY(%s)", sql)
        self.assertIn("COALESCE(quantity, 0) - COALESCE(quantity_sold, 0) > 0", sql)

    def test_allowlist_ids_passed_as_param(self):
        conn, cur = _conn([])
        sps.fetch_rows(conn, ALLOW)
        self.assertEqual(sorted(cur.execute.call_args[0][1][0]), ["183454", "261328"])

    def test_shipped_allowlist_loads(self):
        allow = sps.load_allowlist()
        self.assertTrue(allow)
        self.assertTrue(all(k.isdigit() for k in allow))


class TestBuildValues(unittest.TestCase):

    def test_layout_and_row(self):
        rows = [("1234", "Charizard 4/102", Decimal("199.99"),
                 datetime(2026, 11, 11, 2, 0, tzinfo=timezone.utc), "183454")]
        values = sps.build_values(rows, ALLOW, NOW)
        self.assertEqual(values[0][0], "Last updated: Nov 12, 2026 3:05 AM PT")
        self.assertEqual(values[1], sps.HEADER)
        # 02:00 UTC on 11/11 is still 11/10 in Pacific time.
        self.assertEqual(values[2], ["Charizard 4/102", 199.99, "2026-11-10", "TCG Cards",
                                     "https://www.ebay.com/itm/1234"])

    def test_formula_like_titles_neutralized(self):
        rows = [("1", "=IMPORTXML(\"x\")", None, None, "261328"), ("2", "+1 Lot", None, None, "261328")]
        values = sps.build_values(rows, ALLOW, NOW)
        self.assertEqual(values[2][0], "'=IMPORTXML(\"x\")")
        self.assertEqual(values[3][0], "'+1 Lot")
        self.assertEqual(values[2][1:3], ["", ""])


class TestMain(unittest.TestCase):

    def _run(self, rows, env):
        conn, cur = _conn(rows)
        # First query: the per-category log; second: the listings themselves.
        cur.fetchall.side_effect = [[("261328", "Sports:Singles", len(rows))], rows]
        with patch.object(sps, "get_connection", return_value=conn), \
             patch.object(sps, "load_allowlist", return_value=ALLOW), \
             patch.object(sps, "_open_worksheet") as open_ws, \
             patch.dict(os.environ, env, clear=False):
            rc = sps.main([])
        return rc, open_ws

    def test_zero_rows_does_not_touch_sheet(self):
        rc, open_ws = self._run([], {"PUBLIC_LISTINGS_SHEET_ID": "abc"})
        self.assertEqual(rc, 0)
        open_ws.assert_not_called()

    def test_missing_sheet_id_skips(self):
        rows = [("1", "t", Decimal("1"), None, "261328")]
        rc, open_ws = self._run(rows, {"PUBLIC_LISTINGS_SHEET_ID": ""})
        self.assertEqual(rc, 0)
        open_ws.assert_not_called()

    def test_writes_then_clears_leftover_rows(self):
        rows = [("1", "t", Decimal("1"), None, "261328")] * 3
        rc, open_ws = self._run(rows, {"PUBLIC_LISTINGS_SHEET_ID": "abc"})
        self.assertEqual(rc, 0)
        ws = open_ws.return_value
        ws.update.assert_called_once()
        self.assertEqual(len(ws.update.call_args[0][0]), 5)  # stamp + header + 3 rows
        ws.batch_clear.assert_called_once_with(["A6:E"])


if __name__ == "__main__":
    unittest.main()
