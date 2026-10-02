"""
Delivery ETA / delivered date from carrier tracking APIs.

Every open shipment on a supported carrier is re-checked each run (the APIs are free),
so ETA changes after pickup reach the task. Supported: FedEx (fedex_track.py).
Other carriers get no lookup: their task keeps the email's date, or "No ETA".
"""

import re

import requests

import fedex_track

_TRACKING_RE = re.compile(r"[A-Za-z0-9]{8,40}")
_FEDEX_NUMBER_RE = re.compile(r"\d{12}|\d{15}")      # FedEx Express/Ground; only used when carrier unknown


def carrier_of(tracking: str, carrier: str | None, tracking_url: str | None = None) -> str | None:
    """'fedex' for a FedEx shipment, else None (unsupported or unknown)."""
    name = (carrier or "").lower()
    if "fedex" in name or "fedex.com" in (tracking_url or "").lower():
        return "fedex"
    if not name and _FEDEX_NUMBER_RE.fullmatch(tracking):
        return "fedex"
    return None


def lookup(shipments: list[tuple[str, str | None, str | None]]) -> tuple[dict, list[str]]:
    """shipments: [(tracking, carrier, tracking_url)].

    Returns ({tracking: {"eta", "delivered_on"}}, warnings). A carrier API failing only
    costs that carrier's lookups for this run; it never stops the run.
    """
    fedex = [t for t, c, url in shipments
             if _TRACKING_RE.fullmatch(t) and carrier_of(t, c, url) == "fedex"]
    results, warnings = {}, []
    if fedex:
        if not fedex_track.configured():
            warnings.append("FedEx lookup skipped: FEDEX_API_KEY / FEDEX_SECRET_KEY not set")
        else:
            try:
                results.update(fedex_track.lookup(fedex))
            except (fedex_track.FedExError, requests.RequestException, KeyError, ValueError) as exc:
                warnings.append(f"FedEx lookup failed, continuing without it: {exc}")
    return results, warnings
