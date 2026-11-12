"""Tests for parsing GetItem detail fields (category, sport/game, grader, grade). No network."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from traffic_analytics.listing_details import parse_details


def _xml(condition_id=None, specifics=None, descriptors=None, category=True):
    spec = "".join(
        f"<NameValueList><Name>{n}</Name><Value>{v}</Value></NameValueList>" for n, v in (specifics or {}).items()
    )
    desc = "".join(
        f"<ConditionDescriptor><Name>{n}</Name><Value>{v}</Value></ConditionDescriptor>"
        for n, v in (descriptors or {}).items()
    )
    cat = "<PrimaryCategory><CategoryID>261328</CategoryID><CategoryName>X:Singles</CategoryName></PrimaryCategory>"
    return f"""<?xml version="1.0"?><GetItemResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack>
<Item>{cat if category else ""}{f"<ConditionID>{condition_id}</ConditionID>" if condition_id else ""}
<ConditionDescriptors>{desc}</ConditionDescriptors><ItemSpecifics>{spec}</ItemSpecifics></Item></GetItemResponse>"""


class TestParseDetails(unittest.TestCase):

    def test_ungraded_is_raw(self):
        d = parse_details(_xml("4000", {"Sport": "Football", "Graded": "No"}, {"40001": "400010"}))
        self.assertEqual((d["sport_or_game"], d["grader"], d["grade"]), ("Football", None, "Raw"))
        self.assertEqual(d["category_id"], "261328")

    def test_graded_from_descriptors(self):
        d = parse_details(_xml("2750", {"Game": "Pokémon TCG"}, {"27501": "275010", "27502": "275021"}))
        self.assertEqual((d["sport_or_game"], d["grader"], d["grade"]), ("Pokémon TCG", "PSA", "9.5"))

    def test_item_specifics_win_and_grade_normalized(self):
        d = parse_details(_xml("2750", {"Professional Grader": "TAG", "Grade": "10.0"}, {"27501": "275010"}))
        self.assertEqual((d["grader"], d["grade"]), ("TAG", "10"))

    def test_unknown_descriptor_left_blank(self):
        d = parse_details(_xml("2750", {}, {"27501": "999", "27502": "999"}))
        self.assertEqual((d["grader"], d["grade"]), (None, None))

    def test_no_condition_means_unknown_not_raw(self):
        self.assertIsNone(parse_details(_xml(None))["grade"])

    def test_missing_category_returns_none(self):
        self.assertIsNone(parse_details(_xml("4000", category=False)))


if __name__ == "__main__":
    unittest.main()
