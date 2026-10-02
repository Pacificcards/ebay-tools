"""
FedEx Track API: delivery ETA / delivered date straight from FedEx.

Credentials: FEDEX_API_KEY / FEDEX_SECRET_KEY (production keys from developer.fedex.com,
project with the Track API). OAuth client-credentials token, then batched track calls.

Probe one number (prints status and dates only, no addresses):
    python package-tracker/fedex_track.py --probe 383881342851
"""

import argparse
import os
import sys
from datetime import date

import requests

API = "https://apis.fedex.com"
MAX_PER_CALL = 30          # FedEx limit per track request
TIMEOUT = 30


class FedExError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.environ.get("FEDEX_API_KEY") and os.environ.get("FEDEX_SECRET_KEY"))


def _token() -> str:
    resp = requests.post(f"{API}/oauth/token", timeout=TIMEOUT, data={
        "grant_type": "client_credentials",
        "client_id": os.environ["FEDEX_API_KEY"],
        "client_secret": os.environ["FEDEX_SECRET_KEY"],
    })
    if resp.status_code != 200:
        raise FedExError(f"FedEx auth failed: HTTP {resp.status_code} {_error_codes(resp)}")
    return resp.json()["access_token"]


def _error_codes(resp) -> str:
    try:
        return ", ".join(f"{e.get('code')}: {e.get('message')}" for e in resp.json().get("errors", []))
    except ValueError:
        return resp.text[:200]


def _track_raw(token: str, numbers: list[str]) -> list[dict]:
    resp = requests.post(
        f"{API}/track/v1/trackingnumbers", timeout=TIMEOUT,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "X-locale": "en_US"},
        json={"includeDetailedScans": False,
              "trackingInfo": [{"trackingNumberInfo": {"trackingNumber": n}} for n in numbers]},
    )
    if resp.status_code != 200:
        raise FedExError(f"FedEx track failed: HTTP {resp.status_code} {_error_codes(resp)}")
    return resp.json().get("output", {}).get("completeTrackResults", [])


def _day(value: str | None) -> date | None:
    """'2026-10-01T00:00:00-06:00' -> date(2026, 10, 1): the date as FedEx states it locally."""
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def parse_result(result: dict) -> dict:
    """One trackResults entry -> {"eta", "delivered_on"} (dates or None)."""
    times = {t.get("type"): t.get("dateTime") for t in result.get("dateAndTimes", [])}
    delivered = (_day(times.get("ACTUAL_DELIVERY"))
                 if (result.get("latestStatusDetail") or {}).get("code") == "DL" else None)
    window = (result.get("estimatedDeliveryTimeWindow") or {}).get("window") or {}
    standard = (result.get("standardTransitTimeWindow") or {}).get("window") or {}
    eta = (_day(times.get("ESTIMATED_DELIVERY")) or _day(window.get("ends"))
           or _day(window.get("begins")) or _day(standard.get("ends")))
    return {"eta": None if delivered else eta, "delivered_on": delivered}


def lookup(numbers: list[str]) -> dict:
    """[tracking] -> {tracking: {"eta", "delivered_on"}}; numbers FedEx doesn't know are left out."""
    token = _token()
    out = {}
    for i in range(0, len(numbers), MAX_PER_CALL):
        for complete in _track_raw(token, numbers[i:i + MAX_PER_CALL]):
            results = [r for r in complete.get("trackResults", []) if not r.get("error")]
            if results:
                out[complete["trackingNumber"]] = parse_result(results[0])
    return out


def _probe(number: str) -> int:
    if not configured():
        print("FEDEX_API_KEY / FEDEX_SECRET_KEY not set")
        return 1
    try:
        token = _token()
        print("Auth: OK")
        complete = _track_raw(token, [number])
    except (FedExError, requests.RequestException) as exc:
        print(exc)
        return 1
    for c in complete:
        for r in c.get("trackResults", []):
            if r.get("error"):
                print(f"Error: {r['error'].get('code')}: {r['error'].get('message')}")
                continue
            status = r.get("latestStatusDetail") or {}
            print(f"Status: {status.get('code')} - {status.get('description')}")
            for t in r.get("dateAndTimes", []):
                print(f"  {t.get('type')}: {t.get('dateTime')}")
            print(f"  estimatedDeliveryTimeWindow: {r.get('estimatedDeliveryTimeWindow')}")
            print(f"  standardTransitTimeWindow: {r.get('standardTransitTimeWindow')}")
            print(f"Parsed: {parse_result(r)}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", required=True, help="tracking number to look up")
    sys.exit(_probe(parser.parse_args().probe))
