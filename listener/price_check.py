"""Price check: reads 'Price Check' tab from Pokemon sheet, searches eBay active
listings, and writes Clearing Price (p30) + Holding Price (p75) back per card."""
import os
import re
import sys
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).parent))
import ebay
import sheets

# Remove graded slabs and accessories from price distribution
_EXCLUDE_RE = re.compile(
    r"\b(psa|bgs|cgc|sgc|hga|ags|graded|slab|gem\s*mint|gem\s*10|"
    r"lot|bundle|case|sleeve|toploader|binder|display|booster|pack\s+of|set\s+of)\b",
    re.IGNORECASE,
)


def _simplify_query(description: str, client: anthropic.Anthropic) -> str:
    """Ask Claude to strip a card description down to a clean eBay search query."""
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=60,
        messages=[{
            "role": "user",
            "content": (
                "Convert this trading card description into a short eBay search query. "
                "Keep the card name, number, set name, and key identifiers. "
                "Remove filler words. Return ONLY the search query, nothing else.\n\n"
                f"Description: {description}"
            ),
        }],
    )
    return msg.content[0].text.strip()


def _percentile(sorted_prices: list[float], pct: float) -> float:
    n = len(sorted_prices)
    if n == 0:
        return 0.0
    idx = (pct / 100) * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    return sorted_prices[lo] + (idx - lo) * (sorted_prices[hi] - sorted_prices[lo])


def _filter(listings: list[dict]) -> list[float]:
    """Title filter + IQR outlier removal. Returns sorted clean price list."""
    prices = [
        item["price"] for item in listings
        if item["price"] > 0 and not _EXCLUDE_RE.search(item["title"])
    ]
    if len(prices) < 4:
        return sorted(prices)
    prices.sort()
    q1 = _percentile(prices, 25)
    q3 = _percentile(prices, 75)
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return [p for p in prices if lo <= p <= hi]


def main():
    client_id = os.environ["EBAY_CLIENT_ID"]
    client_secret = os.environ["EBAY_CLIENT_SECRET"]
    sheet_id = os.environ["POKEMON_SHEET_ID"]

    claude = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    print("Authenticating with eBay...")
    token = ebay.get_app_token(client_id, client_secret)

    print("Reading Price Check tab...")
    rows = sheets.read_price_check(sheet_id)
    if not rows:
        print("No rows with descriptions found.")
        return

    print(f"Processing {len(rows)} row(s)...\n")
    for row in rows:
        description = str(row["Description"]).strip()
        row_idx = row["_row_index"]
        print(f"  [{row_idx}] {description}")

        query = _simplify_query(description, claude)
        print(f"       Query: {query!r}")

        listings = ebay.search_listings_for_price(token, query)
        prices = _filter(listings)
        print(f"       {len(listings)} raw → {len(prices)} after filter")

        if len(prices) < 3:
            print(f"       ✗ Too few clean results — skipping")
            sheets.write_price_check_row(sheet_id, row_idx, None, None, len(prices))
            continue

        clearing = _percentile(prices, 30)
        holding = _percentile(prices, 75)
        sheets.write_price_check_row(sheet_id, row_idx, clearing, holding, len(prices))
        print(f"       ✓ Clearing ${clearing:.2f}  Holding ${holding:.2f}")

    print("\nDone.")


if __name__ == "__main__":
    main()
