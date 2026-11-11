"""Shared Google OAuth mechanics - one Google login covers every Google
capability this project supports (Calendar, Drive, ...), so token
exchange/refresh lives here once instead of being duplicated per adapter.
Adapters (google_calendar.py, google_drive.py) only implement their own
API calls, taking an access_token param.
"""

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.config import get_settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# calendar.events is a "sensitive" OAuth scope - normal Google verification
# unlocks it for every user. drive.readonly and the gmail.* scopes are
# "restricted" - they require a paid, annually-renewed CASA security audit,
# which isn't worth it yet: Gmail has no adapter built at all, and Drive
# import (app/integrations/google_drive.py) is a nice-to-have, not the
# actual ask - real user feedback specifically wants Calendar sync. Scope
# request deliberately narrowed to just calendar.events so verification
# stays in the cheap tier; Drive import's tool registration is disabled to
# match (see tools_registry.py) rather than left silently broken. Restore
# drive.readonly/gmail.* here (and re-enable the Drive tool) if CASA is
# ever done.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
]


class GoogleAPIError(Exception):
    pass


class GoogleTokenRevokedError(GoogleAPIError):
    """The access token was rejected outright (401) - distinct from other
    API failures so a caller can prompt the user to reconnect immediately
    instead of surfacing a generic "try again later". Refresh-time
    revocation (invalid_grant) is already caught separately in
    app/core/integrations/service.py::get_valid_access_token; this is the
    other half - the user revokes access in their Google account mid-
    lifetime, before the locally-cached access_token's own tracked expiry,
    so it still looks valid to us but Google now rejects every real call
    made with it.
    """


def raise_for_status(response: httpx.Response, action: str) -> None:
    if response.status_code == 401:
        raise GoogleTokenRevokedError(f"{action} failed: 401 {response.text}")
    if response.status_code >= 400:
        raise GoogleAPIError(f"{action} failed: {response.status_code} {response.text}")


@dataclass
class TokenResponse:
    access_token: str
    refresh_token: str | None
    expires_in: int
    scope: str


def build_authorization_url(state: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        # Forces Google to re-issue a refresh_token even on a repeat
        # connect - without this, a second consent only returns an
        # access_token, silently leaving us without a way to refresh later.
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_tokens(code: str) -> TokenResponse:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if response.status_code >= 400:
        raise GoogleAPIError(f"token exchange failed: {response.status_code} {response.text}")
    data = response.json()
    return TokenResponse(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_in=data["expires_in"],
        scope=data.get("scope", " ".join(SCOPES)),
    )


async def refresh_access_token(refresh_token: str) -> TokenResponse:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "grant_type": "refresh_token",
            },
        )
    if response.status_code >= 400:
        raise GoogleAPIError(f"token refresh failed: {response.status_code} {response.text}")
    data = response.json()
    return TokenResponse(
        access_token=data["access_token"],
        refresh_token=refresh_token,  # Google doesn't reissue it on refresh
        expires_in=data["expires_in"],
        scope=data.get("scope", " ".join(SCOPES)),
    )
