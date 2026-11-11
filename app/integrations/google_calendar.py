"""Google Calendar adapter - pure HTTP client, no database access.

OAuth mechanics live in google_oauth.py (shared across Google adapters).
Built on httpx (already a dependency) rather than
google-api-python-client - too heavy for a handful of REST calls.
"""

from datetime import datetime

import httpx

from app.integrations.google_oauth import raise_for_status

CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


async def create_event(
    access_token: str,
    summary: str,
    start: datetime,
    end: datetime,
    description: str | None = None,
) -> dict:
    body = {
        "summary": summary,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }
    if description:
        body["description"] = description

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{CALENDAR_API_BASE}/calendars/primary/events",
            headers={"Authorization": f"Bearer {access_token}"},
            json=body,
        )
    raise_for_status(response, "create_event")
    return response.json()


async def update_event(
    access_token: str,
    event_id: str,
    summary: str,
    start: datetime,
    end: datetime,
    description: str | None = None,
) -> dict:
    body = {
        "summary": summary,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }
    if description:
        body["description"] = description

    async with httpx.AsyncClient() as client:
        response = await client.patch(
            f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            json=body,
        )
    raise_for_status(response, "update_event")
    return response.json()


async def delete_event(access_token: str, event_id: str) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.delete(
            f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    # 410 Gone means it's already deleted (e.g. removed by hand in Google
    # Calendar) - not an error from our side, nothing left to do either way.
    if response.status_code in (404, 410):
        return
    raise_for_status(response, "delete_event")


async def list_events(access_token: str, time_min: datetime, time_max: datetime) -> list[dict]:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{CALENDAR_API_BASE}/calendars/primary/events",
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "timeMin": time_min.isoformat(),
                "timeMax": time_max.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
            },
        )
    raise_for_status(response, "list_events")
    return response.json().get("items", [])
