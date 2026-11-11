from datetime import datetime, timezone

from app.database.models.user import SubscriptionStatus, User


class SubscriptionRequired(Exception):
    """Raised when a user's plan does not allow a metered action.

    Channel-agnostic: both the HTTP layer (app/auth/dependencies.py) and
    the bot handler catch this and translate it into their own response
    format, so the check itself lives in one place.
    """


def check_subscription(user: User) -> None:
    now = datetime.now(timezone.utc)

    if user.subscription_status == SubscriptionStatus.TRIALING.value:
        if user.trial_ends_at and user.trial_ends_at < now:
            raise SubscriptionRequired("trial expired")
        return

    if user.subscription_status == SubscriptionStatus.ACTIVE.value:
        # Defense-in-depth: the daily subscription_renewal job is what
        # normally demotes an expired ACTIVE user to PAST_DUE (see
        # app/scheduler/handlers.py::_handle_subscription_renewal). If that
        # job ever misses a user - scheduler downtime, an unrelated bug -
        # this keeps access from staying open indefinitely past the paid
        # period instead of trusting subscription_status alone.
        if user.current_period_end and user.current_period_end < now:
            raise SubscriptionRequired("subscription period ended")
        return

    raise SubscriptionRequired("subscription inactive")
