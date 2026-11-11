import os
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_PT = ZoneInfo("America/Los_Angeles")

# Discord merges up to 4 embeds that share the same `url` into one image gallery.
MAX_ALERT_IMAGES = 4

_COLOR_GREEN = 0x2ECC71
_COLOR_YELLOW = 0xF1C40F
_COLOR_RED = 0xE74C3C


def _listed_time_pt(iso_date_str: str) -> str:
    if not iso_date_str:
        return "unknown"
    dt = datetime.fromisoformat(iso_date_str.replace("Z", "+00:00")).astimezone(_PT)
    return dt.strftime("%-I:%M %p")


def _alert_lines(listing: dict, max_price: float, pct_below: float, market_price: float | None):
    """Return (price_line, details_line, embed_color or None)."""
    feedback_score = listing["seller_feedback_score"]
    feedback_pct = listing["seller_feedback_pct"]
    if feedback_score != "" and feedback_pct != "":
        seller_line = f"Seller: {feedback_score} feedback ({feedback_pct}% positive)"
    elif feedback_score != "":
        seller_line = f"Seller: {feedback_score} feedback"
    else:
        seller_line = "Seller: no feedback data"

    listed_line = f"Listed: {_listed_time_pt(listing['item_creation_date'])}"

    direction = "below" if pct_below >= 0 else "above"
    color = None
    if market_price:
        if pct_below > 5:
            emoji, color = "🟢", _COLOR_GREEN
        elif pct_below < -5:
            emoji, color = "🔴", _COLOR_RED
        else:
            emoji, color = "🟡", _COLOR_YELLOW
        price_line = f"${listing['price']:.2f} — {emoji} {abs(pct_below)}% {direction} market (${market_price:.2f})"
    else:
        price_line = f"${listing['price']:.2f} — {abs(pct_below)}% {direction} your ${max_price:.2f} target"

    return price_line, f"{listed_line} · {seller_line}", color


def _build_alert_payload(description: str, listing: dict, max_price: float, pct_below: float,
                         market_price: float | None = None) -> dict:
    """Webhook payload: one embed with the alert text and first photo, plus one embed per extra
    photo (same url, so Discord shows them as a single gallery)."""
    price_line, details_line, color = _alert_lines(listing, max_price, pct_below, market_price)
    url = listing["url"]
    main = {
        "title": description[:256],
        "url": url,
        "description": f"{price_line}\n{details_line}",
    }
    if color is not None:
        main["color"] = color

    images = (listing.get("image_urls") or [])[:MAX_ALERT_IMAGES]
    embeds = [main]
    if images:
        main["image"] = {"url": images[0]}
        embeds += [{"url": url, "image": {"url": img}} for img in images[1:]]
    return {"embeds": embeds}


def _plain_text_message(description: str, listing: dict, max_price: float, pct_below: float,
                        market_price: float | None = None) -> str:
    price_line, details_line, _ = _alert_lines(listing, max_price, pct_below, market_price)
    return f"**{description}**\n{price_line}\n{details_line}\n{listing['url']}"


def send_alert(description: str, listing: dict, max_price: float, pct_below: float, market_price: float | None = None):
    webhook_url = os.environ["DISCORD_WEBHOOK_URL"]
    payload = _build_alert_payload(description, listing, max_price, pct_below, market_price)
    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        if resp.ok:
            return
        print(f"  [discord] photo alert rejected ({resp.status_code}: {resp.text[:200]}), sending plain text")
    except requests.RequestException as e:
        print(f"  [discord] photo alert failed ({e}), sending plain text")
    requests.post(
        webhook_url,
        json={"content": _plain_text_message(description, listing, max_price, pct_below, market_price)},
        timeout=15,
    )


def send_stale_alert(stale_items: list[str]) -> None:
    webhook_url = os.environ["DISCORD_WEBHOOK_URL"]
    lines = "\n".join(f"• {item}" for item in stale_items)
    message = (
        f"**⚠️ Stale price targets — no new listings in 3+ days**\n"
        f"{lines}\n\n"
        f"Review market price or adjust your trigger price for each."
    )
    requests.post(webhook_url, json={"content": message})
