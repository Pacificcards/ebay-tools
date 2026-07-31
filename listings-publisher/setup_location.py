"""
One-time setup: register a merchant location with eBay.

Run once, then add the printed key to .env as EBAY_MERCHANT_LOCATION_KEY.

Usage:
    .venv/bin/python listings-publisher/setup_location.py
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import requests
from ebay_api import get_write_token

load_dotenv()

LOCATION_KEY = "PCC-MAIN"
BASE = "https://api.ebay.com/sell/inventory/v1"


def main():
    token = get_write_token(
        os.environ["EBAY_CLIENT_ID"],
        os.environ["EBAY_CLIENT_SECRET"],
        os.environ["EBAY_REFRESH_TOKEN_WRITE"],
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Content-Language": "en-US",
    }

    print("Enter your shipping address (where cards ship from):")
    address_line1 = input("  Street address: ").strip()
    city = input("  City: ").strip()
    state = input("  State (2-letter, e.g. CA): ").strip().upper()
    postal_code = input("  ZIP code: ").strip()

    body = {
        "location": {
            "address": {
                "addressLine1": address_line1,
                "city": city,
                "stateOrProvince": state,
                "postalCode": postal_code,
                "country": "US",
            }
        },
        "locationTypes": ["WAREHOUSE"],
        "name": "Pacific Cards Co",
        "merchantLocationStatus": "ENABLED",
    }

    r = requests.post(f"{BASE}/location/{LOCATION_KEY}", json=body, headers=headers)

    if r.status_code == 204:
        print(f"\n✓ Location created successfully.")
        print(f"\nAdd this to your .env file:")
        print(f"  EBAY_MERCHANT_LOCATION_KEY={LOCATION_KEY}")
    elif r.status_code == 409:
        print(f"\n✓ Location '{LOCATION_KEY}' already exists — nothing to do.")
        print(f"\nAdd this to your .env file if not already there:")
        print(f"  EBAY_MERCHANT_LOCATION_KEY={LOCATION_KEY}")
    else:
        print(f"\n✗ Failed: {r.status_code} — {r.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
