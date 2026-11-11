"""Gmail adapter - pure HTTP client, no database access.

OAuth mechanics live in google_oauth.py (shared across Google adapters);
scopes (gmail.readonly, gmail.send) were already added to that shared
list when Drive was built, so no new reconnect is needed for this.
"""

import base64
from email.mime.text import MIMEText

import httpx

from app.integrations.google_oauth import raise_for_status

API_BASE = "https://www.googleapis.com/gmail/v1/users/me"


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


async def list_messages(access_token: str, query: str | None = None, max_results: int = 10) -> list[dict]:
    auth = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        list_response = await client.get(
            f"{API_BASE}/messages",
            headers=auth,
            params={"q": query, "maxResults": max_results} if query else {"maxResults": max_results},
        )
        raise_for_status(list_response, "list_messages")
        message_ids = [m["id"] for m in list_response.json().get("messages", [])]

        messages = []
        for message_id in message_ids:
            detail = await client.get(
                f"{API_BASE}/messages/{message_id}",
                headers=auth,
                params={
                    "format": "metadata",
                    "metadataHeaders": ["From", "Subject", "Date"],
                },
            )
            raise_for_status(detail, "get message")
            data = detail.json()
            headers = data.get("payload", {}).get("headers", [])
            messages.append(
                {
                    "id": message_id,
                    "from": _header(headers, "From"),
                    "subject": _header(headers, "Subject"),
                    "date": _header(headers, "Date"),
                    "snippet": data.get("snippet", ""),
                }
            )
    return messages


async def send_message(access_token: str, to: str, subject: str, body: str) -> dict:
    mime_message = MIMEText(body)
    mime_message["to"] = to
    mime_message["subject"] = subject
    raw = base64.urlsafe_b64encode(mime_message.as_bytes()).decode()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/messages/send",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"raw": raw},
        )
    raise_for_status(response, "send_message")
    return response.json()
