import os
from datetime import datetime, timezone, timedelta

from shared.db import get_connection
from listener.ebay import get_app_token, search_listings_by_epid, search_listings_by_keyword
from listener.epid_resolver import resolve_epid
from listener.sheets import load_watchlist, update_epid_in_sheet, append_observed_listing
from listener.discord import send_alert, send_stale_alert
from listener.discord_ingest import run_ingest


def _should_run_stale_check() -> bool:
    now = datetime.now(timezone.utc)
    return now.hour == 16 and now.minute < 15


_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%-m/%-d/%Y", "%-m/%-d/%y")


def _parse_last_hit(val: str):
    """Parse a Last Hit date string in any common format. Returns date or None."""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    # gspread may return a Google Sheets serial number as a string
    try:
        serial = float(val)
        # Google Sheets epoch is Dec 30 1899
        from datetime import date as date_cls
        return (date_cls(1899, 12, 30) + timedelta(days=int(serial)))
    except (ValueError, TypeError):
        pass
    return None


def _check_stale(watchlist: list[dict]) -> None:
    stale_threshold = datetime.now(timezone.utc).date() - timedelta(days=3)
    stale = []
    for row in watchlist:
        if row.get("Active (Y/N)", "Y").strip().upper() != "Y":
            continue
        description = row.get("Description", "").strip()
        if not description:
            continue
        last_hit = str(row.get("Last Hit", "") or "").strip()
        if not last_hit:
            stale.append(description)
            continue
        last_hit_date = _parse_last_hit(last_hit)
        if last_hit_date is None:
            print(f"  [stale] unrecognised Last Hit format for '{description}': {last_hit!r} — skipping")
            continue
        if last_hit_date < stale_threshold:
            stale.append(description)
    if stale:
        send_stale_alert(stale)
        print(f"Stale alert sent for {len(stale)} card(s)")


def run():
    client_id = os.environ["EBAY_CLIENT_ID"]
    client_secret = os.environ["EBAY_CLIENT_SECRET"]
    sheet_id = os.environ["GOOGLE_SHEET_ID"]

    token = get_app_token(client_id, client_secret)
    conn = get_connection()

    watchlist = load_watchlist(sheet_id)

    for row in watchlist:
        if row.get("Active (Y/N)", "Y").strip().upper() != "Y":
            continue

        description = row.get("Description", "").strip()
        if not description:
            continue

        try:
            max_price = float(row.get("Max Price ($)", 0))
        except (ValueError, TypeError):
            print(f"Skipping '{description}': invalid max price")
            continue

        try:
            min_price = float(row.get("Min Price ($)", 0) or 0)
        except (ValueError, TypeError):
            min_price = 0.0

        try:
            market_price = float(row.get("Market Price", "") or "") if row.get("Market Price", "") else None
        except (ValueError, TypeError):
            market_price = None

        epid = str(row.get("EPID", "") or "").strip()
        epid_status = str(row.get("EPID Status", "") or "").strip()
        row_index = row["_row_index"]

        # Resolve EPID on first encounter
        if not epid and epid_status != "not_found":
            print(f"Resolving EPID for: {description}")
            epid, status = resolve_epid(row, token)
            update_epid_in_sheet(sheet_id, row_index, epid, status)
            print(f"  → {status}: {epid or 'none'}")
        elif epid_status == "not_found":
            epid = ""

        if epid:
            listings = search_listings_by_epid(token, epid, min_price, max_price)
            print(f"{description}: {len(listings)} listing(s) by EPID (${min_price}–${max_price}, US, BIN, last 12h)")
        else:
            listings = search_listings_by_keyword(token, description, min_price, max_price)
            print(f"{description}: {len(listings)} listing(s) by keyword (${min_price}–${max_price}, US, BIN, last 12h)")

        for listing in listings:
            # Skip zero-feedback and 0% positive sellers
            try:
                feedback_score = listing.get("seller_feedback_score")
                feedback_pct = listing.get("seller_feedback_pct")
                if (feedback_score != "" and int(float(str(feedback_score))) == 0) or \
                   (feedback_pct != "" and float(str(feedback_pct)) == 0.0):
                    print(f"  Skipping {listing['item_id']}: seller has 0 feedback or 0% positive")
                    continue
            except (ValueError, TypeError):
                pass

            item_id = listing["item_id"]

            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM listener_seen_items WHERE item_id = %s", (item_id,))
                if cur.fetchone():
                    continue
                cur.execute(
                    "INSERT INTO listener_seen_items (item_id, watchlist_description) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (item_id, description),
                )
            conn.commit()

            ref_price = market_price if market_price else max_price
            pct_below = round((ref_price - listing["price"]) / ref_price * 100, 1)
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            append_observed_listing(sheet_id, {
                "timestamp": timestamp,
                "description": description,
                "title": listing["title"],
                "price": listing["price"],
                "pct_below": pct_below,
                "item_id": item_id,
                "url": listing["url"],
            })
            send_alert(description, listing, max_price, pct_below, market_price=market_price)
            print(f"  ✓ New: {listing['title']} @ ${listing['price']:.2f}")

    if _should_run_stale_check():
        print("Running stale market price check...")
        _check_stale(watchlist)

    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    channel_id = os.environ.get("DISCORD_WATCHLIST_CHANNEL_ID", "")
    if bot_token and channel_id:
        print("Checking Discord for new watchlist entries...")
        run_ingest(sheet_id, bot_token, channel_id, conn)

    conn.close()


if __name__ == "__main__":
    run()
