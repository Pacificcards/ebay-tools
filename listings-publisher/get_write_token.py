#!/usr/bin/env python3
"""
One-time script to generate a write-scoped eBay refresh token for the listings publisher.
This creates a SEPARATE token from the existing read token — nothing existing is affected.

Usage:
    python listings-publisher/get_write_token.py

You'll need: App ID, Cert ID, and RuName from the eBay developer console.
After running, copy EBAY_REFRESH_TOKEN_WRITE into your .env file.
"""

import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

SCOPE = " ".join([
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
])
AUTH_URL = "https://auth.ebay.com/oauth2/authorize"
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"


def prompt(label: str, secret: bool = False) -> str:
    if secret:
        import getpass
        return getpass.getpass(f"{label}: ").strip()
    value = input(f"{label}: ").strip()
    if not value:
        print(f"ERROR: {label} is required")
        sys.exit(1)
    return value


def main():
    print("=== eBay Write Token Setup (listings-publisher) ===")
    print("This generates a NEW token and does not affect your existing EBAY_REFRESH_TOKEN.\n")
    print("You'll need your App ID, Cert ID, and RuName from:")
    print("  https://developer.ebay.com → My Account → Application Access Keys\n")

    client_id = prompt("App ID (Client ID)")
    client_secret = prompt("Cert ID (Client Secret)", secret=True)
    ru_name = prompt("RuName")

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": ru_name,
        "scope": SCOPE,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    print("\nOpening the eBay consent page in your browser...")
    print(f"If it doesn't open automatically, visit:\n\n  {url}\n")
    webbrowser.open(url)

    print("After you click Agree, your browser will redirect to https://localhost/...")
    print("The page will fail to load — that's expected.")
    print("Copy the ENTIRE URL from the address bar and paste it below.\n")

    redirect_url = prompt("Paste the full redirect URL here")

    parsed = urllib.parse.urlparse(redirect_url)
    qs = urllib.parse.parse_qs(parsed.query)
    if "code" not in qs:
        print("\nERROR: No 'code' found in that URL. Make sure you copied the full redirect URL.")
        sys.exit(1)

    auth_code = urllib.parse.unquote(qs["code"][0])

    print("\nExchanging code for tokens...")
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": ru_name,
    }).encode()

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
            tokens = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"\nHTTP {e.code} error:\n{e.read().decode()}")
        sys.exit(1)

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print(f"\nERROR: No refresh_token in response:\n{json.dumps(tokens, indent=2)}")
        sys.exit(1)

    expires_days = tokens.get("refresh_token_expires_in", 0) // 86400

    print("\n" + "=" * 60)
    print("SUCCESS")
    print("=" * 60)
    print(f"\nAdd this to your .env file:\n")
    print(f"EBAY_REFRESH_TOKEN_WRITE={refresh_token}")
    print(f"\nThis token expires in ~{expires_days} days (18 months).")
    print("Your existing EBAY_REFRESH_TOKEN is unchanged.")
    print("=" * 60)


if __name__ == "__main__":
    main()
