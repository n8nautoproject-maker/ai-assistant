import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import CheckoutResponse, SubscriptionStatusResponse
from app.auth.dependencies import get_current_user
from app.config import get_settings
from app.core.billing.service import PRICE_RUB, handle_webhook_notification, start_checkout
from app.database.models.user import User
from app.database.session import get_db
from app.integrations.yookassa import YooKassaError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/status", response_model=SubscriptionStatusResponse)
async def get_billing_status(user: User = Depends(get_current_user)) -> SubscriptionStatusResponse:
    return SubscriptionStatusResponse(
        subscription_plan=user.subscription_plan,
        subscription_status=user.subscription_status,
        trial_ends_at=user.trial_ends_at,
        current_period_end=user.current_period_end,
        price_rub=PRICE_RUB,
    )


@router.post("/checkout", response_model=CheckoutResponse)
async def checkout(
    # Deliberately get_current_user, not require_active_subscription - a
    # lapsed/trialing user must be ABLE to reach this endpoint, that's the
    # whole point of it.
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CheckoutResponse:
    settings = get_settings()
    try:
        confirmation_url = await start_checkout(db, user, return_url=settings.app_base_url)
    except YooKassaError:
        logger.exception("failed to start checkout for user %s", user.id)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Не получилось создать ссылку на оплату — попробуйте ещё раз."
        ) from None
    return CheckoutResponse(confirmation_url=confirmation_url)


@router.post("/webhook")
async def webhook(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    # Unauthenticated by necessity - ЮKassa calls this directly, same
    # pattern as GET /integrations/google/callback. Never trusts the body
    # for anything beyond "which payment id to re-check" - see
    # handle_webhook_notification's docstring.
    body = await request.json()

    # Only payment.* events carry a payment in `object` - refund.succeeded's
    # `object` is a refund, payment_method.active's is a payment method, and
    # treating either id as a payment id would make get_payment 404 forever
    # (nothing here handles refunds/payment methods yet). Acknowledge and
    # skip rather than erroring ЮKassa into endless retries.
    event = body.get("event", "")
    if not event.startswith("payment."):
        return {"status": "ok"}

    payment_id = (body.get("object") or {}).get("id")
    if not payment_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing payment id")

    try:
        await handle_webhook_notification(db, payment_id)
    except YooKassaError:
        # Re-fetch itself failed (e.g. transient network issue) - ЮKassa
        # retries notifications on non-200, so returning an error here (not
        # swallowing it) is the correct way to ask for a retry.
        logger.exception("failed to verify yookassa payment %s", payment_id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "verification failed") from None

    return {"status": "ok"}
