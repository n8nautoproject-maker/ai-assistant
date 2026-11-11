from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.scheduled_job import ScheduledJob
from app.database.models.user import SubscriptionStatus, User

TRIAL_DAYS = 7
MORNING_CHECKIN_HOUR = 9
EVENING_REVIEW_HOUR = 21
# "Пару раз в течение дня" per user request - midday and late afternoon,
# after the morning question but well before evening_review's own overdue
# sweep at EVENING_REVIEW_HOUR.
PRIORITY_CHECKIN_HOURS = (13, 17)
WEEKLY_SUMMARY_WEEKDAY = 6  # Sunday (Monday=0)
WEEKLY_SUMMARY_HOUR = 18
DAILY_MOTIVATION_HOUR = 8
# Early-morning, low-traffic local hour to check whether today is this
# user's trial-expiry or subscription-renewal day - see
# app/scheduler/handlers.py::_handle_subscription_renewal.
SUBSCRIPTION_RENEWAL_HOUR = 3
# Right after local midnight, before anyone's realistically checking
# "Сегодня" yet - see carry_forward_overdue_tasks's docstring for why this
# exists as its own job instead of folding into evening_review.
CARRY_FORWARD_OVERDUE_HOUR = 0
# Same hour as morning_checkin - see carry_forward_life_goals/
# _handle_life_goal_expiry_checkin. A separate job/message rather than
# folded into morning_checkin's own push since it's a no-op most days
# (nothing expired) and shouldn't grow that message's prompt/complexity.
LIFE_GOAL_EXPIRY_CHECKIN_HOUR = 9
# Seeded as a plain custom_reminder (same job_type/payload shape as
# set_recurring_reminder creates), not a dedicated job_type - this is
# deliberately the SAME mechanism a user could set up themselves by asking
# the bot, which means the user can already retime or cancel it via the
# existing list_reminders/cancel_reminder/set_recurring_reminder tools
# without any new code: find it by this text, cancel, recreate at the time
# they want. No separate "change time" tool needed.
DAILY_MOTIVATION_TEXT = (
    "Посмотрите свои задачи на сегодняшний день, поставьте новые. "
    "Пусть этот день будет максимально продуктивным!"
)


def _next_daily_at(now_utc: datetime, tz_name: str, hour: int) -> datetime:
    """First occurrence is computed in the user's real local time; the
    scheduler's daily recurrence then advances it by a flat 24h in UTC
    (see app/scheduler/runner.py) - an accepted drift-on-DST simplification
    for MVP, documented in docs/ARCHITECTURE.md.
    """
    tz = ZoneInfo(tz_name)
    now_local = now_utc.astimezone(tz)
    candidate_local = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate_local <= now_local:
        candidate_local += timedelta(days=1)
    return candidate_local.astimezone(timezone.utc)


def _next_weekly_summary_at(now_utc: datetime, tz_name: str) -> datetime:
    """Same UTC-delta-drift caveat as _next_morning_checkin_at, weekly instead."""
    tz = ZoneInfo(tz_name)
    now_local = now_utc.astimezone(tz)
    days_ahead = (WEEKLY_SUMMARY_WEEKDAY - now_local.weekday()) % 7
    candidate_local = (now_local + timedelta(days=days_ahead)).replace(
        hour=WEEKLY_SUMMARY_HOUR, minute=0, second=0, microsecond=0
    )
    if candidate_local <= now_local:
        candidate_local += timedelta(days=7)
    return candidate_local.astimezone(timezone.utc)


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> User:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is not None:
        # Called on every bot message and every Mini App session-open (see
        # get_current_user in app/auth/dependencies.py for the per-request
        # touch covering everything in between) - the only signal available
        # for a user who only taps around the Mini App and never chats.
        user.last_active_at = datetime.now(timezone.utc)
        await session.commit()
        return user

    user = User(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        subscription_status=SubscriptionStatus.TRIALING.value,
        trial_ends_at=datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS),
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        # Two concurrent first-contacts for the same telegram_id (duplicate
        # Telegram update delivery, or a double-tap opening the Mini App for
        # the very first time) both pass the SELECT above before either
        # commits - the loser hits the unique constraint on telegram_id here
        # instead of the request blowing up with a raw 500. Whoever won just
        # created the real row; fetch and return that one.
        await session.rollback()
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one()
        user.last_active_at = datetime.now(timezone.utc)
        await session.commit()
        return user

    now = datetime.now(timezone.utc)
    session.add(
        ScheduledJob(
            user_id=user.id,
            job_type="morning_checkin",
            next_run_at=_next_daily_at(now, user.timezone, MORNING_CHECKIN_HOUR),
            recurrence="daily",
        )
    )
    session.add(
        ScheduledJob(
            user_id=user.id,
            job_type="evening_review",
            next_run_at=_next_daily_at(now, user.timezone, EVENING_REVIEW_HOUR),
            recurrence="daily",
        )
    )
    for hour in PRIORITY_CHECKIN_HOURS:
        session.add(
            ScheduledJob(
                user_id=user.id,
                job_type="priority_checkin",
                next_run_at=_next_daily_at(now, user.timezone, hour),
                recurrence="daily",
            )
        )
    session.add(
        ScheduledJob(
            user_id=user.id,
            job_type="weekly_habit_summary",
            next_run_at=_next_weekly_summary_at(now, user.timezone),
            recurrence="weekly",
        )
    )
    session.add(
        ScheduledJob(
            user_id=user.id,
            job_type="custom_reminder",
            next_run_at=_next_daily_at(now, user.timezone, DAILY_MOTIVATION_HOUR),
            recurrence="daily",
            payload={"text": DAILY_MOTIVATION_TEXT},
        )
    )
    session.add(
        ScheduledJob(
            user_id=user.id,
            job_type="subscription_renewal",
            next_run_at=_next_daily_at(now, user.timezone, SUBSCRIPTION_RENEWAL_HOUR),
            recurrence="daily",
        )
    )
    session.add(
        ScheduledJob(
            user_id=user.id,
            job_type="carry_forward_overdue",
            next_run_at=_next_daily_at(now, user.timezone, CARRY_FORWARD_OVERDUE_HOUR),
            recurrence="daily",
        )
    )
    session.add(
        ScheduledJob(
            user_id=user.id,
            job_type="life_goal_expiry_checkin",
            next_run_at=_next_daily_at(now, user.timezone, LIFE_GOAL_EXPIRY_CHECKIN_HOUR),
            recurrence="daily",
        )
    )
    await session.commit()
    await session.refresh(user)
    return user
