"""
Tests for pl/sync_to_sheets.py

Covers:
  - fetch_sales / fetch_purchases output shape and types
  - group value preservation when re-syncing (existing user edits survive)
  - P&L tab formula construction
  - Analytics app integrity (no P&L symbols remain after separation)
  - New Entries: record_id stamping on insert, deletion workflow

All DB and Sheets calls are mocked — no live credentials needed.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

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
    # 9-column schema: order_date | title | gross_sale | net_payout | shipping_cost
    #                  | order_id | ebay_order_id | group | source
    FAKE_ROWS = [
        ("2026-05-20", "Topps Chrome Charizard", "45.00", "39.50", "3.50", "ORD-001", "ORD-001", "", "eBay"),
        ("2026-05-19", "PSA 10 Pikachu",         "120.00", "104.30", "5.20", "ORD-002", "ORD-002", "", "eBay"),
    ]

    def test_returns_list_of_lists(self):
        conn = _make_conn(self.FAKE_ROWS)
        result = sts.fetch_sales(conn)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], list)

    def test_each_row_has_nine_columns(self):
        conn = _make_conn([self.FAKE_ROWS[0]])
        result = sts.fetch_sales(conn)
        self.assertEqual(len(result[0]), 9)

    def test_group_column_starts_blank(self):
        conn = _make_conn([self.FAKE_ROWS[0]])
        result = sts.fetch_sales(conn)
        self.assertEqual(result[0][7], "")  # group is index 7

    def test_empty_result_when_no_orders(self):
        conn = _make_conn([])
        result = sts.fetch_sales(conn)
        self.assertEqual(result, [])


# ── fetch_purchases ───────────────────────────────────────────────────────────

class TestFetchPurchases(unittest.TestCase):
    # 7-column schema: purchase_date | description | vendor | total_cost | source | id | group
    FAKE_ROWS = [
        ("2026-05-01", "Hobby Box Topps", "eBay",   "120.00", "ebay_purchase", "42", ""),
        ("2026-05-03", "PSA grading fee", "manual",  "25.00", "manual",        "43", ""),
    ]

    def test_returns_list_of_lists(self):
        conn = _make_conn(self.FAKE_ROWS)
        result = sts.fetch_purchases(conn)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], list)

    def test_each_row_has_seven_columns(self):
        conn = _make_conn([self.FAKE_ROWS[0]])
        result = sts.fetch_purchases(conn)
        self.assertEqual(len(result[0]), 7)

    def test_group_column_starts_blank(self):
        conn = _make_conn([self.FAKE_ROWS[0]])
        result = sts.fetch_purchases(conn)
        self.assertEqual(result[0][6], "")  # group is index 6

    def test_empty_result_when_no_purchases(self):
        conn = _make_conn([])
        result = sts.fetch_purchases(conn)
        self.assertEqual(result, [])


# ── Batch value preservation ──────────────────────────────────────────────────

class TestBatchPreservation(unittest.TestCase):
    """
    When the user has typed group names into the sheet and re-runs the sync,
    those values must survive the overwrite.
    """

    def test_sales_group_values_preserved_on_resync(self):
        # existing sheet rows use the current header-based schema
        existing = [
            ["order_date", "title", "gross_sale", "net_payout", "shipping_cost", "order_id", "ebay_order_id", "group", "source"],
            ["2026-05-20", "Card A", "45.00", "39.50", "3.50", "ORD-001", "ORD-001", "May Break", "eBay"],
            ["2026-05-19", "Card B", "30.00", "26.50", "2.10", "ORD-002", "ORD-002", "",          "eBay"],
        ]
        ws = _make_worksheet(existing)
        doc = _make_doc(ws)

        # new_rows match the 9-column fetch_sales() output
        new_rows = [
            ["2026-05-20", "Card A", "45.00", "39.50", "3.50", "ORD-001", "ORD-001", "", "eBay"],
            ["2026-05-19", "Card B", "30.00", "26.50", "2.10", "ORD-002", "ORD-002", "", "eBay"],
            ["2026-05-18", "Card C", "60.00", "52.10", "4.00", "ORD-003", "ORD-003", "", "eBay"],
        ]

        sts.write_sales_tab(doc, new_rows)

        self.assertEqual(new_rows[0][7], "May Break")  # ORD-001 preserved
        self.assertEqual(new_rows[1][7], "")            # ORD-002 had no group
        self.assertEqual(new_rows[2][7], "")            # ORD-003 is new

    def test_purchases_group_values_preserved_on_resync(self):
        existing = [
            ["purchase_date", "description", "vendor", "total_cost", "source", "id", "group"],
            ["2026-05-01", "Hobby Box", "eBay",   "120.00", "ebay_purchase", "42", "May Break"],
            ["2026-05-03", "PSA fee",   "manual",  "25.00", "manual",        "43", ""],
        ]
        ws = _make_worksheet(existing)
        doc = _make_doc(ws)

        new_rows = [
            ["2026-05-01", "Hobby Box", "eBay",   "120.00", "ebay_purchase", "42", ""],
            ["2026-05-03", "PSA fee",   "manual",  "25.00", "manual",        "43", ""],
            ["2026-05-10", "New item",  "manual",  "15.00", "manual",        "44", ""],
        ]

        sts.write_purchases_tab(doc, new_rows)

        self.assertEqual(new_rows[0][6], "May Break")  # id 42 preserved
        self.assertEqual(new_rows[1][6], "")            # id 43 had no group
        self.assertEqual(new_rows[2][6], "")            # id 44 is new

    def test_new_order_gets_empty_group(self):
        existing = [
            ["order_date", "title", "gross_sale", "net_payout", "shipping_cost", "order_id", "ebay_order_id", "group", "source"],
            ["2026-05-20", "Card A", "45.00", "39.50", "3.50", "ORD-001", "ORD-001", "May Break", "eBay"],
        ]
        ws = _make_worksheet(existing)
        doc = _make_doc(ws)

        new_rows = [["2026-05-22", "Card Z", "99.00", "86.00", "6.00", "ORD-NEW", "ORD-NEW", "", "eBay"]]

        sts.write_sales_tab(doc, new_rows)

        self.assertEqual(new_rows[0][7], "")  # brand-new order has no group

    def test_empty_existing_sheet_does_not_crash(self):
        # First run: sheet has only the header row
        existing = [["order_date", "title", "gross_sale", "net_payout", "shipping_cost", "order_id", "ebay_order_id", "group", "source"]]
        ws = _make_worksheet(existing)
        doc = _make_doc(ws)

        new_rows = [["2026-05-20", "Card A", "45.00", "39.50", "3.50", "ORD-001", "ORD-001", "", "eBay"]]

        sts.write_sales_tab(doc, new_rows)

        self.assertEqual(new_rows[0][7], "")

    def test_old_6col_schema_groups_preserved(self):
        # Backwards compat: sheet written before the column expansion still preserves groups
        existing = [
            ["order_date", "title", "gross_sale", "net_payout", "order_id", "group"],
            ["2026-05-20", "Card A", "45.00", "39.50", "ORD-001", "May Break"],
        ]
        ws = _make_worksheet(existing)
        doc = _make_doc(ws)

        new_rows = [["2026-05-20", "Card A", "45.00", "39.50", "3.50", "ORD-001", "ORD-001", "", "eBay"]]

        sts.write_sales_tab(doc, new_rows)

        self.assertEqual(new_rows[0][7], "May Break")


# ── P&L tab formula construction ─────────────────────────────────────────────

class TestPLTab(unittest.TestCase):

    def _make_doc_with_ws(self):
        ws = MagicMock()
        doc = MagicMock()
        doc.worksheet.return_value = ws
        return doc, ws

    def test_pl_tab_update_called_once(self):
        doc, ws = self._make_doc_with_ws()
        sts.write_pl_tab(doc, sales_row_count=5, purchases_row_count=3, ad_fees_row_count=10)
        ws.update.assert_called_once()

    def test_pl_tab_headers_correct(self):
        doc, ws = self._make_doc_with_ws()
        sts.write_pl_tab(doc, sales_row_count=5, purchases_row_count=3, ad_fees_row_count=10)

        written = ws.update.call_args[0][0]
        self.assertEqual(written[0], ["group", "net_payout", "costs", "ad_fees", "shipping_cost", "profit"])

    def test_pl_tab_data_row_has_six_formulas(self):
        doc, ws = self._make_doc_with_ws()
        sts.write_pl_tab(doc, sales_row_count=5, purchases_row_count=3, ad_fees_row_count=10)

        written = ws.update.call_args[0][0]
        self.assertEqual(len(written[1]), 6)

    def test_pl_tab_formulas_reference_correct_tabs(self):
        doc, ws = self._make_doc_with_ws()
        sts.write_pl_tab(doc, sales_row_count=10, purchases_row_count=7, ad_fees_row_count=20)

        written      = ws.update.call_args[0][0]
        formula_row  = written[1]

        self.assertIn("Sales!",     formula_row[0])  # group formula covers Sales
        self.assertIn("Purchases!", formula_row[0])  # group formula covers Purchases
        self.assertIn("Sales!D",    formula_row[1])  # net_payout pulls from Sales col D
        self.assertIn("Purchases!", formula_row[2])  # costs pull from Purchases
        self.assertIn("BYROW",      formula_row[3])  # ad_fees uses BYROW+LAMBDA
        self.assertIn("Ad Fee",     formula_row[3])  # ad_fees filters by "Ad Fee" category
        self.assertIn("BYROW",      formula_row[4])  # shipping_cost uses BYROW+LAMBDA
        self.assertIn("Shipping",   formula_row[4])  # shipping_cost filters by "Shipping" category
        self.assertIn("B2:B-C2:C-D2:D-E2:E", formula_row[5])  # profit = net - costs - ads - shipping



# ── New Entries: record_id and deletion ──────────────────────────────────────

class TestNewEntriesRecordId(unittest.TestCase):
    """Record ID is stamped after successful insert; deletion flow works end-to-end."""

    def _make_ws(self, rows):
        ws = MagicMock()
        ws.get_all_values.return_value = rows
        return ws

    def _make_doc(self, ws):
        doc = MagicMock()
        doc.worksheet.return_value = ws
        return doc

    def test_record_id_stamped_after_purchase_insert(self):
        """Successful purchase insert writes record_id to col 9."""
        rows = [
            sts.NEW_ENTRIES_HEADERS,
            ["2026-06-20", "Test purchase", "purchase", "50.00", "", "", "", "", ""],
        ]
        ws  = self._make_ws(rows)
        doc = self._make_doc(ws)

        with patch.object(sts, "_insert_manual_entries", return_value={0: "42"}) as mock_ins, \
             patch.object(sts, "_insert_manual_sales",   return_value={}):
            sts.process_new_entries(doc)

        mock_ins.assert_called_once()
        ws.batch_update.assert_called_once()
        updates = ws.batch_update.call_args[0][0]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["range"], "H2:I2")
        self.assertIn("42", updates[0]["values"][0])

    def test_record_id_stamped_after_sale_insert(self):
        """Successful sale insert writes MANUAL- order_id to col 9."""
        rows = [
            sts.NEW_ENTRIES_HEADERS,
            ["2026-06-20", "Test sale", "sale", "100.00", "", "", "", "", ""],
        ]
        ws  = self._make_ws(rows)
        doc = self._make_doc(ws)

        with patch.object(sts, "_insert_manual_entries", return_value={}), \
             patch.object(sts, "_insert_manual_sales",   return_value={0: "MANUAL-abc123"}):
            sts.process_new_entries(doc)

        ws.batch_update.assert_called_once()
        updates = ws.batch_update.call_args[0][0]
        self.assertEqual(len(updates), 1)
        self.assertIn("MANUAL-abc123", updates[0]["values"][0])

    def test_marked_for_deletion_calls_delete_and_stamps_deleted(self):
        """Row with 'Marked for Deletion' status triggers deletion and stamps 'Deleted …'."""
        rows = [
            sts.NEW_ENTRIES_HEADERS,
            ["2026-06-20", "Old purchase", "purchase", "50.00", "", "", "", "Marked for Deletion", "42"],
        ]
        ws  = self._make_ws(rows)
        doc = self._make_doc(ws)

        with patch.object(sts, "_delete_manual_entry", return_value=(True, "")) as mock_del, \
             patch.object(sts, "_insert_manual_entries", return_value={}), \
             patch.object(sts, "_insert_manual_sales",   return_value={}):
            sts.process_new_entries(doc)

        mock_del.assert_called_once_with("42")
        status_call = ws.update_cell.call_args_list[0]
        self.assertIn("Deleted", status_call[0][2])

    def test_marked_for_deletion_no_record_id_stamps_error(self):
        """Deletion without a Record ID stamps an error."""
        rows = [
            sts.NEW_ENTRIES_HEADERS,
            ["2026-06-20", "Old entry", "purchase", "50.00", "", "", "", "Marked for Deletion", ""],
        ]
        ws  = self._make_ws(rows)
        doc = self._make_doc(ws)

        with patch.object(sts, "_delete_manual_entry") as mock_del, \
             patch.object(sts, "_insert_manual_entries", return_value={}), \
             patch.object(sts, "_insert_manual_sales",   return_value={}):
            sts.process_new_entries(doc)

        mock_del.assert_not_called()
        status_call = ws.update_cell.call_args_list[0]
        self.assertIn("No Record ID", status_call[0][2])

    def test_marked_for_deletion_failure_stamps_error(self):
        """Failed deletion stamps the error message from _delete_manual_entry."""
        rows = [
            sts.NEW_ENTRIES_HEADERS,
            ["2026-06-20", "eBay order", "sale", "100.00", "", "", "", "Marked for Deletion", "MANUAL-abc"],
        ]
        ws  = self._make_ws(rows)
        doc = self._make_doc(ws)

        with patch.object(sts, "_delete_manual_entry", return_value=(False, "Not found or not a manual entry")), \
             patch.object(sts, "_insert_manual_entries", return_value={}), \
             patch.object(sts, "_insert_manual_sales",   return_value={}):
            sts.process_new_entries(doc)

        status_call = ws.update_cell.call_args_list[0]
        self.assertIn("Not found", status_call[0][2])

    def test_record_id_stamped_after_shipping_insert(self):
        """Successful shipping insert writes SHIP- txn_id to col 9."""
        rows = [
            sts.NEW_ENTRIES_HEADERS,
            ["2026-06-20", "PirateShip batch", "shipping", "12.50", "", "", "Pokemon Repacks", "", ""],
        ]
        ws  = self._make_ws(rows)
        doc = self._make_doc(ws)

        with patch.object(sts, "_insert_manual_entries",  return_value={}), \
             patch.object(sts, "_insert_manual_sales",    return_value={}), \
             patch.object(sts, "_insert_manual_shipping", return_value={0: "SHIP-abc123"}) as mock_ship:
            sts.process_new_entries(doc)

        mock_ship.assert_called_once()
        ws.batch_update.assert_called_once()
        updates = ws.batch_update.call_args[0][0]
        self.assertEqual(len(updates), 1)
        self.assertIn("SHIP-abc123", updates[0]["values"][0])

    def test_shipping_deletion_calls_delete_and_stamps_deleted(self):
        """Row with SHIP- record_id and 'Marked for Deletion' deletes from order_fees."""
        rows = [
            sts.NEW_ENTRIES_HEADERS,
            ["2026-06-20", "PirateShip", "shipping", "12.50", "", "", "Pokemon Repacks", "Marked for Deletion", "SHIP-abc123"],
        ]
        ws  = self._make_ws(rows)
        doc = self._make_doc(ws)

        with patch.object(sts, "_delete_manual_entry", return_value=(True, "")) as mock_del, \
             patch.object(sts, "_insert_manual_entries",  return_value={}), \
             patch.object(sts, "_insert_manual_sales",    return_value={}), \
             patch.object(sts, "_insert_manual_shipping", return_value={}):
            sts.process_new_entries(doc)

        mock_del.assert_called_once_with("SHIP-abc123")
        status_call = ws.update_cell.call_args_list[0]
        self.assertIn("Deleted", status_call[0][2])

    def test_already_synced_row_skipped(self):
        """Rows with a non-deletion status are not re-processed."""
        rows = [
            sts.NEW_ENTRIES_HEADERS,
            ["2026-06-20", "Old entry", "purchase", "50.00", "", "", "", "✓ Synced 2026-06-20", "42"],
        ]
        ws  = self._make_ws(rows)
        doc = self._make_doc(ws)

        with patch.object(sts, "_insert_manual_entries", return_value={}) as mock_ins, \
             patch.object(sts, "_insert_manual_sales",   return_value={}):
            count = sts.process_new_entries(doc)

        mock_ins.assert_not_called()  # early return — nothing to insert
        self.assertEqual(count, 0)
        ws.update_cell.assert_not_called()


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
