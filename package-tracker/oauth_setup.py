#!/usr/bin/env python3
"""
One-time local script: mint a Google OAuth refresh token for Gmail (read-only) + Tasks.
Not run by the scheduled job.

Usage:
    .venv/bin/python package-tracker/oauth_setup.py

You'll need the Client ID and Client Secret of a "Desktop app" OAuth client from
Google Cloud Console. A browser opens; approve access.
The three values are saved into the repo's .env (never printed), ready to copy to GitHub secrets.
"""

import getpass
import http.server
import re
import json
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from google_auth import SCOPES, TOKEN_URI

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
PORT = 8765
REDIRECT_URI = f"http://127.0.0.1:{PORT}/"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def save_to_env(values: dict) -> None:
    text = ENV_PATH.read_text() if ENV_PATH.exists() else ""
    for key, value in values.items():
        line = f"{key}={value}"
        if re.search(rf"^{key}=.*$", text, re.M):
            text = re.sub(rf"^{key}=.*$", lambda _: line, text, flags=re.M)
        else:
            text = text.rstrip("\n") + f"\n{line}\n"
    ENV_PATH.write_text(text)


def main():
    client_id = input("Client ID: ").strip()
    client_secret = getpass.getpass("Client Secret: ").strip()
    if not client_id or not client_secret:
        sys.exit("ERROR: both values are required")

    state = secrets.token_urlsafe(16)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",          # forces a refresh token even on re-auth
        "state": state,
    })

    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            result.update(params)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Done - you can close this tab and return to the terminal.")

        def log_message(self, *args):
            pass

    print("\nOpening browser for Google sign-in...")
    webbrowser.open(url)
    print(f"If it didn't open, visit:\n{url}\n")
    with http.server.HTTPServer(("127.0.0.1", PORT), Handler) as server:
        server.handle_request()

    if result.get("state") != state or "code" not in result:
        sys.exit(f"ERROR: authorization failed: {result.get('error', 'no code returned')}")

    body = urllib.parse.urlencode({
        "code": result["code"],
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()
    with urllib.request.urlopen(TOKEN_URI, data=body) as resp:
        token = json.load(resp)

    if "refresh_token" not in token:
        sys.exit("ERROR: no refresh token returned. Revoke the app at "
                 "myaccount.google.com/permissions and run again.")

    save_to_env({
        "GOOGLE_OAUTH_CLIENT_ID": client_id,
        "GOOGLE_OAUTH_CLIENT_SECRET": client_secret,
        "GOOGLE_OAUTH_REFRESH_TOKEN": token["refresh_token"],
    })
    print(f"\nSuccess. Saved GOOGLE_OAUTH_CLIENT_ID / _SECRET / _REFRESH_TOKEN to {ENV_PATH}")


if __name__ == "__main__":
    main()
