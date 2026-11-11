"""
Tests for listener photo alerts.

Covers:
  - ebay._image_urls / _parse_items: photo URLs pulled from Browse API items, upsized, de-duplicated
  - discord._build_alert_payload: embed gallery shape (max 4 photos, shared url), colors, no-photo case
  - discord.send_alert: falls back to a plain-text message if Discord rejects the embed

All network calls are mocked — no live credentials needed.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from listener import discord, ebay

URL = "https://www.ebay.com/itm/123456789"


def _img(n: int, size: int = 225) -> str:
    return f"https://i.ebayimg.com/images/g/photo{n}/s-l{size}.jpg"


def _listing(image_urls: list[str]) -> dict:
    return {
        "item_id": "123456789",
        "title": "Raw card",
        "price": 40.0,
        "url": URL,
        "seller_feedback_score": 120,
        "seller_feedback_pct": "99.5",
        "item_creation_date": "2026-11-11T20:32:00.000Z",
        "image_urls": image_urls,
    }


class TestImageUrls(unittest.TestCase):

    def test_primary_then_additional_upsized(self):
        item = {
            "image": {"imageUrl": _img(1)},
            "additionalImages": [{"imageUrl": _img(2)}, {"imageUrl": _img(3, 64)}],
        }
        self.assertEqual(ebay._image_urls(item), [_img(1, 1600), _img(2, 1600), _img(3, 1600)])

    def test_duplicates_removed_after_upsizing(self):
        item = {"image": {"imageUrl": _img(1, 225)}, "additionalImages": [{"imageUrl": _img(1, 500)}]}
        self.assertEqual(ebay._image_urls(item), [_img(1, 1600)])

    def test_no_photos(self):
        self.assertEqual(ebay._image_urls({}), [])
        self.assertEqual(ebay._image_urls({"image": None, "additionalImages": None}), [])

    def test_non_ebay_url_left_alone(self):
        other = "https://example.com/pics/s-l225.jpg"
        self.assertEqual(ebay._image_urls({"image": {"imageUrl": other}}), [other])

    def test_parse_items_includes_image_urls(self):
        resp = {"itemSummaries": [{
            "itemId": "v1|123456789|0",
            "title": "Raw card",
            "price": {"value": "40.00"},
            "buyingOptions": ["FIXED_PRICE"],
            "image": {"imageUrl": _img(1)},
        }]}
        self.assertEqual(ebay._parse_items(resp)[0]["image_urls"], [_img(1, 1600)])


class TestAlertPayload(unittest.TestCase):

    def test_gallery_capped_at_four_sharing_listing_url(self):
        payload = discord._build_alert_payload("Card", _listing([_img(n) for n in range(6)]), 50.0, 20.0, 50.0)
        embeds = payload["embeds"]
        self.assertEqual(len(embeds), 4)
        self.assertEqual([e["image"]["url"] for e in embeds], [_img(n) for n in range(4)])
        self.assertTrue(all(e["url"] == URL for e in embeds))
        self.assertEqual(embeds[0]["title"], "Card")
        self.assertIn("🟢 20.0% below market ($50.00)", embeds[0]["description"])
        self.assertEqual(embeds[0]["color"], discord._COLOR_GREEN)
        self.assertNotIn("title", embeds[1])

    def test_no_photos_is_single_text_embed(self):
        payload = discord._build_alert_payload("Card", _listing([]), 50.0, 20.0)
        self.assertEqual(len(payload["embeds"]), 1)
        self.assertNotIn("image", payload["embeds"][0])
        self.assertNotIn("color", payload["embeds"][0])  # no market price → no color tier
        self.assertIn("20.0% below your $50.00 target", payload["embeds"][0]["description"])

    def test_red_when_above_market(self):
        payload = discord._build_alert_payload("Card", _listing([_img(1)]), 50.0, -10.0, 40.0)
        self.assertEqual(payload["embeds"][0]["color"], discord._COLOR_RED)


class TestSendAlert(unittest.TestCase):

    @patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.test/hook"})
    def test_sends_embed_once_when_accepted(self):
        with patch.object(discord.requests, "post", return_value=MagicMock(ok=True)) as post:
            discord.send_alert("Card", _listing([_img(1)]), 50.0, 20.0, 50.0)
        self.assertEqual(post.call_count, 1)
        self.assertIn("embeds", post.call_args.kwargs["json"])

    @patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.test/hook"})
    def test_falls_back_to_plain_text_when_rejected(self):
        rejected = MagicMock(ok=False, status_code=400, text="Invalid Form Body")
        with patch.object(discord.requests, "post", side_effect=[rejected, MagicMock(ok=True)]) as post:
            discord.send_alert("Card", _listing([_img(1)]), 50.0, 20.0, 50.0)
        self.assertEqual(post.call_count, 2)
        fallback = post.call_args.kwargs["json"]["content"]
        self.assertTrue(fallback.startswith("**Card**"))
        self.assertIn(URL, fallback)


if __name__ == "__main__":
    unittest.main()
