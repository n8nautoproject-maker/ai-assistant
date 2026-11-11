import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.payment import Payment
from app.database.models.user import SubscriptionStatus, User
from app.integrations import yookassa

# One named constant, change here if the price needs to move.
PRICE_RUB = "490.00"
SUBSCRIPTION_PERIOD_DAYS = 30


async def start_checkout(session: AsyncSession, user: User, return_url: str) -> str:
    """Starts a first-time (or re-activation) checkout - always goes
    through ЮKassa's hosted confirmation page, even for a user who already
    has a saved payment_method_id, since this is a user-initiated "pay me
    now" action (as opposed to charge_recurring below, the unattended
    scheduled path). Returns the URL to redirect the user to.
    """
    # A double-tap on "Оплатить" (or a client retry after a slow/timed-out
    # response whose request actually landed) used to always create a
    # second, independent real payment - both could go on to succeed, and
    # handle_webhook_notification correctly grants a period for each of
    # them, since they're genuinely different payment ids. That means the
    # user pays twice. If there's already a payment we're still waiting on,
    # re-check its live status and hand back the same confirmation page
    # instead of starting a new charge.
    existing = await session.execute(
        select(Payment)
        .where(Payment.user_id == user.id, Payment.status == "pending")
        .order_by(Payment.created_at.desc())
    )
    pending = existing.scalars().first()
    if pending is not None:
        try:
            payment_data = await yookassa.get_payment(pending.yookassa_payment_id)
        except yookassa.YooKassaError:
            payment_data = None
        if payment_data is not None:
            pending.status = payment_data["status"]
            if payment_data["status"] == "pending":
                confirmation_url = (payment_data.get("confirmation") or {}).get("confirmation_url")
                if confirmation_url:
                    await session.commit()
                    return confirmation_url
            await session.commit()

    idempotence_key = str(uuid.uuid4())
    result = await yookassa.create_payment(
        amount_rub=PRICE_RUB,
        description=f"Подписка на {SUBSCRIPTION_PERIOD_DAYS} дней",
        return_url=return_url,
        idempotence_key=idempotence_key,
        save_payment_method=True,
        metadata={"user_id": str(user.id)},
    )
    session.add(
        Payment(
            user_id=user.id,
            yookassa_payment_id=result["id"],
            amount=Decimal(PRICE_RUB),
            status=result["status"],
        )
    )
    await session.commit()
    return result["confirmation"]["confirmation_url"]


async def handle_webhook_notification(session: AsyncSession, yookassa_payment_id: str) -> None:
    """Never trusts the webhook POST body itself (not cryptographically
    signed) - re-fetches the payment by id via an authenticated API call
    and acts only on that. A forged notification for a real payment id just
    causes a harmless re-check of that payment's real status; a forged id
    for a payment that doesn't exist raises YooKassaError from get_payment,
    which the route layer catches and turns into a 200 (nothing to retry).
    """
    payment_data = await yookassa.get_payment(yookassa_payment_id)

    result = await session.execute(
        select(Payment).where(Payment.yookassa_payment_id == yookassa_payment_id)
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        return  # not one of ours (or already cleaned up) - nothing to update

    # ЮKassa retries a notification on anything but a 200, and the webhook
    # body itself carries no signature (see this function's own docstring) -
    # a replayed/duplicated "succeeded" notification for a payment already
    # applied must be a no-op. Without this guard, anyone who knows a real
    # payment_id (visible in the checkout URL and the email receipt) could
    # re-POST it to /billing/webhook and extend their own subscription for
    # free, indefinitely.
    already_applied = payment.status == "succeeded"
    payment.status = payment_data["status"]

    if payment_data["status"] == "succeeded" and not already_applied:
        user = await session.get(User, payment.user_id)
        if user is not None:
            user.subscription_status = SubscriptionStatus.ACTIVE.value
            # Extend from whichever is later: "now" for a first purchase or
            # a renewal after the period already lapsed, or the existing
            # current_period_end for a renewal paid before it expired - so
            # paying early adds a full period on top instead of being
            # clipped back to "now + 30".
            now = datetime.now(timezone.utc)
            base = max(user.current_period_end or now, now)
            user.current_period_end = base + timedelta(days=SUBSCRIPTION_PERIOD_DAYS)
            payment_method = payment_data.get("payment_method") or {}
            if payment_method.get("saved") and payment_method.get("id"):
                user.yookassa_payment_method_id = payment_method["id"]

    await session.commit()


async def charge_recurring(session: AsyncSession, user: User) -> bool:
    """Called by the subscription_renewal scheduled job when a user's
    current_period_end has arrived - charges the saved payment method with
    no redirect/user interaction. Returns whether the charge succeeded;
    either way, subscription_status is updated so the gate reflects reality
    immediately rather than waiting for the next poll.
    """
    if not user.yookassa_payment_method_id:
        user.subscription_status = SubscriptionStatus.PAST_DUE.value
        await session.commit()
        return False

    idempotence_key = str(uuid.uuid4())
    try:
        result = await yookassa.create_payment(
            amount_rub=PRICE_RUB,
            description=f"Продление подписки на {SUBSCRIPTION_PERIOD_DAYS} дней",
            idempotence_key=idempotence_key,
            payment_method_id=user.yookassa_payment_method_id,
            metadata={"user_id": str(user.id)},
        )
    except yookassa.YooKassaError:
        user.subscription_status = SubscriptionStatus.PAST_DUE.value
        await session.commit()
        return False

    session.add(
        Payment(
            user_id=user.id,
            yookassa_payment_id=result["id"],
            amount=Decimal(PRICE_RUB),
            status=result["status"],
        )
    )

    if result["status"] == "succeeded":
        user.subscription_status = SubscriptionStatus.ACTIVE.value
        # Same "extend from the later of now/current_period_end" as
        # handle_webhook_notification, for consistency - in practice the
        # caller (subscription_renewal) only ever invokes this once
        # current_period_end has already passed, so this is equivalent to
        # "now" today, but doesn't silently clip a period if that invariant
        # ever changes.
        now = datetime.now(timezone.utc)
        base = max(user.current_period_end or now, now)
        user.current_period_end = base + timedelta(days=SUBSCRIPTION_PERIOD_DAYS)
        await session.commit()
        return True

    user.subscription_status = SubscriptionStatus.PAST_DUE.value
    await session.commit()
    return False
