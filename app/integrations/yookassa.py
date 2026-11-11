"""ЮKassa (YooKassa) payments API - pure httpx adapter, no DB access. Auth
is HTTP Basic (shop_id as username, secret_key as password) - a static
app-level credential (same shop for every user), so this module reads it
from settings directly rather than threading it through every caller, same
convention as app/integrations/speech_to_text.py (also a static app-level
key) rather than google_calendar.py (a per-user OAuth token, necessarily
passed in by the caller that already fetched it).
"""

import httpx

from app.config import get_settings

BASE_URL = "https://api.yookassa.ru/v3"


class YooKassaError(Exception):
    pass


async def create_payment(
    amount_rub: str,
    description: str,
    idempotence_key: str,
    return_url: str | None = None,
    save_payment_method: bool = False,
    payment_method_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Creates a payment. Two distinct shapes, matching how ЮKassa "does
    subscriptions" (there's no separate subscription object):

    - First charge (payment_method_id=None): needs `confirmation` (redirect
      to ЮKassa's own hosted page - this app never sees card details) and
      should pass save_payment_method=True to get a reusable
      payment_method.id back once it succeeds.
    - Recurring charge (payment_method_id set): no `confirmation` at all -
      charges immediately against the saved method, no user interaction.

    idempotence_key must be a fresh UUID per real attempt (not per retry of
    the same attempt) - ЮKassa's own duplicate-charge prevention.
    """
    body: dict = {
        "amount": {"value": amount_rub, "currency": "RUB"},
        "capture": True,
        "description": description,
    }
    if payment_method_id:
        body["payment_method_id"] = payment_method_id
    else:
        body["confirmation"] = {"type": "redirect", "return_url": return_url}
        if save_payment_method:
            body["save_payment_method"] = True
    if metadata:
        body["metadata"] = metadata

    settings = get_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{BASE_URL}/payments",
            json=body,
            auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
            headers={"Idempotence-Key": idempotence_key},
        )
    if response.status_code >= 400:
        raise YooKassaError(f"create_payment failed: {response.status_code} {response.text}")
    return response.json()


async def get_payment(payment_id: str) -> dict:
    """Re-fetches a payment's real status via an authenticated call - the
    only trustworthy source of truth for a webhook notification, since
    ЮKassa's notification POST body itself isn't cryptographically signed.
    """
    settings = get_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{BASE_URL}/payments/{payment_id}",
            auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
        )
    if response.status_code >= 400:
        raise YooKassaError(f"get_payment failed: {response.status_code} {response.text}")
    return response.json()
