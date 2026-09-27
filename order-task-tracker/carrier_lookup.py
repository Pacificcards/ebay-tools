"""
Web lookup of delivery ETA for shipments whose emails carry no date.

One batched headless-Claude call with WebSearch only. It receives carrier + tracking
strings, never email text, so a malicious email can't steer a tool-enabled model.
"""

import re
from datetime import date

import claude_cli
import config

_TRACKING_RE = re.compile(r"[A-Za-z0-9]{8,40}")
_CARRIER_RE = re.compile(r"[A-Za-z .&-]{2,30}")

SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "tracking": {"type": "string"},
            "eta": {"type": ["string", "null"]},
            "delivered_on": {"type": ["string", "null"]},
        },
        "required": ["tracking", "eta", "delivered_on"],
        "additionalProperties": False,
    }}},
    "required": ["results"],
    "additionalProperties": False,
}

PROMPT = """For each shipment below, use web search (at most 2 searches per shipment) to find \
its current tracking status. Return eta = the estimated delivery date (YYYY-MM-DD, the latest \
date if a range), or delivered_on = the delivery date if it was already delivered. Use null for \
anything you cannot confirm from a search result - never guess. Today is {today}.

Shipments:
{shipments}"""


def _parse(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def lookup(shipments: list[tuple[str, str | None]], today: date) -> dict:
    """shipments: [(tracking, carrier)] -> {tracking: {"eta", "delivered_on"}}. Capped per run."""
    # tracking/carrier originate in email text: allow only plain tokens into the prompt
    shipments = [(t, c if c and _CARRIER_RE.fullmatch(c) else None)
                 for t, c in shipments if _TRACKING_RE.fullmatch(t)]
    shipments = shipments[: config.MAX_WEB_LOOKUPS_PER_RUN]
    if not shipments:
        return {}
    listing = "\n".join(f"- tracking {t} (carrier: {c or 'unknown'})" for t, c in shipments)
    out = claude_cli.run(
        PROMPT.format(today=today.isoformat(), shipments=listing), SCHEMA, config.LOOKUP_MODEL,
        tools="WebSearch", max_turns=2 * len(shipments) + 4,
    )
    wanted = {t for t, _ in shipments}
    return {r["tracking"]: {"eta": _parse(r["eta"]), "delivered_on": _parse(r["delivered_on"])}
            for r in out["results"] if r["tracking"] in wanted}
