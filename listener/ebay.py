import base64
import re
import requests
from datetime import datetime, timezone, timedelta

BROWSE_BASE   = "https://api.ebay.com/buy/browse/v1"
TAXONOMY_BASE = "https://api.ebay.com/commerce/taxonomy/v1"
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
APP_SCOPE = "https://api.ebay.com/oauth/api_scope"


def get_app_token(client_id: str, client_secret: str) -> str:
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        TOKEN_URL,
        headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials", "scope": APP_SCOPE},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"}


def _build_filter(min_price: float, max_price: float) -> str:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return ",".join([
        f"price:[{min_price}..{max_price}]",
        "priceCurrency:USD",
        f"itemStartDate:[{cutoff}..]",
        "buyingOptions:{FIXED_PRICE}",
        "itemLocationCountry:US",
    ])


def get_category_id(token: str, category_name: str) -> str | None:
    """Look up an eBay category ID by name via the Taxonomy API. Returns the top suggestion."""
    resp = requests.get(
        f"{TAXONOMY_BASE}/category_tree/0/get_category_suggestions",
        headers=_headers(token),
        params={"q": category_name},
    )
    if not resp.ok:
        return None
    suggestions = resp.json().get("categorySuggestions", [])
    if not suggestions:
        return None
    return suggestions[0]["category"]["categoryId"]


def get_epid_from_listing(token: str, hint_url: str) -> str | None:
    """Extract item ID from an eBay listing URL and return its EPID via Browse API."""
    match = re.search(r'/itm/(?:[^/?]+/)?(\d+)', hint_url)
    if not match:
        return None
    item_id = match.group(1)
    resp = requests.get(
        f"{BROWSE_BASE}/item/get_item_by_legacy_id",
        headers=_headers(token),
        params={"legacy_item_id": item_id},
    )
    if resp.status_code != 200:
        return None
    return resp.json().get("epid")


def _parse_items(response_json: dict) -> list[dict]:
    results = []
    for item in response_json.get("itemSummaries", []):
        buying_options = item.get("buyingOptions", [])
        is_auction = "AUCTION" in buying_options
        # Auctions carry currentBidPrice; BIN listings use price
        if is_auction:
            price_val = (item.get("currentBidPrice") or item.get("price") or {}).get("value")
        else:
            price_val = item.get("price", {}).get("value")
        raw_id = item.get("itemId", "")
        # Browse API returns IDs as "v1|123456789|0" — extract the numeric part
        item_id = raw_id.split("|")[1] if raw_id.count("|") >= 2 else raw_id
        seller = item.get("seller", {})
        results.append({
            "item_id": item_id,
            "title": item.get("title", ""),
            "price": float(price_val) if price_val else 0.0,
            "buying_format": "AUCTION" if is_auction else "FIXED_PRICE",
            "url": item.get("itemWebUrl", ""),
            "end_time": item.get("itemEndDate", "") or "",
            "seller_feedback_score": seller.get("feedbackScore", ""),
            "seller_feedback_pct": seller.get("feedbackPercentage", ""),
            "item_creation_date": item.get("itemCreationDate", ""),
        })
    return results


def search_listings_by_epid(token: str, epid: str, min_price: float, max_price: float) -> list[dict]:
    """Return recent BIN listings for an EPID within the price range, cheapest first."""
    resp = requests.get(
        f"{BROWSE_BASE}/item_summary/search",
        headers=_headers(token),
        params={
            "epid": epid,
            "filter": _build_filter(min_price, max_price),
            "limit": 50,
            "sort": "price",
        },
    )
    if resp.status_code != 200:
        return []
    return _parse_items(resp.json())


def _paginate(token: str, params: dict, max_results: int = 2000) -> list[dict]:
    """Fetch all pages for a given set of search params."""
    items: list[dict] = []
    offset = 0
    while len(items) < max_results:
        resp = requests.get(
            f"{BROWSE_BASE}/item_summary/search",
            headers=_headers(token),
            params={**params, "offset": offset},
        )
        if not resp.ok:
            break
        batch = _parse_items(resp.json())
        items.extend(batch)
        if len(batch) < 200:
            break
        offset += 200
    return items


def search_all_listings(
    token: str,
    query: str,
    category_id: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """Return all active US listings matching query.

    BIN listings: price-filtered by min_price/max_price.
    Auction listings: always included, no price filter (prices are naturally lower).
    Results are merged and deduplicated by item_id.
    """
    base_params: dict = {"q": query, "limit": 200, "sort": "price"}
    if category_id:
        base_params["category_ids"] = category_id

    base_filters = ["itemLocationCountry:US", "priceCurrency:USD"]

    # BIN with price filter
    bin_filters = base_filters + ["buyingOptions:{FIXED_PRICE}"]
    if min_price is not None and max_price is not None:
        bin_filters.append(f"price:[{min_price}..{max_price}]")
    elif min_price is not None:
        bin_filters.append(f"price:[{min_price}..]")
    elif max_price is not None:
        bin_filters.append(f"price:[..{max_price}]")
    bin_items = _paginate(token, {**base_params, "filter": ",".join(bin_filters)})

    # Auctions — no price filter
    auction_filters = base_filters + ["buyingOptions:{AUCTION}"]
    auction_items = _paginate(token, {**base_params, "filter": ",".join(auction_filters)})

    # Merge, deduplicate by item_id (BIN takes priority if same ID appears in both)
    seen: set[str] = set()
    result: list[dict] = []
    for item in bin_items + auction_items:
        if item["item_id"] not in seen:
            seen.add(item["item_id"])
            result.append(item)

    return result[:2000]


def search_listings_by_keyword(token: str, query: str, min_price: float, max_price: float) -> list[dict]:
    """Return recent BIN listings matching a keyword query within the price range, cheapest first."""
    resp = requests.get(
        f"{BROWSE_BASE}/item_summary/search",
        headers=_headers(token),
        params={
            "q": query,
            "filter": _build_filter(min_price, max_price),
            "limit": 50,
            "sort": "price",
        },
    )
    if resp.status_code != 200:
        return []
    return _parse_items(resp.json())
