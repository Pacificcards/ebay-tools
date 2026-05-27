from listener.ebay import get_epid_from_listing


def resolve_epid(row: dict, token: str) -> tuple[str, str]:
    """
    Resolve EPID for a watchlist row via hint URL. Returns (epid, status).
    Status is 'resolved' if found, 'not_found' otherwise (keyword search used as fallback in main).
    """
    hint_urls = row.get("Hint URL(s)", "").strip()

    if hint_urls:
        # Support both comma-separated and newline-separated URLs
        import re
        for url in [u.strip() for u in re.split(r'[,\n]', hint_urls) if u.strip()]:
            epid = get_epid_from_listing(token, url)
            if epid:
                return epid, "resolved"

    return "", "not_found"
