"""
Tests for order-task-tracker pure logic: date/year resolution, merchant normalization,
order aggregation, task formatting, and the create / update / skip plan.

No network, DB, Gmail, Tasks or Claude calls.
"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

config.TRACKING_START = "2000-01-01T00:00:00+00:00"   # sample data below predates the real cutoff

from extract import normalize_merchant, resolve_date, resolve_range  # noqa: E402
from orders import build_orders  # noqa: E402
from tasks_sync import ETA_UNAVAILABLE, TaskIndex, lookup_priority, plan_order, render  # noqa: E402

K = ("Topps", "A-1001")


def _raw(**kw):
    base = {"is_order_email": True, "is_purchase": True, "merchant": "Topps", "order_number": "A-1001",
            "status": "order_confirmed", "full_cancellation": False, "refund_amount": None,
            "items": [{"name": "Chrome Box", "qty": 1}], "eta_start": None, "eta_end": None,
            "shipments": []}
    base.update(kw)
    return base


def _row(received, domain="topps.com", **raw):
    return {"message_id": received, "received": f"{received}T10:00:00-07:00",
            "sender_domain": domain, "display_name": "", "subject": "", "raw": _raw(**raw)}


def _ship(tracking, status="shipped", carrier="UPS", items=(), eta_end=None, delivered_on=None):
    return {"carrier": carrier, "tracking": tracking, "tracking_url": None, "items": list(items),
            "status": status, "eta_start": None, "eta_end": eta_end, "delivered_on": delivered_on}


def _task(link_spec, status="needsAction", due=None, title="t", updated="2026-09-20T00:00:00.000Z",
          notes="", **kw):
    """link_spec: 'Retailer: X\nOrderRef: Y[\nTrackingRef: Z]' -> the task's stored link."""
    refs = dict(line.split(": ", 1) for line in link_spec.splitlines() if ": " in line)
    t = {"id": link_spec, "title": title, "notes": notes, "status": status, "updated": updated,
         "_link": {"task_id": link_spec, "merchant": refs["Retailer"], "order_number": refs["OrderRef"],
                   "tracking": refs.get("TrackingRef")}}
    if due:
        t["due"] = f"{due}T00:00:00.000Z"
    t.update(kw)
    return t


# ── Dates ────────────────────────────────────────────────────────────────────

class TestDates(unittest.TestCase):
    def test_year_boundary_range_uses_later_date_next_year(self):
        self.assertEqual(resolve_range("12-30", "01-03", date(2026, 12, 20)), date(2027, 1, 3))

    def test_month_day_without_year_uses_email_year(self):
        self.assertEqual(resolve_date("10-03", date(2026, 9, 27)), date(2026, 10, 3))

    def test_explicit_year_kept(self):
        self.assertEqual(resolve_date("2027-02-01", date(2026, 9, 27)), date(2027, 2, 1))

    def test_month_only_is_last_day(self):
        self.assertEqual(resolve_date("11", date(2026, 9, 27)), date(2026, 11, 30))

    def test_garbage_is_none(self):
        self.assertIsNone(resolve_date("soon", date(2026, 9, 27)))


# ── Merchant ─────────────────────────────────────────────────────────────────

class TestMerchant(unittest.TestCase):
    def test_ebay_seller_is_ebay(self):
        self.assertEqual(normalize_merchant("members.ebay.com", "cardshop99", "cardshop99"), "eBay")

    def test_unlisted_sender_is_not_a_merchant(self):
        self.assertIsNone(normalize_merchant("amazon.com", "Amazon.com", "Amazon"))
        self.assertIsNone(normalize_merchant("info6.citi.com", "Costco Anywhere Visa", None))

    def test_subdomain_on_allow_list(self):
        self.assertEqual(normalize_merchant("logistics.costco.com", "Costco", None), "Costco")
        self.assertEqual(normalize_merchant("oe1.target.com", "Target", None), "Target")

    def test_shared_shopify_sender_needs_shop_name(self):
        self.assertEqual(normalize_merchant("t.shopifyemail.com", "Topps", None), "Topps")
        self.assertEqual(normalize_merchant("t.shopifyemail.com", "TAG", None), "TAG")
        self.assertIsNone(normalize_merchant("t.shopifyemail.com", "Some Other Shop", None))

    def test_fanatics_collect_not_confused_with_fanatics(self):
        self.assertEqual(normalize_merchant("email.fanaticscollect.com", "", None), "Fanatics Collect")
        self.assertEqual(normalize_merchant("s.fanatics.com", "", None), "Fanatics")

    def test_carrier_sender_has_no_merchant(self):
        self.assertIsNone(normalize_merchant("ups.com", "UPS", None))


# ── Aggregation ──────────────────────────────────────────────────────────────

class TestBuildOrders(unittest.TestCase):
    def test_excluded_merchant_dropped(self):
        rows = [_row("2026-09-20", domain="amazon.com", merchant="Amazon")]
        self.assertEqual(build_orders(rows), {})

    def test_seller_mail_dropped(self):
        self.assertEqual(build_orders([_row("2026-09-20", is_purchase=False)]), {})

    def test_out_for_delivery_overrides_delayed(self):
        rows = [_row("2026-09-20", shipments=[_ship("1Z999", status="delayed", eta_end="09-30")]),
                _row("2026-09-24", shipments=[_ship("1Z999", status="out_for_delivery")])]
        sh = build_orders(rows)[K].shipments["1Z999"]
        self.assertEqual((sh.status, sh.ofd_date), ("out_for_delivery", date(2026, 9, 24)))

    def test_partial_refund_does_not_cancel(self):
        rows = [_row("2026-09-20"), _row("2026-09-21", status="partial_refund", refund_amount="5.00")]
        self.assertFalse(build_orders(rows)[K].cancelled)

    def test_full_cancellation(self):
        rows = [_row("2026-09-20"), _row("2026-09-21", status="cancelled", full_cancellation=True,
                                         refund_amount="$49.99")]
        self.assertTrue(build_orders(rows)[K].cancelled)

    def test_carrier_email_attaches_by_tracking(self):
        rows = [_row("2026-09-20", shipments=[_ship("1Z999")]),
                _row("2026-09-23", domain="ups.com", merchant=None, order_number=None, items=[],
                     shipments=[_ship("1Z999", status="delivered")])]
        self.assertEqual(build_orders(rows)[K].shipments["1Z999"].status, "delivered")

    def test_unknown_carrier_tracking_ignored(self):
        rows = [_row("2026-09-23", domain="ups.com", merchant=None, order_number=None,
                     shipments=[_ship("OUTGOING1")])]
        self.assertEqual(build_orders(rows), {})

    def test_order_number_hash_stripped(self):
        self.assertIn(K, build_orders([_row("2026-09-20", order_number="#A-1001")]))


# ── Planning ─────────────────────────────────────────────────────────────────

def _plan(rows, tasks=(), lookups=None):
    order = next(iter(build_orders(rows).values()))
    return plan_order(order, _index(tasks), lookups or {})


def _index(tasks):
    return TaskIndex.build(list(tasks), [t["_link"] for t in tasks])


class TestPlan(unittest.TestCase):
    def test_confirmation_without_date_creates_undated_task(self):
        (a,) = _plan([_row("2026-09-20")])
        self.assertEqual((a.kind, a.due), ("create", None))
        self.assertEqual(a.link, {"merchant": "Topps", "order_number": "A-1001", "tracking": None})

    def test_confirmation_with_date_sets_due(self):
        (a,) = _plan([_row("2026-09-20", eta_start="10-01", eta_end="10-05")])
        self.assertEqual(a.due, date(2026, 10, 5))

    def test_first_shipment_updates_confirmation_task(self):
        confirm = _task("Retailer: Topps\n\nOrderRef: A-1001")
        (a,) = _plan([_row("2026-09-20"), _row("2026-09-22", shipments=[_ship("1Z999", eta_end="09-26")])],
                     [confirm])
        self.assertEqual(a.kind, "update")
        self.assertIs(a.task, confirm)
        self.assertEqual(a.link["tracking"], "1Z999")
        self.assertTrue(a.relink)
        self.assertEqual(a.due, date(2026, 9, 26))

    def test_second_package_gets_its_own_task(self):
        confirm = _task("Retailer: Topps\nOrderRef: A-1001")
        rows = [_row("2026-09-20"), _row("2026-09-22", shipments=[_ship("1Z111"), _ship("1Z222")])]
        a1, a2 = _plan(rows, [confirm])
        self.assertEqual((a1.kind, a2.kind), ("update", "create"))
        self.assertTrue(a1.title.endswith("(1 of 2)"))
        self.assertTrue(a2.title.endswith("(2 of 2)"))

    def test_shipment_without_confirmation_creates_task(self):
        (a,) = _plan([_row("2026-09-22", status="shipped", shipments=[_ship("1Z999", eta_end="09-26")])])
        self.assertEqual(a.kind, "create")

    def test_completed_task_never_touched(self):
        done = _task("Retailer: Topps\nOrderRef: A-1001\nTrackingRef: 1Z999", status="completed")
        (a,) = _plan([_row("2026-09-22", shipments=[_ship("1Z999", status="delivered")])], [done])
        self.assertEqual(a.kind, "skip_closed")

    def test_deleted_task_treated_as_closed(self):
        gone = _task("Retailer: Topps\nOrderRef: A-1001", deleted=True)
        (a,) = _plan([_row("2026-09-20")], [gone])
        self.assertEqual(a.kind, "skip_closed")

    def test_delivered_without_task_is_not_created(self):
        (a,) = _plan([_row("2026-09-22", shipments=[_ship("1Z999", status="delivered")])])
        self.assertEqual(a.kind, "skip_no_task")

    def test_delivered_adds_note_and_never_completes(self):
        open_task = _task("Retailer: Topps\nOrderRef: A-1001\nTrackingRef: 1Z999")
        (a,) = _plan([_row("2026-09-22", shipments=[_ship("1Z999", status="delivered", delivered_on="09-25")])],
                     [open_task])
        self.assertEqual(a.notes.splitlines()[0], "Delivered Sep 25")
        self.assertEqual(a.kind, "update")

    def test_no_eta_with_tracking_flags_unavailable(self):
        (a,) = _plan([_row("2026-09-22", shipments=[_ship("1Z999")])])
        self.assertIsNone(a.due)
        self.assertIn(ETA_UNAVAILABLE, a.notes)

    def test_lookup_eta_used(self):
        (a,) = _plan([_row("2026-09-22", shipments=[_ship("1Z999")])],
                     lookups={"1Z999": {"eta": date(2026, 9, 29), "delivered_on": None}})
        self.assertEqual(a.due, date(2026, 9, 29))
        self.assertNotIn(ETA_UNAVAILABLE, a.notes)

    def test_cancelled_note(self):
        open_task = _task("Retailer: Topps\nOrderRef: A-1001")
        (a,) = _plan([_row("2026-09-20"), _row("2026-09-21", status="cancelled", full_cancellation=True,
                                               refund_amount="$49.99")], [open_task])
        self.assertEqual(a.notes.splitlines()[0], "Cancelled - refunded $49.99")

    def test_unchanged_task_not_rewritten(self):
        rows = [_row("2026-09-20", eta_end="10-05")]
        (first,) = _plan(rows)
        existing = _task("Retailer: Topps\nOrderRef: A-1001", notes=first.notes, title=first.title,
                         due="2026-10-05")
        (again,) = _plan(rows, [existing])
        self.assertEqual(again.kind, "unchanged")


    def test_lookup_beats_older_email_eta(self):
        (a,) = _plan([_row("2026-09-22", shipments=[_ship("1Z999", eta_end="09-26")])],
                     lookups={"1Z999": {"eta": date(2026, 9, 30), "delivered_on": None}})
        self.assertEqual(a.due, date(2026, 9, 30))

    def test_same_order_number_different_shops_not_merged(self):
        rows = [_row("2026-09-20", order_number="1001"),
                _row("2026-09-21", domain="oe.target.com", merchant="Target", order_number="1001")]
        self.assertEqual(len(build_orders(rows)), 2)

    def test_other_shops_task_not_reused(self):
        other = _task("Retailer: Target\nOrderRef: A-1001")
        (a,) = _plan([_row("2026-09-20")], [other])
        self.assertEqual(a.kind, "create")


class TestAggregationFixes(unittest.TestCase):
    def test_emails_before_tracking_start_ignored(self):
        saved = config.TRACKING_START
        config.TRACKING_START = "2026-09-21T00:00:00-07:00"
        try:
            rows = [_row("2026-09-20"), _row("2026-09-22", order_number="B-2002")]
            self.assertEqual(list(build_orders(rows)), [("Topps", "B-2002")])
        finally:
            config.TRACKING_START = saved

    def test_digital_goods_dropped(self):
        self.assertEqual(build_orders([_row("2026-09-20", is_physical_goods=False)]), {})

    def test_items_from_first_email_only(self):
        rows = [_row("2026-09-20", items=[{"name": "Chrome Hobby Box", "qty": 1}]),
                _row("2026-09-22", items=[{"name": "2025 Chrome Hobby Box (Sealed)", "qty": 1}])]
        self.assertEqual(len(build_orders(rows)[K].items), 1)

    def test_scheduled_tomorrow_moves_date(self):
        rows = [_row("2026-09-20", shipments=[_ship("1Z999", eta_end="09-30")]),
                _row("2026-09-24", shipments=[_ship("1Z999", status="scheduled_tomorrow")])]
        self.assertEqual(build_orders(rows)[K].shipments["1Z999"].eta, date(2026, 9, 25))


class TestLookupPriority(unittest.TestCase):
    def _order(self, **ship):
        return build_orders([_row("2026-09-20", shipments=[_ship("1Z999", **ship)])])[K]

    def test_no_date_is_fresh(self):
        o = self._order()
        self.assertEqual(lookup_priority(o, o.shipments["1Z999"], _index([]), date(2026, 9, 27)), "fresh")

    def test_quiet_task_rechecked_once_per_period(self):
        o = self._order(eta_end="10-20")
        task = _task("Retailer: Topps\nOrderRef: A-1001\nTrackingRef: 1Z999", due="2026-10-20",
                     updated="2026-09-01T00:00:00.000Z")
        index = _index([task])
        sh = o.shipments["1Z999"]
        self.assertEqual(lookup_priority(o, sh, index, date(2026, 9, 15)), "stale")   # 14 days
        self.assertIsNone(lookup_priority(o, sh, index, date(2026, 9, 16)))           # 15 days
        self.assertEqual(lookup_priority(o, sh, index, date(2026, 9, 29)), "stale")   # 28 days

    def test_delivered_never_looked_up(self):
        o = self._order(status="delivered")
        self.assertIsNone(lookup_priority(o, o.shipments["1Z999"], _index([]), date(2026, 9, 27)))


class TestFormat(unittest.TestCase):
    def _order(self, **kw):
        return build_orders([_row("2026-09-20", **kw)])

    def test_confirmation_layout(self):
        o = self._order(items=[{"name": "Chrome Hobby Box", "qty": 2}])[K]
        title, notes = render(o, None, 1, 1, None)
        self.assertEqual(title, "2x Chrome Hobby Box - Topps")
        self.assertEqual(notes.splitlines(), ["A-1001", "Sep 20, 2026"])

    def test_shipped_layout(self):
        orders = self._order(merchant="eBay", domain="ebay.com", order_number="01-15070-33009",
                             items=[{"name": "Mew VMAX 114/264 NEAR MINT", "qty": 1}],
                             shipments=[_ship("9400111899223197428490", carrier="USPS")])
        o = orders[("eBay", "01-15070-33009")]
        title, notes = render(o, o.shipments["9400111899223197428490"], 1, 1, None)
        self.assertEqual(title, "Mew VMAX 114/264 NEAR MINT - eBay")
        self.assertEqual(notes.splitlines(), [
            "9400111899223197428490",
            "https://tools.usps.com/go/TrackConfirmAction?tLabels=9400111899223197428490",
            "01-15070-33009",
        ])

    def test_status_line_on_top_and_package_suffix(self):
        o = self._order(shipments=[_ship("1Z111"), _ship("1Z222")])[K]
        title, notes = render(o, o.shipments["1Z222"], 2, 2, ETA_UNAVAILABLE)
        self.assertTrue(title.endswith(" - Topps (2 of 2)"))
        self.assertEqual(notes.splitlines()[0], "No ETA - check tracking")

    def test_multi_item_title(self):
        o = self._order(items=[{"name": "Chrome Hobby Box", "qty": 2}, {"name": "Chrome Blaster", "qty": 1}])[K]
        self.assertEqual(render(o, None, 1, 1, None)[0], "2x Chrome Hobby Box, Chrome Blaster - Topps")

    def test_deleted_and_purged_task_stays_dismissed(self):
        link = {"task_id": "gone", "merchant": "Topps", "order_number": "A-1001", "tracking": None}
        order = build_orders([_row("2026-09-20")])[K]
        (a,) = plan_order(order, TaskIndex.build([], [link]), {})
        self.assertEqual(a.kind, "skip_closed")


if __name__ == "__main__":
    unittest.main()
