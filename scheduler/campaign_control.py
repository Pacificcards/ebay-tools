#!/usr/bin/env python3
"""
Pause or resume one or more eBay Promoted Listings campaigns.

Usage:
    python3 -m scheduler.campaign_control pause
    python3 -m scheduler.campaign_control resume

Campaigns are defined in scheduler/campaigns.json.

Required environment variables:
    EBAY_CLIENT_ID
    EBAY_CLIENT_SECRET
    EBAY_REFRESH_TOKEN
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
API_BASE = "https://api.ebay.com/sell/marketing/v1"
SCOPE = "https://api.ebay.com/oauth/api_scope/sell.marketing"


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"ERROR: environment variable {name} is not set")
        sys.exit(1)
    return value


def get_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": SCOPE,
        }
    ).encode()

    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"ERROR: token refresh failed — HTTP {e.code}: {e.read().decode()}")
        sys.exit(1)

    token = body.get("access_token")
    if not token:
        print(f"ERROR: no access_token in token response: {body}")
        sys.exit(1)

    return token


def call_campaign_api(action: str, campaign_id: str, access_token: str) -> bool:
    url = f"{API_BASE}/ad_campaign/{campaign_id}/{action}"
    req = urllib.request.Request(
        url,
        data=b"",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        print(f"  FAILED — HTTP {e.code}: {e.read().decode()}")
        return False

    if status == 204:
        print(f"  SUCCESS")
        return True
    else:
        print(f"  FAILED — unexpected HTTP {status}")
        return False


def load_campaigns() -> list[dict]:
    config_path = os.path.join(os.path.dirname(__file__), "campaigns.json")
    try:
        with open(config_path) as f:
            campaigns = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: campaigns.json not found at {config_path}")
        sys.exit(1)
    if not campaigns:
        print("ERROR: campaigns.json is empty")
        sys.exit(1)
    return campaigns


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("pause", "resume"):
        print("Usage: python3 -m scheduler.campaign_control [pause|resume]")
        sys.exit(1)

    action = sys.argv[1]

    client_id = require_env("EBAY_CLIENT_ID")
    client_secret = require_env("EBAY_CLIENT_SECRET")
    refresh_token = require_env("EBAY_REFRESH_TOKEN")
    campaigns = load_campaigns()

    print(f"Refreshing access token...")
    access_token = get_access_token(client_id, client_secret, refresh_token)
    print(f"Access token obtained.\n")

    results = {}
    for campaign in campaigns:
        campaign_id = campaign["id"]
        name = campaign.get("name", campaign_id)
        print(f"{name} ({campaign_id}) — {action}...")
        results[name] = call_campaign_api(action, campaign_id, access_token)

    print("\n--- Summary ---")
    any_failed = False
    for name, success in results.items():
        status = "SUCCESS" if success else "FAILED"
        print(f"  {name}: {status}")
        if not success:
            any_failed = True

    if any_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
