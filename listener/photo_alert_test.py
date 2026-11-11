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
ALERTS_TO_SEND = 2


def main() -> int:
    token = get_app_token(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"])
    listings = search_listings_by_keyword(token, QUERY, MIN_PRICE, MAX_PRICE)
    print(f"{len(listings)} listing(s) for {QUERY!r} (${MIN_PRICE:.0f}-${MAX_PRICE:.0f}, BIN, US, ungraded, last 12h)")
    if not listings:
        print("No listings found — nothing to test with.")
        return 1

    dist = Counter(len(l["image_urls"]) for l in listings)
    print("Photos per listing from search alone:", dict(sorted(dist.items())))

    for listing in listings[:ALERTS_TO_SEND]:
        search_photos = len(listing["image_urls"])
        lookup_photos = None
        if search_photos < MAX_ALERT_IMAGES:
            item_photos = get_item_image_urls(token, listing["item_id"])
            lookup_photos = len(item_photos)
            if lookup_photos > search_photos:
                listing["image_urls"] = item_photos
        print(f"- {listing['item_id']}: {search_photos} from search, item lookup "
              f"{'not needed' if lookup_photos is None else f'gave {lookup_photos}'}, "
              f"sending {min(len(listing['image_urls']), MAX_ALERT_IMAGES)}")

        # Pretend market price sits 20% above the listing so the green tier/color shows.
        market_price = round(listing["price"] / 0.8, 2)
        send_alert(f"[TEST] Photo alert — {listing['title']}", listing, MAX_PRICE, 20.0,
                   market_price=market_price)
    return 0


if __name__ == "__main__":
    sys.exit(main())
