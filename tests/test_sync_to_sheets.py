"""
Tests for pl/sync_to_sheets.py

Covers:
  - fetch_sales / fetch_purchases output shape and types
  - group value preservation when re-syncing (existing user edits survive)
  - P&L tab formula construction
  - Analytics app integrity (no P&L symbols remain after separation)

All DB and Sheets calls are mocked — no live credentials needed.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Import the module under test once here so patch() can resolve it by reference.
import pl.sync_to_sheets as sts


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_conn(rows):
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur_ctx = MagicMock()
    cur_ctx.__enter__ = MagicMock(return_value=cur)
    cur_ctx.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur_ctx
    return conn


def _make_worksheet(existing_rows):
    ws = MagicMock()
    ws.get_all_values.return_value = existing_rows
    return ws


def _make_doc(ws):
    doc = MagicMock()
    doc.worksheet.return_value = ws
    return doc


# ── fetch_sales ───────────────────────────────────────────────────────────────

class TestFetchSales(unittest.TestCase):

    FAKE_ROWS = [
        ("2026-05-20", "Topps Chrome Charizard", "45.00", "39.50", "ORD-001", ""),
        ("2026-05-19", "PSA 10 Pikachu",         "120.00", "104.30", "ORD-002", ""),
    ]

    def test_returns_list_of_lists(self):
        conn = _make_conn(self.FAKE_ROWS)
        with patch.object(sts, "get_connection", return_value=conn):
            result = sts.fetch_sales()
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], list)

    def test_each_row_has_six_columns(self):
        conn = _make_conn([self.FAKE_ROWS[0]])
        with patch.object(sts, "get_connection", return_value=conn):
            result = sts.fetch_sales()
        self.assertEqual(len(result[0]), 6)

    def test_group_column_starts_blank(self):
        conn = _make_conn([self.FAKE_ROWS[0]])
        with patch.object(sts, "get_connection", return_value=conn):
            result = sts.fetch_sales()
        self.assertEqual(result[0][5], "")

    def test_empty_result_when_no_orders(self):
        conn = _make_conn([])
        with patch.object(sts, "get_connection", return_value=conn):
            result = sts.fetch_sales()
        self.assertEqual(result, [])


# ── fetch_purchases ───────────────────────────────────────────────────────────

class TestFetchPurchases(unittest.TestCase):

    FAKE_ROWS = [
        ("2026-05-01", "Hobby Box Topps", "120.00", "ebay_purchase", "42", ""),
        ("2026-05-03", "PSA grading fee", "25.00",  "manual",        "43", ""),
    ]

    def test_returns_list_of_lists(self):
        conn = _make_conn(self.FAKE_ROWS)
        with patch.object(sts, "get_connection", return_value=conn):
            result = sts.fetch_purchases()
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], list)

    def test_each_row_has_six_columns(self):
        conn = _make_conn([self.FAKE_ROWS[0]])
        with patch.object(sts, "get_connection", return_value=conn):
            result = sts.fetch_purchases()
        self.assertEqual(len(result[0]), 6)

    def test_group_column_starts_blank(self):
        conn = _make_conn([self.FAKE_ROWS[0]])
        with patch.object(sts, "get_connection", return_value=conn):
            result = sts.fetch_purchases()
        self.assertEqual(result[0][5], "")

    def test_empty_result_when_no_purchases(self):
        conn = _make_conn([])
        with patch.object(sts, "get_connection", return_value=conn):
            result = sts.fetch_purchases()
        self.assertEqual(result, [])


# ── Batch value preservation ──────────────────────────────────────────────────

class TestBatchPreservation(unittest.TestCase):
    """
    When the user has typed group names into the sheet and re-runs the sync,
    those values must survive the overwrite.
    """

    def test_sales_group_values_preserved_on_resync(self):
        existing = [
            ["order_date", "title", "gross_sale", "net_payout", "order_id", "group"],
            ["2026-05-20", "Card A", "45.00", "39.50", "ORD-001", "May Break"],
            ["2026-05-19", "Card B", "30.00", "26.50", "ORD-002", ""],
        ]
        ws = _make_worksheet(existing)
        doc = _make_doc(ws)

        new_rows = [
            ["2026-05-20", "Card A", "45.00", "39.50", "ORD-001", ""],
            ["2026-05-19", "Card B", "30.00", "26.50", "ORD-002", ""],
            ["2026-05-18", "Card C", "60.00", "52.10", "ORD-003", ""],
        ]

        sts.write_sales_tab(doc, new_rows)

        self.assertEqual(new_rows[0][5], "May Break")  # ORD-001 preserved
        self.assertEqual(new_rows[1][5], "")            # ORD-002 had no group
        self.assertEqual(new_rows[2][5], "")            # ORD-003 is new

    def test_purchases_group_values_preserved_on_resync(self):
        existing = [
            ["purchase_date", "description", "total_cost", "source", "id", "group"],
            ["2026-05-01", "Hobby Box", "120.00", "ebay_purchase", "42", "May Break"],
            ["2026-05-03", "PSA fee",   "25.00",  "manual",        "43", ""],
        ]
        ws = _make_worksheet(existing)
        doc = _make_doc(ws)

        new_rows = [
            ["2026-05-01", "Hobby Box", "120.00", "ebay_purchase", "42", ""],
            ["2026-05-03", "PSA fee",   "25.00",  "manual",        "43", ""],
            ["2026-05-10", "New item",  "15.00",  "manual",        "44", ""],
        ]

        sts.write_purchases_tab(doc, new_rows)

        self.assertEqual(new_rows[0][5], "May Break")  # id 42 preserved
        self.assertEqual(new_rows[1][5], "")            # id 43 had no group
        self.assertEqual(new_rows[2][5], "")            # id 44 is new

    def test_new_order_gets_empty_group(self):
        existing = [
            ["order_date", "title", "gross_sale", "net_payout", "order_id", "group"],
            ["2026-05-20", "Card A", "45.00", "39.50", "ORD-001", "May Break"],
        ]
        ws = _make_worksheet(existing)
        doc = _make_doc(ws)

        new_rows = [["2026-05-22", "Card Z", "99.00", "86.00", "ORD-NEW", ""]]

        sts.write_sales_tab(doc, new_rows)

        self.assertEqual(new_rows[0][5], "")  # brand-new order has no group

    def test_empty_existing_sheet_does_not_crash(self):
        # First run: sheet has only the header row
        existing = [["order_date", "title", "gross_sale", "net_payout", "order_id", "group"]]
        ws = _make_worksheet(existing)
        doc = _make_doc(ws)

        new_rows = [["2026-05-20", "Card A", "45.00", "39.50", "ORD-001", ""]]

        sts.write_sales_tab(doc, new_rows)

        self.assertEqual(new_rows[0][5], "")


# ── P&L tab formula construction ─────────────────────────────────────────────

class TestPLTab(unittest.TestCase):

    def _make_doc_with_ws(self):
        ws = MagicMock()
        doc = MagicMock()
        doc.worksheet.return_value = ws
        return doc, ws

    def test_pl_tab_update_called_once(self):
        doc, ws = self._make_doc_with_ws()
        sts.write_pl_tab(doc,sales_row_count=5, purchases_row_count=3)
        ws.update.assert_called_once()

    def test_pl_tab_headers_correct(self):
        doc, ws = self._make_doc_with_ws()
        sts.write_pl_tab(doc,sales_row_count=5, purchases_row_count=3)

        written = ws.update.call_args[0][0]
        self.assertEqual(written[0], ["group", "gross_revenue", "net_revenue", "costs", "profit"])

    def test_pl_tab_data_row_has_five_formulas(self):
        doc, ws = self._make_doc_with_ws()
        sts.write_pl_tab(doc,sales_row_count=5, purchases_row_count=3)

        written = ws.update.call_args[0][0]
        self.assertEqual(len(written[1]), 5)

    def test_pl_tab_formulas_reference_both_tabs(self):
        doc, ws = self._make_doc_with_ws()
        sts.write_pl_tab(doc,sales_row_count=10, purchases_row_count=7)

        written      = ws.update.call_args[0][0]
        formula_row  = written[1]

        self.assertIn("Sales!",     formula_row[0])  # group formula covers Sales
        self.assertIn("Purchases!", formula_row[0])  # group formula covers Purchases
        self.assertIn("Sales!",     formula_row[1])  # gross_revenue pulls from Sales
        self.assertIn("Purchases!", formula_row[3])  # costs pull from Purchases



# ── Analytics app separation ──────────────────────────────────────────────────

class TestAnalyticsAppSeparation(unittest.TestCase):
    """Static check: analytics app source must have no P&L symbols."""

    APP_PATH = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "dashboard", "app.py"
    )

    def _source(self):
        with open(self.APP_PATH) as f:
            return f.read()

    def test_no_cost_entry_tab(self):
        self.assertNotIn("tab_cost",    self._source())
        self.assertNotIn("Cost Entry",  self._source())

    def test_no_pl_data_loaders(self):
        src = self._source()
        for symbol in [
            "load_sales", "load_import_queue", "load_groupes",
            "load_group_sales", "load_past_payment_methods",
            "load_past_group_names", "load_sale_allocations",
        ]:
            self.assertNotIn(symbol, src, msg=f"P&L loader '{symbol}' still in analytics app")

    def test_no_pl_mutators(self):
        src = self._source()
        for symbol in [
            "_create_group", "_update_group_sales", "_delete_group",
            "_save_allocation_from_queue", "_save_manual_cost",
            "_remove_allocation", "_update_queue_status",
        ]:
            self.assertNotIn(symbol, src, msg=f"P&L mutator '{symbol}' still in analytics app")

    def test_analytics_tabs_present(self):
        src = self._source()
        self.assertIn("tab_mc",             src)
        self.assertIn("tab_dive",           src)
        self.assertIn("Mission Control",    src)
        self.assertIn("Listing Deep Dive",  src)

    def test_only_two_tabs_defined(self):
        self.assertIn("tab_mc, tab_dive = st.tabs(", self._source())


if __name__ == "__main__":
    unittest.main(verbosity=2)
