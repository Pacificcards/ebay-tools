#!/usr/bin/env python3
"""
One-time script: mint a READ-ONLY eBay refresh token for the shipping-policy audit.

Scopes: base api_scope + sell.account.readonly (read business policies; cannot change them).
Does not touch EBAY_REFRESH_TOKEN or EBAY_REFRESH_TOKEN_WRITE.

Usage (from repo root):
    .venv/bin/python policy_audit/get_account_token.py

Needs App ID, Cert ID and RuName from developer.ebay.com -> Application Access Keys.
Paste the printed EBAY_REFRESH_TOKEN_ACCOUNT=... line into .env.
"""
import base64
import getpass
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

SCOPE = " ".join([
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.account.readonly",
])
AUTH_URL = "https://auth.ebay.com/oauth2/authorize"
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"


def _ask(label: str, secret: bool = False) -> str:
    value = (getpass.getpass(f"{label}: ") if secret else input(f"{label}: ")).strip()
    if not value:
        sys.exit(f"ERROR: {label} is required")
    return value


def main():
    print("=== eBay read-only account token (shipping-policy audit) ===\n")
    client_id = _ask("App ID (Client ID)")
    client_secret = _ask("Cert ID (Client Secret)", secret=True)
    ru_name = _ask("RuName")

    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code",
        "redirect_uri": ru_name, "scope": SCOPE,
    })
    print(f"\nOpening the consent page. If it doesn't open, visit:\n\n  {url}\n")
    webbrowser.open(url)
    print("After Agree, the browser redirects to a page that may fail to load - that's expected.")
    redirect = _ask("Paste the full redirect URL")

    qs = urllib.parse.parse_qs(urllib.parse.urlparse(redirect).query)
    if "code" not in qs:
        sys.exit("ERROR: no 'code' in that URL.")

    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": qs["code"][0],
            "redirect_uri": ru_name,
        }).encode(),
        headers={"Authorization": f"Basic {creds}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            tokens = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()}")

    refresh = tokens.get("refresh_token")
    if not refresh:
        sys.exit("ERROR: no refresh_token in the response.")
    print("\nAdd this line to .env:\n")
    print(f"EBAY_REFRESH_TOKEN_ACCOUNT={refresh}\n")


if __name__ == "__main__":
    main()
