"""TEMPORARY — delete before merging the photo-alert branch.

Runs the real listener search (BIN, US, last 12h, Graded:No, 3 categories) for one broad
query, then posts a couple of [TEST] alerts to the alerts channel through the new
photo-alert code. Logs how many photos eBay's search returns versus the item lookup.

Writes nothing to Supabase or the Google Sheet, so the live listener is unaffected.
"""
import os
import sys
from collections import Counter

from listener.discord import MAX_ALERT_IMAGES, send_alert
from listener.ebay import get_app_token, get_item_image_urls, search_listings_by_keyword

QUERY = "pokemon"
MIN_PRICE, MAX_PRICE = 10.0, 500.0


def main() -> int:
    token = get_app_token(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"])
    listings = search_listings_by_keyword(token, QUERY, MIN_PRICE, MAX_PRICE)
    print(f"{len(listings)} listing(s) for {QUERY!r} (${MIN_PRICE:.0f}-${MAX_PRICE:.0f}, BIN, US, ungraded, last 12h)")
    if not listings:
        print("No listings found — nothing to test with.")
        return 1

    dist = Counter(len(l["image_urls"]) for l in listings)
    print("Photos per listing from search alone:", dict(sorted(dist.items())))

    # Does the item lookup ever find photos that search missed? Log only, no posting.
    few = [l for l in listings if len(l["image_urls"]) < MAX_ALERT_IMAGES][:10]
    gained = 0
    for listing in few:
        item_photos = get_item_image_urls(token, listing["item_id"])
        more = len(item_photos) > len(listing["image_urls"])
        gained += more
        print(f"- lookup check {listing['item_id']}: {len(listing['image_urls'])} from search, "
              f"{len(item_photos)} from item lookup{'  <-- MORE' if more else ''}")
    print(f"Item lookup found extra photos for {gained} of {len(few)} listings with <{MAX_ALERT_IMAGES} photos")

    # Post one alert with a full 4-photo gallery.
    full = next((l for l in listings if len(l["image_urls"]) >= MAX_ALERT_IMAGES), None)
    if full is None:
        print("No listing with 4+ photos found — gallery not posted.")
        return 1
    print(f"Posting gallery alert for {full['item_id']} ({len(full['image_urls'])} photos, sending {MAX_ALERT_IMAGES})")
    # Pretend market price sits 20% above the listing so the green tier/color shows.
    market_price = round(full["price"] / 0.8, 2)
    send_alert(f"[TEST] Photo alert — {full['title']}", full, MAX_PRICE, 20.0, market_price=market_price)
    return 0


if __name__ == "__main__":
    sys.exit(main())
