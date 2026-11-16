"""Tests for the read-only shipping-policy audit. No network calls."""
import os
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from policy_audit import audit_shipping_policies as a


def _policy(pid, name, cost="0.00", handling=1, free=False, default=False):
    return {
        "fulfillmentPolicyId": pid, "name": name,
        "handlingTime": {"value": handling, "unit": "DAY"},
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES", "default": default}],
        "shippingOptions": [{
            "optionType": "DOMESTIC", "costType": "FLAT_RATE",
            "shippingServices": [{"shippingServiceCode": "USPSGroundAdvantage",
                                  "shippingCost": {"value": cost}, "freeShipping": free,
                                  "sortOrder": 1}],
        }],
    }


class TestAudit(unittest.TestCase):

    def test_summarize_services(self):
        self.assertEqual(a.summarize_services(_policy("1", "a", "4.99"), "DOMESTIC"),
                         "USPSGroundAdvantage $4.99")
        self.assertEqual(a.summarize_services(_policy("1", "a", free=True), "DOMESTIC"),
                         "USPSGroundAdvantage free")
        self.assertEqual(a.summarize_services(_policy("1", "a"), "INTERNATIONAL"), "")

    def test_duplicates_ignore_name(self):
        ps = [_policy("1", "PWE"), _policy("2", "PWE copy"), _policy("3", "BMWT", "4.99")]
        self.assertEqual(a.duplicate_groups(ps), {"1": 1, "2": 1})

    def test_handling_time_breaks_duplicate(self):
        ps = [_policy("1", "x", handling=1), _policy("2", "y", handling=3)]
        self.assertEqual(a.duplicate_groups(ps), {})

    def test_flags(self):
        ps = [_policy("1", "used"), _policy("2", "sheet only", "1.00"),
              _policy("3", "dead", "2.00", default=True)]
        rows = {r["policy_id"]: r for r in a.build_rows(
            ps, Counter({"1": 5}), {"2": Counter({"sports pending": 2})}, {})}
        self.assertEqual(rows["1"]["flags"], "")
        self.assertEqual(rows["2"]["flags"], "no active listings")
        self.assertEqual(rows["3"]["flags"], "UNUSED, default")
        self.assertEqual(rows["2"]["sheet_rows"], "sports pending: 2")

    def test_skip_listings_never_flags_unused(self):
        rows = a.build_rows([_policy("1", "x")], None, {}, {})
        self.assertEqual(rows[0]["active_listings"], "")
        self.assertNotIn("UNUSED", rows[0]["flags"])

    def test_parse_shipping_profile_id(self):
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<GetItemResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Item><SellerProfiles>
<SellerShippingProfile><ShippingProfileID>245000000018</ShippingProfileID></SellerShippingProfile>
</SellerProfiles></Item></GetItemResponse>"""
        self.assertEqual(a.parse_shipping_profile_id(xml), "245000000018")
        self.assertIsNone(a.parse_shipping_profile_id(
            b'<GetItemResponse xmlns="urn:ebay:apis:eBLBaseComponents"/>'))


if __name__ == "__main__":
    unittest.main()
