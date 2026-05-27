import os
import requests


def send_alert(description: str, listing: dict, max_price: float, pct_below: float):
    webhook_url = os.environ["DISCORD_WEBHOOK_URL"]

    seller = listing["seller_username"] or "unknown"
    feedback_score = listing["seller_feedback_score"]
    feedback_pct = listing["seller_feedback_pct"]
    seller_line = f"Seller: {seller}"
    if feedback_score != "" and feedback_pct != "":
        seller_line += f" ({feedback_score} feedback, {feedback_pct}% positive)"
    elif feedback_score != "":
        seller_line += f" ({feedback_score} feedback)"

    message = (
        f"**{description}**\n"
        f"${listing['price']:.2f} — {pct_below}% below your ${max_price:.2f} target\n"
        f"{seller_line}\n"
        f"{listing['url']}"
    )
    requests.post(webhook_url, json={"content": message})
