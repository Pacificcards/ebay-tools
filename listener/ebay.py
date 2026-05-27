import base64
import re
import requests
from datetime import datetime, timezone, timedelta

BROWSE_BASE = "https://api.ebay.com/buy/browse/v1"
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
        price_val = item.get("price", {}).get("value")
        raw_id = item.get("itemId", "")
        # Browse API returns IDs as "v1|123456789|0" — extract the numeric part
        item_id = raw_id.split("|")[1] if raw_id.count("|") >= 2 else raw_id
        seller = item.get("seller", {})
        results.append({
            "item_id": item_id,
            "title": item.get("title", ""),
            "price": float(price_val) if price_val else 0.0,
            "url": item.get("itemWebUrl", ""),
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
