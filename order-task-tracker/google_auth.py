"""Authorized HTTP session for Gmail + Tasks, from a stored OAuth refresh token."""

import os

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/tasks",
]
TOKEN_URI = "https://oauth2.googleapis.com/token"


def get_session() -> AuthorizedSession:
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"],
        client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return AuthorizedSession(creds)
