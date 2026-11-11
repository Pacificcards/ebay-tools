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


def _last_hits(conn) -> dict:
    """Most recent alert date (UTC) per watchlist description, from listener_seen_items.

    Every alert inserts a row there, so this is the source of truth for "last hit". The sheet's
    Last Hit column is display-only: rows added via Discord ingestion used to have no formula
    there, which made every such card look stale.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT watchlist_description, MAX(first_seen_at) FROM listener_seen_items "
            "WHERE watchlist_description IS NOT NULL GROUP BY watchlist_description"
        )
        return {desc.strip().lower(): ts.astimezone(timezone.utc).date() for desc, ts in cur.fetchall() if ts}


def _stale_cards(watchlist: list[dict], last_hits: dict, today) -> list[str]:
    """Active cards with no alert in the 3 days before today (or never)."""
    threshold = today - timedelta(days=3)
    stale = []
    for row in watchlist:
        if row.get("Active (Y/N)", "Y").strip().upper() != "Y":
            continue
        description = row.get("Description", "").strip()
        if not description:
            continue
        last_hit = last_hits.get(description.lower())
        if last_hit is None or last_hit < threshold:
            stale.append(description)
    return stale


def _check_stale(watchlist: list[dict], conn) -> None:
    stale = _stale_cards(watchlist, _last_hits(conn), datetime.now(timezone.utc).date())
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
            print(f"{description}: {len(listings)} listing(s) by EPID (${min_price}–${max_price}, US, BIN, ungraded, last 12h)")
        else:
            listings = search_listings_by_keyword(token, description, min_price, max_price)
            print(f"{description}: {len(listings)} listing(s) by keyword (${min_price}–${max_price}, US, BIN, ungraded, last 12h)")

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
        _check_stale(watchlist, conn)

    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    channel_id = os.environ.get("DISCORD_WATCHLIST_CHANNEL_ID", "")
    if bot_token and channel_id:
        print("Checking Discord for new watchlist entries...")
        run_ingest(sheet_id, bot_token, channel_id, conn)

    conn.close()


if __name__ == "__main__":
    run()
