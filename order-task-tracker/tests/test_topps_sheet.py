"""
Tests for topps_sheet: which emails count, email -> rows, sheet layout, and the
never-twice claim logic. No network, DB, Gmail, Sheets or Claude calls.
"""

import os
import sys
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import topps_sheet  # noqa: E402
from orders import PACIFIC  # noqa: E402
from topps_sheet import Row, build_requests, find_layout, plan_rows  # noqa: E402

SHOPIFY = "Topps <store+66297495709@t.shopifyemail.com>"


class TestWhichEmails(unittest.TestCase):
    def test_senders(self):
        self.assertTrue(topps_sheet.is_topps_sender(SHOPIFY))
        self.assertTrue(topps_sheet.is_topps_sender("Topps <orders@topps.com>"))
        self.assertTrue(topps_sheet.is_topps_sender("Topps <no-reply@mail.runfair.com>"))
        # another Shopify store, even one calling itself Topps
        self.assertFalse(topps_sheet.is_topps_sender("Topps <store+123@t.shopifyemail.com>"))
        self.assertFalse(topps_sheet.is_topps_sender("Fanatics <orders@fanatics.com>"))
        self.assertFalse(topps_sheet.is_topps_sender("x <a@nottopps.com>"))

    def test_subject_must_match_exactly(self):
        self.assertEqual(topps_sheet.subject_order_number("Order US-14426844-S confirmed"), "US-14426844-S")
        self.assertEqual(topps_sheet.subject_order_number("Order #1234 confirmed"), "1234")
        for subject in ["Re: Order US-1 confirmed", "Order US-1 confirmed - thanks!", "Your order US-1 confirmed",
                        "Order US-1 has shipped", "Order US-1 cancelled", "order US-1 confirmed",
                        "A shipment from order US-1 is on the way"]:
            self.assertIsNone(topps_sheet.subject_order_number(subject), subject)


def _ex(items, subtotal):
    return {"order_number": "US-1", "subtotal": subtotal,
            "items": [{"name": n, "qty": q, "line_total": t} for n, q, t in items]}


class TestPlanRows(unittest.TestCase):
    def test_each_product_its_own_row(self):
        rows, problem = plan_rows(_ex([("2026 Topps Chrome® Tennis - Hobby Box", 4, "$379.96"),
                                       ("2026 Bowman Football - Mega Box", 2, "$129.98")], "$509.94"))
        self.assertIsNone(problem)
        self.assertEqual(rows, [Row("2026 Topps Chrome Tennis - Hobby Box", Decimal("94.99"), 4),
                                Row("2026 Bowman Football - Mega Box", Decimal("64.99"), 2)])

    def test_thousands_and_rounding(self):
        rows, _ = plan_rows(_ex([("Mega Case", 2, "$2,599.00"), ("Odd", 3, "$100.00")], "$2,699.00"))
        self.assertEqual([r.unit_price for r in rows], [Decimal("1299.50"), Decimal("33.33")])

    def test_symbols_dropped(self):
        self.assertEqual(topps_sheet.clean_name("Topps™ Chrome®  Star Wars "), "Topps Chrome Star Wars")

    def test_doubtful_numbers_mean_no_rows(self):
        cases = [
            _ex([("A", 1, "$10.00")], "$11.00"),        # lines don't add up to subtotal
            _ex([("A", 1, "$10.00")], None),            # nothing to check against
            _ex([("A", 1, None)], "$10.00"),            # missing line price
            _ex([("A", 0, "$10.00")], "$10.00"),        # bad quantity
            _ex([("", 1, "$10.00")], "$10.00"),         # no name
            _ex([], "$0.00"),
        ]
        for ex in cases:
            rows, problem = plan_rows(ex)
            self.assertEqual(rows, [], ex)
            self.assertTrue(problem, ex)


# The live sheet's shape (2026-10-01): total in O1, header row 2, active rows, two name-only
# rows, blanks, then the Sold table with its own header.
SHEET = (
    [["", "", "", "", "", "", "", "", "", "", "", "", "", "", "=SUM(O3:O30)"],
     ["", "Category", "Year", "Set", "Format", "Preorder MSRP", "Preorder Date", "Release Date", "Age",
      "30 Days After", "Status", "Incoming", "On Hand", "Sold", "Inventory Value"]]
    + [["", "Baseball", "2026", f"Set {i}", "Hobby", "100", "", "", f"=TODAY()-H{i}", f"=H{i}+30",
        "", "1", "", "", f"=(L{i}+M{i})*F{i}"] for i in range(3, 27)]
    + [["", "", "", "Topps X Jennie"], ["", "", "", "Topps Samurai Packs"], [], [], [], []]
    + [["", "Category", "Year", "Set", "Format"], ["", "Football", "2026", "Flagship Football"]]
)


class TestLayout(unittest.TestCase):
    def test_new_rows_go_after_last_active_row(self):
        layout = find_layout(SHEET, 7)
        self.assertEqual(layout.insert_at, 28)            # 0-based -> sheet row 29, after "Samurai Packs"
        self.assertEqual(layout.formula_rows, {"I": 25, "J": 25, "O": 25})   # last row that has them
        self.assertEqual(layout.total_formula, "=SUM(O3:O30)")

    def test_no_header_is_an_error(self):
        with self.assertRaises(RuntimeError):
            find_layout([["a"], ["b"]], 0)

    def test_requests(self):
        layout = find_layout(SHEET, 7)
        reqs = build_requests(layout, [Row("A", Decimal("94.99"), 4), Row("B", Decimal("64.99"), 2)])
        ins = reqs[0]["insertDimension"]["range"]
        self.assertEqual((ins["startIndex"], ins["endIndex"]), (28, 30))
        cells = [(r["updateCells"]["range"]["startRowIndex"], r["updateCells"]["range"]["startColumnIndex"],
                  r["updateCells"]["rows"][0]["values"][0]["userEnteredValue"])
                 for r in reqs if "updateCells" in r]
        self.assertEqual(cells, [(28, 3, {"stringValue": "A"}), (28, 5, {"numberValue": 94.99}),
                                 (28, 11, {"numberValue": 4}),
                                 (29, 3, {"stringValue": "B"}), (29, 5, {"numberValue": 64.99}),
                                 (29, 11, {"numberValue": 2})])
        pastes = [r["copyPaste"] for r in reqs if "copyPaste" in r]
        self.assertEqual(len(pastes), 3)
        self.assertTrue(all(p["pasteType"] == "PASTE_FORMULA" and p["source"]["startRowIndex"] == 25
                            and p["destination"]["startRowIndex"] == 28
                            and p["destination"]["endRowIndex"] == 30 for p in pastes))


# ── never twice ──────────────────────────────────────────────────────────────

GOOD = _ex([("2026 Bowman Football - Hobby Box", 2, "$579.98")], "$579.98")


class FakeDB:
    """order_tracker_sheet_orders in memory, honoring both unique keys."""

    def __init__(self, claims=()):
        self.claims = {c["message_id"]: dict(c) for c in claims}


class TestRun(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.emails = {}
        p = [
            patch.object(topps_sheet, "_claimed", side_effect=lambda conn, ids: (
                set(self.db.claims), {c["order_number"] for c in self.db.claims.values()})),
            patch.object(topps_sheet, "_claim", side_effect=self._claim),
            patch.object(topps_sheet, "_set_status", side_effect=lambda conn, mid, status, d:
                         self.db.claims[mid].update(status=status)),
            patch.object(topps_sheet.gmail_search, "search_ids", side_effect=lambda s, q: list(self.emails)),
            patch.object(topps_sheet.gmail_search, "fetch_headers", side_effect=lambda s, mid: dict(self.emails[mid])),
            patch.object(topps_sheet.gmail_search, "fetch_body", return_value="body"),
            patch.object(topps_sheet, "extract", return_value=GOOD),
            patch.object(topps_sheet, "sheets_session", return_value=MagicMock()),
        ]
        for x in p:
            x.start()
            self.addCleanup(x.stop)
        self.write = patch.object(topps_sheet, "write_rows", return_value=29).start()
        self.addCleanup(patch.stopall)

    def _claim(self, conn, mid, number, status, detail):
        if mid in self.db.claims or number in {c["order_number"] for c in self.db.claims.values()}:
            return False
        self.db.claims[mid] = {"message_id": mid, "order_number": number, "status": status}
        return True

    def _email(self, mid, subject="Order US-1 confirmed", sender=SHOPIFY, when="2026-10-02T09:00:00"):
        self.emails[mid] = {"id": mid, "from": sender, "subject": subject,
                            "received": datetime.fromisoformat(when).replace(tzinfo=PACIFIC)}

    def test_written_once_across_runs(self):
        self._email("m1")
        topps_sheet.run(None, None, dry_run=False)
        topps_sheet.run(None, None, dry_run=False)
        self.assertEqual(self.write.call_count, 1)
        self.assertEqual(self.db.claims["m1"]["status"], "written")

    def test_resent_confirmation_for_same_order_skipped(self):
        self._email("m1")
        self._email("m2")                                   # same subject/order, different message
        topps_sheet.run(None, None, dry_run=False)
        self.assertEqual(self.write.call_count, 1)

    def test_failed_write_is_reported_and_never_retried(self):
        self._email("m1")
        self.write.side_effect = RuntimeError("HTTP 500")
        lines = topps_sheet.run(None, None, dry_run=False)
        self.assertIn("FAILED", lines[0])
        self.assertEqual(self.db.claims["m1"]["status"], "pending")
        self.write.side_effect = None
        topps_sheet.run(None, None, dry_run=False)
        self.assertEqual(self.write.call_count, 1)          # not attempted again

    def test_claude_failure_retried_next_run(self):
        self._email("m1")
        with patch.object(topps_sheet, "extract", side_effect=topps_sheet.claude_cli.ClaudeError("x")):
            topps_sheet.run(None, None, dry_run=False)
        self.assertEqual(self.db.claims, {})
        topps_sheet.run(None, None, dry_run=False)
        self.assertEqual(self.write.call_count, 1)

    def test_doubtful_prices_not_written_and_reported_once(self):
        self._email("m1")
        with patch.object(topps_sheet, "extract", return_value=_ex([("A", 1, "$10.00")], "$11.00")):
            lines = topps_sheet.run(None, None, dry_run=False)
            self.assertIn("NOT added", lines[0])
            self.assertEqual(topps_sheet.run(None, None, dry_run=False), [])
        self.write.assert_not_called()
        self.assertEqual(self.db.claims["m1"]["status"], "needs_review")

    def test_non_matching_emails_ignored(self):
        self._email("a", subject="Order US-1 has shipped")
        self._email("b", sender="Topps <store+999@t.shopifyemail.com>")
        self._email("c", when="2026-09-30T09:00:00")         # before TOPPS_SHEET_START: no backfill
        self.assertEqual(topps_sheet.run(None, None, dry_run=False), [])
        self.write.assert_not_called()
        self.assertEqual(self.db.claims, {})

    def test_dry_run_writes_and_claims_nothing(self):
        self._email("m1")
        with patch.object(topps_sheet, "read_layout", return_value=find_layout(SHEET, 7)):
            lines = topps_sheet.run(None, None, dry_run=True)
        self.assertIn("would add at row 29", lines[0])
        self.write.assert_not_called()
        self.assertEqual(self.db.claims, {})


if __name__ == "__main__":
    unittest.main()
