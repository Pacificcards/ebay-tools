import os
import requests
from datetime import datetime, timezone


def _relative_time(iso_date_str: str) -> str:
    if not iso_date_str:
        return "unknown"
    dt = datetime.fromisoformat(iso_date_str.replace("Z", "+00:00"))
    minutes = int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m ago" if mins else f"{hours}h ago"


def send_alert(description: str, listing: dict, max_price: float, pct_below: float, market_price: float | None = None):
    webhook_url = os.environ["DISCORD_WEBHOOK_URL"]

    feedback_score = listing["seller_feedback_score"]
    feedback_pct = listing["seller_feedback_pct"]
    if feedback_score != "" and feedback_pct != "":
        seller_line = f"Seller: {feedback_score} feedback ({feedback_pct}% positive)"
    elif feedback_score != "":
        seller_line = f"Seller: {feedback_score} feedback"
    else:
        seller_line = "Seller: no feedback data"

    listed_line = f"Listed: {_relative_time(listing['item_creation_date'])}"

    if market_price:
        direction = "below" if pct_below >= 0 else "above"
        price_line = f"${listing['price']:.2f} — {abs(pct_below)}% {direction} market (${market_price:.2f})"
    else:
        direction = "below" if pct_below >= 0 else "above"
        price_line = f"${listing['price']:.2f} — {abs(pct_below)}% {direction} your ${max_price:.2f} target"

    message = (
        f"**{description}**\n"
        f"{price_line}\n"
        f"{listed_line} · {seller_line}\n"
        f"{listing['url']}"
    )
    requests.post(webhook_url, json={"content": message})
