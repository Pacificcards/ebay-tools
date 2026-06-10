"""
Polls a Discord channel for new watchlist addition requests, parses them
with Claude, and writes the result to the Watchlist tab in Google Sheets.

Each processed message ID is stored in Supabase to prevent reprocessing.
"""

import json
import os

import anthropic
import requests

DISCORD_API = "https://discord.com/api/v10"

PARSE_PROMPT = """\
Extract watchlist fields from this message about a trading card.
Return JSON only — no explanation, no markdown, no code fences.

Required field:
  description (str) — the card name/description

Optional fields (include only if clearly stated):
  max_price (float) — maximum price to trigger an alert
  min_price (float) — minimum price filter
  market_price (float) — current market value
  category (str) — e.g. Pokemon, Baseball, Basketball

If max_price is missing or ambiguous, return {{"error": "max_price is required — please include a max price (e.g. 'max 500')"}}

Message: {text}"""


# ── Discord API helpers ───────────────────────────────────────────────────────

def _bot_headers(bot_token: str) -> dict:
    return {"Authorization": f"Bot {bot_token}"}


def _fetch_messages(channel_id: str, bot_token: str) -> list[dict]:
    resp = requests.get(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        headers=_bot_headers(bot_token),
        params={"limit": 50},
    )
    if resp.status_code != 200:
        print(f"  [discord_ingest] failed to fetch messages: {resp.status_code} {resp.text}")
        return []
    return resp.json()


def _reply(channel_id: str, message_id: str, bot_token: str, content: str) -> None:
    requests.post(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        headers={**_bot_headers(bot_token), "Content-Type": "application/json"},
        json={"content": content, "message_reference": {"message_id": message_id}},
    )


# ── Supabase dedup ────────────────────────────────────────────────────────────

def _already_processed(conn, message_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM discord_processed_messages WHERE message_id = %s", (message_id,))
        return cur.fetchone() is not None


def _mark_processed(conn, message_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO discord_processed_messages (message_id) VALUES (%s) ON CONFLICT DO NOTHING",
            (message_id,),
        )
    conn.commit()


# ── Claude parsing ────────────────────────────────────────────────────────────

def _parse_entry(text: str) -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        messages=[{"role": "user", "content": PARSE_PROMPT.format(text=text)}],
    )
    raw = message.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(raw)


# ── Orchestration ─────────────────────────────────────────────────────────────

def run_ingest(sheet_id: str, bot_token: str, channel_id: str, conn) -> None:
    from listener.sheets import append_watchlist_row

    messages = _fetch_messages(channel_id, bot_token)
    if not messages:
        return

    # Get our own bot user ID so we can skip our own reply messages
    me_resp = requests.get(f"{DISCORD_API}/users/@me", headers=_bot_headers(bot_token))
    bot_id = me_resp.json().get("id") if me_resp.status_code == 200 else None

    new_count = 0
    for msg in messages:
        message_id = msg["id"]
        author_id = msg.get("author", {}).get("id")

        if author_id == bot_id:
            continue
        if _already_processed(conn, message_id):
            continue

        text = msg.get("content", "").strip()
        if not text:
            _mark_processed(conn, message_id)
            continue

        print(f"  [discord_ingest] processing message: {text[:60]}")

        try:
            entry = _parse_entry(text)
        except Exception as e:
            print(f"  [discord_ingest] parse error: {e}")
            _reply(channel_id, message_id, bot_token, "❌ Sorry, I couldn't parse that. Try again with a description and a max price.")
            _mark_processed(conn, message_id)
            continue

        if "error" in entry:
            _reply(channel_id, message_id, bot_token, f"❌ {entry['error']}")
            _mark_processed(conn, message_id)
            continue

        try:
            append_watchlist_row(sheet_id, entry)
        except Exception as e:
            print(f"  [discord_ingest] sheet write error: {e}")
            _reply(channel_id, message_id, bot_token, "❌ Parsed OK but failed to write to the sheet. Try again.")
            _mark_processed(conn, message_id)
            continue

        parts = [f"✅ Added **{entry['description']}**"]
        if entry.get("max_price"):
            parts.append(f"max ${entry['max_price']:,.0f}")
        if entry.get("market_price"):
            parts.append(f"market ${entry['market_price']:,.0f}")
        if entry.get("min_price"):
            parts.append(f"min ${entry['min_price']:,.0f}")
        _reply(channel_id, message_id, bot_token, " · ".join(parts))

        _mark_processed(conn, message_id)
        new_count += 1

    if new_count:
        print(f"  [discord_ingest] {new_count} new watchlist item(s) added")
