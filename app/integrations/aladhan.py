"""Aladhan prayer-times API - public, no API key needed. Pure httpx adapter,
same "no DB access, just the external call" pattern as google_calendar.py.
"""

from datetime import date

import httpx

BASE_URL = "https://api.aladhan.com/v1/timingsByCity"
# Method 14 = "Spiritual Administration of Muslims of Russia" - confirmed
# against a live API response (meta.method.name), the relevant calculation
# authority for this project's CIS-focused audience rather than a generic
# international default (e.g. MWL/ISNA).
CALCULATION_METHOD = 14

PRAYERS = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]


class AladhanAPIError(Exception):
    pass


async def get_prayer_times(city: str, country: str, on_date: date) -> dict[str, str]:
    """Returns {prayer_name: "HH:MM"} for the 5 daily prayers, already in
    the requested city's local time (Aladhan resolves the city's own
    timezone server-side, no conversion needed here).

    The API responds with a 302 redirect (http->https-style) before the
    real 200 - follow_redirects=True is required, not optional.
    """
    url = f"{BASE_URL}/{on_date:%d-%m-%Y}"
    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
        response = await client.get(
            url, params={"city": city, "country": country, "method": CALCULATION_METHOD}
        )
    if response.status_code >= 400:
        raise AladhanAPIError(f"aladhan request failed: {response.status_code} {response.text}")

    data = response.json()
    if data.get("code") != 200:
        raise AladhanAPIError(f"aladhan returned code={data.get('code')}: {data.get('status')}")

    timings = data["data"]["timings"]
    return {prayer: timings[prayer] for prayer in PRAYERS}
