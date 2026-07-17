"""Google OAuth (installed-app flow) with tokens in the OS credential store.

One Google connection serves every Google-backed connector (Gmail always;
Google Calendar on Windows/Linux where EventKit doesn't exist). The user
supplies their own OAuth "Desktop app" credentials JSON downloaded from
Google Cloud Console (docs/google-oauth.md walks through it). Scopes are the
narrowest that cover the enabled features. Refresh tokens live only in the
OS credential store — never in SQLite or the repository.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from ..storage.keychain import SecretStore

log = logging.getLogger(__name__)

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
]
CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]
TOKEN_KEY = "google_oauth_token"


class GoogleAuth:
    def __init__(self, secrets: SecretStore, scopes: list[str]):
        self._secrets = secrets
        self._scopes = scopes

    def is_connected(self) -> bool:
        return self._secrets.get(TOKEN_KEY) is not None

    def disconnect(self) -> None:
        self._secrets.delete(TOKEN_KEY)

    async def connect(self, credentials_file: str) -> str:
        """Run the interactive OAuth flow in a worker thread.

        Opens the user's browser; returns the connected account email.
        """
        path = Path(credentials_file).expanduser()
        if not path.is_file():
            raise FileNotFoundError(
                f"Credentials file not found: {credentials_file}. "
                "Download the OAuth 'Desktop app' JSON from Google Cloud Console."
            )
        return await asyncio.to_thread(self._run_flow, path)

    def _run_flow(self, path: Path) -> str:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(str(path), self._scopes)
        creds = flow.run_local_server(port=0, open_browser=True)
        self._secrets.set(TOKEN_KEY, creds.to_json())
        email = self._probe_email(creds)
        log.info("Google account connected")
        return email

    def get_credentials(self):
        """Return refreshed google Credentials, or None if not connected.

        A revoked/expired-beyond-refresh token clears itself so the UI shows
        'disconnected' instead of failing on every call.
        """
        raw = self._secrets.get(TOKEN_KEY)
        if raw is None:
            return None

        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_info(json.loads(raw), self._scopes)
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                log.warning("Google token no longer refreshable; disconnecting")
                self.disconnect()
                return None
            self._secrets.set(TOKEN_KEY, creds.to_json())
        return creds

    @staticmethod
    def _probe_email(creds) -> str:
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()
        return profile.get("emailAddress", "unknown")
