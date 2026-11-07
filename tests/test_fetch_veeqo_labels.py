"""Tests for traffic_analytics.fetch_veeqo_labels. HTTP and DB are mocked."""

import os
import sys
import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from traffic_analytics import fetch_veeqo_labels as fv


def _order(number="02-15405-69621", channel="ebay", shipments=None):
    return {
        "number": number,
        "channel": {"type_code": channel},
        "allocations": [{"shipment": s} for s in (shipments or [])],
    }


def _ship(sid=1, cost="13.48", cancelled=False, created="2026-11-05T01:30:00Z"):
    return {
        "id": sid,
        "created_at": created,
        "outbound_label_charges": {"value": cost},
        "tracking_number": {"cancelled": cancelled},
    }


class TestLabelRows(unittest.TestCase):
    def test_ebay_order_uses_ebay_order_id(self):
        rows = fv._label_rows(_order(shipments=[_ship(sid=77)]))
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["order_id"], "02-15405-69621")
        self.assertEqual(r["billing_transaction_id"], "VEEQO-77")
        self.assertEqual((r["fee_type"], r["booking_entry"], r["amount"]), ("SHIPPING_LABEL", "DEBIT", 13.48))

    def test_direct_order_gets_made_up_id(self):
        rows = fv._label_rows(_order(number="#P-2245589525", channel="direct", shipments=[_ship()]))
        self.assertEqual(rows[0]["order_id"], "VEEQO-P-2245589525")

    def test_skips_zero_null_and_cancelled(self):
        shipments = [
            _ship(sid=1, cost=None),
            _ship(sid=2, cost="0"),
            _ship(sid=3, cancelled=True),
            _ship(sid=4, cost="5.64"),
        ]
        rows = fv._label_rows(_order(shipments=shipments))
        self.assertEqual([r["billing_transaction_id"] for r in rows], ["VEEQO-4"])

    def test_skips_allocation_without_shipment_and_missing_charges(self):
        order = _order()
        order["allocations"] = [{"shipment": None}, {"shipment": {"id": 9, "outbound_label_charges": None}}]
        self.assertEqual(fv._label_rows(order), [])

    def test_date_converted_to_pacific(self):
        # 01:30 UTC on Nov 5 is still Nov 4 evening in Pacific time.
        self.assertEqual(fv._pacific_date("2026-11-05T01:30:00Z"), date(2026, 11, 4))
        self.assertEqual(fv._pacific_date("2026-11-05T20:00:00.000Z"), date(2026, 11, 5))
        self.assertIsNone(fv._pacific_date(None))


class TestFetchOrders(unittest.TestCase):
    def test_pages_until_short_page(self):
        full = [{"number": str(i)} for i in range(fv.PAGE_SIZE)]
        short = [{"number": "x"}]
        with patch.object(fv, "_get", side_effect=[full, short]) as get:
            orders = fv._fetch_orders("key", datetime(2026, 11, 4, tzinfo=timezone.utc))
        self.assertEqual(len(orders), fv.PAGE_SIZE + 1)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].args[2]["updated_at_min"], "2026-11-04 00:00:00")

    def test_get_retries_on_429(self):
        limited = MagicMock(status_code=429)
        ok = MagicMock(status_code=200)
        ok.json.return_value = [{"number": "1"}]
        with patch.object(fv.requests, "get", side_effect=[limited, ok]), patch.object(fv.time, "sleep"):
            self.assertEqual(fv._get("key", "/orders", {}), [{"number": "1"}])


class TestInsert(unittest.TestCase):
    def _conn(self, rowcounts):
        cur = MagicMock()
        type(cur).rowcount = property(lambda self, it=iter(rowcounts): next(it))
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        return conn, cur

    def test_idempotent_insert_counts_only_new(self):
        rows = fv._label_rows(_order(shipments=[_ship(sid=1), _ship(sid=2)]))
        conn, cur = self._conn([1, 0])  # second label already present
        with patch.object(fv, "get_connection", return_value=conn):
            self.assertEqual(fv._insert(rows), 1)
        sql = cur.execute.call_args_list[0].args[0]
        self.assertIn("ON CONFLICT (billing_transaction_id) DO NOTHING", sql)

    def test_no_rows_does_not_touch_db(self):
        with patch.object(fv, "get_connection") as gc:
            self.assertEqual(fv._insert([]), 0)
        gc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
