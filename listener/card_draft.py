"""Card Draft: looks up eBay item specifics + market prices for cards in the Card Draft tab.

For each row with a Card Name (but no Game yet), this script:
  1. Searches eBay for the card (BIN, US, Graded:No across all raw-card categories)
  2. Fetches full item details for the top 5 listings → extracts localizedAspects
  3. Finds the consensus value for each aspect across those 5 listings
  4. Asks Claude Haiku to generate a clean eBay title (≤80 chars)
  5. Runs a price check on the same search results (IQR filter → p30/p75)
  6. Writes everything back to the Card Draft tab
"""
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).parent))
import ebay
import sheets

_EXCLUDE_RE = re.compile(
    r"\b(psa|bgs|cgc|sgc|hga|ags|graded|slab|gem\s*mint|gem\s*10|"
    r"lot|bundle|case|sleeve|toploader|binder|display|booster|pack\s+of|set\s+of)\b",
    re.IGNORECASE,
)


def _consensus(all_aspects: list[dict]) -> dict:
    """For each aspect key, pick the most common value across all items."""
    aggregated: dict[str, list] = defaultdict(list)
    for aspects in all_aspects:
        for k, v in aspects.items():
            if v:
                aggregated[k].append(v)
    return {k: Counter(vals).most_common(1)[0][0] for k, vals in aggregated.items()}


def _filter_prices(listings: list[dict]) -> list[float]:
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


def _percentile(sorted_prices: list[float], pct: float) -> float:
    n = len(sorted_prices)
    if n == 0:
        return 0.0
    idx = (pct / 100) * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    return sorted_prices[lo] + (idx - lo) * (sorted_prices[hi] - sorted_prices[lo])


def _generate_title(consensus: dict, card_name: str, card_number: str, claude: anthropic.Anthropic) -> str:
    context = "\n".join([
        f"Card Name: {card_name}",
        f"Card Number: {card_number}",
        f"Set: {consensus.get('Set', '')}",
        f"Rarity: {consensus.get('Rarity', '')}",
        f"Finish: {consensus.get('Finish', '')}",
        f"Game: {consensus.get('Game', 'Pokemon TCG')}",
        f"Language: {consensus.get('Language', 'English')}",
    ])
    msg = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=80,
        messages=[{
            "role": "user",
            "content": (
                "Generate a concise eBay listing title for this Pokemon card. "
                "Max 80 characters. Include: card name, card number, set name, and rarity/finish if notable. "
                "Return ONLY the title text, nothing else.\n\n"
                + context
            ),
        }],
    )
    return msg.content[0].text.strip()[:80]


def main():
    client_id = os.environ["EBAY_CLIENT_ID"]
    client_secret = os.environ["EBAY_CLIENT_SECRET"]
    sheet_id = os.environ["POKEMON_SHEET_ID"]

    claude = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    print("Authenticating with eBay...")
    token = ebay.get_app_token(client_id, client_secret)

    print("Reading Card Draft tab...")
    rows = sheets.read_card_draft(sheet_id)
    if not rows:
        print("No unprocessed rows found (Card Name filled, Game blank).")
        return

    print(f"Processing {len(rows)} card(s)...\n")
    for row in rows:
        card_name = str(row["Card Name"]).strip()
        card_number = str(row.get("Card Number", "")).strip()
        row_idx = row["_row_index"]
        query = f"{card_name} {card_number}".strip()

        print(f"  [{row_idx}] {query}")

        # 1. Search eBay
        listings = ebay.search_card_listings(token, query)
        if not listings:
            print(f"       ✗ No listings found — skipping")
            continue
        print(f"       {len(listings)} listings found")

        # 2. Fetch full item details for top 5 → extract aspects
        all_aspects = []
        for item in listings[:5]:
            aspects = ebay.fetch_item_aspects(token, item["raw_item_id"])
            if aspects:
                all_aspects.append(aspects)
        print(f"       Aspect data from {len(all_aspects)} items")

        consensus = _consensus(all_aspects)

        # 3. Generate title
        title = _generate_title(consensus, card_name, card_number, claude)
        print(f"       Title: {title!r}")

        # 4. Price check on same search results
        prices = _filter_prices(listings)
        clearing = _percentile(prices, 30) if len(prices) >= 3 else None
        holding = _percentile(prices, 75) if len(prices) >= 3 else None
        if clearing is not None:
            print(f"       Clearing ${clearing:.2f}  Holding ${holding:.2f}  ({len(prices)} clean listings)")
        else:
            print(f"       Too few clean listings for pricing ({len(prices)})")

        # 5. Write back — prefer "Character Family" aspect, fall back to "Character"
        character = consensus.get("Character Family") or consensus.get("Character", "")
        data = {
            "game": consensus.get("Game", ""),
            "language": consensus.get("Language", ""),
            "title": title,
            "set": consensus.get("Set", ""),
            "rarity": consensus.get("Rarity", ""),
            "finish": consensus.get("Finish", ""),
            "card_type": consensus.get("Card Type", ""),
            "character": character,
            "card_size": consensus.get("Card Size", ""),
            "material": consensus.get("Material", ""),
            "vintage": consensus.get("Vintage", ""),
            "clearing_price": clearing,
            "holding_price": holding,
            "n_listings": len(prices),
        }
        sheets.write_card_draft_row(sheet_id, row_idx, data)
        print(f"       ✓ Written to sheet")

    print("\nDone.")


if __name__ == "__main__":
    main()
