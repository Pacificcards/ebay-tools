"""eBay Inventory + Offer API for ungraded sports trading cards (category 261328)."""
import re

import requests

from shared.ebay_auth import get_access_token

BASE_INVENTORY = "https://api.ebay.com/sell/inventory/v1"
DEFAULT_CATEGORY = "261328"  # Sports Trading Card Singles

# Category 261328 (Sports Trading Card Singles) only accepts conditionId 4000 = "Ungraded".
# The ConditionEnum that maps to ID 4000 is USED_VERY_GOOD.
# The sub-grade is expressed via conditionDescriptor 40001 (required by eBay).
_UNGRADED_CONDITION_ENUM = "USED_VERY_GOOD"
_CONDITION_DESCRIPTOR = {
    "near mint or better": "400010",
    "excellent": "400011",
    "very good": "400012",
    "poor": "400013",
}



def get_write_token(client_id: str, client_secret: str, refresh_token_write: str) -> str:
    return get_access_token(client_id, client_secret, refresh_token_write)


def create_and_publish(
    token: str,
    sku: str,
    title: str,
    description: str,
    aspects: dict,
    condition: str,
    price: float,
    qty: int,
    category_id: str,
    image_urls: list[str],
    fulfillment_policy_id: str,
    payment_policy_id: str,
    return_policy_id: str,
    merchant_location_key: str = "",
    package_weight_oz: float | None = None,
    package_length_in: float | None = None,
    package_width_in: float | None = None,
    package_height_in: float | None = None,
    best_offer_enabled: bool = False,
    best_offer_min_price: float | None = None,
    scheduled_start: str | None = None,
    condition_map: dict | None = None,
) -> str:
    """Create inventory item + offer, publish, return eBay listing ID."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Content-Language": "en-US",
    }

    _map = condition_map if condition_map is not None else _CONDITION_DESCRIPTOR
    condition_key = condition.lower().strip()
    descriptor_value = _map.get(condition_key)
    if not descriptor_value:
        raise ValueError(
            f"Unknown condition '{condition}'. "
            f"Must be one of: {', '.join(_map)}"
        )
    condition_enum = _UNGRADED_CONDITION_ENUM

    # Step 1: create/replace inventory item (idempotent PUT)
    ship_availability: dict = {"quantity": qty}
    if merchant_location_key:
        ship_availability["availabilityDistributions"] = [
            {"merchantLocationKey": merchant_location_key, "quantity": qty}
        ]

    item_body = {
        "condition": condition_enum,
        "conditionDescriptors": [{"name": "40001", "values": [descriptor_value]}],
        "product": {
            "title": title,
            "description": description,
            "imageUrls": image_urls,
            "aspects": aspects,
        },
        "availability": {
            "shipToLocationAvailability": ship_availability
        },
    }
    if all(v is not None for v in (package_weight_oz, package_length_in, package_width_in, package_height_in)):
        item_body["packageWeightAndSize"] = {
            "weight": {"value": package_weight_oz, "unit": "OUNCE"},
            "dimensions": {
                "length": package_length_in,
                "width": package_width_in,
                "height": package_height_in,
                "unit": "INCH",
            },
        }
    r = requests.put(f"{BASE_INVENTORY}/inventory_item/{sku}", json=item_body, headers=headers)
    _raise(r, "create inventory item")

    # Step 2: create offer (handle 409/400 if one already exists for this SKU)
    # subtitle is intentionally omitted — eBay charges extra fees for subtitles
    offer_body = {
        "sku": sku,
        "marketplaceId": "EBAY_US",
        "format": "FIXED_PRICE",
        "availableQuantity": qty,
        "listingDescription": description,
        "pricingSummary": {
            "price": {"value": f"{price:.2f}", "currency": "USD"}
        },
        "categoryId": str(category_id) if category_id else DEFAULT_CATEGORY,
        "listingPolicies": {
            "fulfillmentPolicyId": fulfillment_policy_id,
            "paymentPolicyId": payment_policy_id,
            "returnPolicyId": return_policy_id,
            "bestOfferTerms": {
                "bestOfferEnabled": best_offer_enabled,
                **({"autoDeclinePrice": {"value": f"{best_offer_min_price:.2f}", "currency": "USD"}}
                   if best_offer_min_price is not None else {}),
            },
        },
        **({"merchantLocationKey": merchant_location_key} if merchant_location_key else {}),
        **({"listingStartDate": scheduled_start} if scheduled_start else {}),
    }
    r = requests.post(f"{BASE_INVENTORY}/offer", json=offer_body, headers=headers)
    if not r.ok and _is_offer_exists_error(r):
        r2 = requests.get(f"{BASE_INVENTORY}/offer", params={"sku": sku}, headers=headers)
        _raise(r2, "retrieve existing offer")
        offer_id = r2.json()["offers"][0]["offerId"]
        # Update the stale offer so it reflects current settings (e.g. location added after creation)
        r3 = requests.put(f"{BASE_INVENTORY}/offer/{offer_id}", json=offer_body, headers=headers)
        _raise(r3, "update existing offer")
    else:
        _raise(r, "create offer")
        offer_id = r.json()["offerId"]

    # Step 3: verify fees before publishing
    non_zero = _get_non_zero_fees(headers, offer_id)
    if non_zero:
        fee_str = ", ".join(f"{t}: ${a:.2f}" for t, a in non_zero)
        print(f"         WARNING: eBay would charge fees ({fee_str})")
        confirm = input("         Proceed anyway? [y/N]: ").strip().lower()
        if confirm != "y":
            raise RuntimeError(f"Skipped — listing would incur seller fees ({fee_str})")

    # Step 4: publish
    r = requests.post(f"{BASE_INVENTORY}/offer/{offer_id}/publish", json={}, headers=headers)
    _raise(r, "publish offer")
    return r.json()["listingId"]


# These fees are always waived by the store subscription; the API returns rack
# rate regardless, so we ignore them to avoid false positives.
_SUBSCRIPTION_COVERED_FEES = {"ListingFee", "InsertionFee"}


def _get_non_zero_fees(headers: dict, offer_id: str) -> list[tuple[str, float]]:
    """Return (feeType, amount) pairs for unexpected non-zero fees on this offer."""
    r = requests.post(
        f"{BASE_INVENTORY}/offer/get_listing_fees",
        json={"offers": [{"offerId": offer_id}]},
        headers=headers,
    )
    _raise(r, "get listing fees")
    summaries = r.json().get("feeSummaries", [])
    if not summaries:
        return []
    return [
        (fee["feeType"], float(fee["amount"]["value"]))
        for fee in summaries[0].get("fees", [])
        if float(fee["amount"]["value"]) > 0
        and fee["feeType"] not in _SUBSCRIPTION_COVERED_FEES
    ]


def _is_offer_exists_error(response: requests.Response) -> bool:
    """eBay returns 409 or 400 with errorId 25002 when an offer already exists for a SKU."""
    try:
        return any(
            e.get("errorId") == 25002 and "already exists" in e.get("message", "")
            for e in response.json().get("errors", [])
        )
    except Exception:
        return False


def _raise(response: requests.Response, step: str):
    if not response.ok:
        raise RuntimeError(
            f"eBay API error at '{step}': {response.status_code} — {response.text}"
        )


def auto_sku(row_idx: int, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "", title.lower())[:8]
    return f"PCC-{row_idx}-{slug}"


def get_offer_for_sku(token: str, sku: str) -> dict:
    """Return {sku, offer_id} for a known SKU, or raise if not found."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.get(f"{BASE_INVENTORY}/offer", params={"sku": sku}, headers=headers)
    _raise(r, f"get offer for SKU {sku}")
    offers = r.json().get("offers", [])
    if not offers:
        raise RuntimeError(f"No offer found for SKU {sku!r}")
    return {"sku": sku, "offer_id": offers[0]["offerId"]}


def build_listing_offer_map(token: str, listing_ids: set[str]) -> dict:
    """Build {listing_id: {sku, offer_id}} for the given set of listing IDs.

    Paginates through all inventory items to find their SKUs, then looks up each
    offer to find the listing ID. Stops early once all requested IDs are found.
    Used when SKU is not stored in the sheet (legacy rows).
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Step 1: collect all SKUs from inventory items
    skus: list[str] = []
    offset = 0
    while True:
        r = requests.get(
            f"{BASE_INVENTORY}/inventory_item",
            params={"limit": 100, "offset": offset},
            headers=headers,
        )
        _raise(r, "list inventory items")
        data = r.json()
        page = data.get("inventoryItems", [])
        skus.extend(item["sku"] for item in page)
        offset += len(page)
        if offset >= data.get("total", 0) or not page:
            break

    # Step 2: look up each SKU's offer until all requested listing IDs are found
    offer_map: dict = {}
    remaining = set(listing_ids)
    for i, sku in enumerate(skus, 1):
        print(f"    {i}/{len(skus)} SKUs checked...", end="\r", flush=True)
        r = requests.get(f"{BASE_INVENTORY}/offer", params={"sku": sku}, headers=headers)
        if not r.ok:
            continue
        for offer in r.json().get("offers", []):
            lid = offer.get("listing", {}).get("listingId")
            if lid:
                offer_map[lid] = {"sku": sku, "offer_id": offer["offerId"]}
                remaining.discard(lid)
        if not remaining:
            break
    print()  # clear the progress line
    return offer_map


def bulk_update_price_quantity(token: str, updates: list[dict]) -> list[dict]:
    """Update price and/or quantity for up to 25 published listings.

    Each item in `updates`: {sku, offer_id, price (float|None), quantity (int|None)}
    Returns per-SKU results: [{sku, success, error}]
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload_requests = []
    for u in updates:
        offer: dict = {"offerId": u["offer_id"]}
        if u.get("price") is not None:
            offer["price"] = {"value": f"{u['price']:.2f}", "currency": "USD"}
        if u.get("quantity") is not None:
            offer["availableQuantity"] = int(u["quantity"])
        item: dict = {"sku": u["sku"], "offers": [offer]}
        if u.get("quantity") is not None:
            item["shipToLocationAvailability"] = {"quantity": int(u["quantity"])}
        payload_requests.append(item)

    r = requests.post(
        f"{BASE_INVENTORY}/bulk_update_price_quantity",
        json={"requests": payload_requests},
        headers=headers,
    )
    _raise(r, "bulk_update_price_quantity")

    results = []
    for resp in r.json().get("responses", []):
        errors = resp.get("errors", [])
        if resp.get("statusCode") == 200 and not errors:
            results.append({"sku": resp["sku"], "success": True, "error": None})
        else:
            msg = errors[0].get("message", "unknown error") if errors else f"status {resp.get('statusCode')}"
            results.append({"sku": resp["sku"], "success": False, "error": msg})
    return results
