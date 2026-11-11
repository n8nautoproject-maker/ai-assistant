import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.scheduled_job import ScheduledJob
from app.database.models.user import User
from app.integrations import aladhan

DAILY_REFRESH_JOB_TYPE = "namaz_daily_refresh"
PRAYER_REMINDER_JOB_TYPE = "namaz_prayer_reminder"
# 00:10 local - well before the earliest realistic Fajr (even at high
# summer latitudes, e.g. ~01:30 in Kazan in July per a live check this
# session), so the day's schedule is always ready before the first prayer.
DAILY_REFRESH_HOUR, DAILY_REFRESH_MINUTE = 0, 10

PRAYER_NAMES_RU = {
    "Fajr": "Фаджр",
    "Dhuhr": "Зухр",
    "Asr": "Аср",
    "Maghrib": "Магриб",
    "Isha": "Иша",
}


def _next_local_time(hour: int, minute: int, tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(timezone.utc).astimezone(tz)
    candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now_local:
        candidate += timedelta(days=1)
    return candidate


async def _clear_prayer_reminders(session: AsyncSession, user_id) -> None:
    result = await session.execute(
        select(ScheduledJob).where(
            ScheduledJob.user_id == user_id,
            ScheduledJob.job_type == PRAYER_REMINDER_JOB_TYPE,
            ScheduledJob.is_active.is_(True),
        )
    )
    for job in result.scalars().all():
        job.is_active = False


async def refresh_namaz_schedule(session: AsyncSession, user: User) -> None:
    """Fetches today's (user-local date) prayer times and (re)schedules the
    day's remaining one-off reminders. Called both right after the user
    enables namaz reminders (so it's live immediately, not only from
    tomorrow) and once a day by the namaz_daily_refresh job - prayer times
    shift daily and can't be computed once and reused.
    """
    if not user.namaz_city or not user.namaz_country:
        return

    await _clear_prayer_reminders(session, user.id)

    tz = ZoneInfo(user.timezone)
    now_local = datetime.now(timezone.utc).astimezone(tz)
    timings = await aladhan.get_prayer_times(user.namaz_city, user.namaz_country, now_local.date())

    for prayer, time_str in timings.items():
        hour, minute = (int(part) for part in time_str.split(":"))
        prayer_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if prayer_local <= now_local:
            continue  # already passed today - only matters right after enabling
        session.add(
            ScheduledJob(
                user_id=user.id,
                job_type=PRAYER_REMINDER_JOB_TYPE,
                next_run_at=prayer_local.astimezone(timezone.utc),
                recurrence=None,
                payload={"prayer": prayer},
            )
        )

    await session.commit()


async def enable_namaz_reminders(session: AsyncSession, user: User, city: str, country: str) -> None:
    # Validate the city BEFORE persisting anything. Real bug found live: a
    # misspelled/nonexistent city made Aladhan raise, the user was told "не
    # получилось", but namaz_city/namaz_country and an active daily-refresh
    # job had already been committed further down - the account was left
    # "half-enabled": get_next_namaz sees a truthy namaz_city and no longer
    # says "не включены", but the schedule never populates since the
    # nightly refresh keeps failing on the same bad city, so it falls back
    # to "расписание обновится ночью" forever.
    tz = ZoneInfo(user.timezone)
    now_local = datetime.now(timezone.utc).astimezone(tz)
    await aladhan.get_prayer_times(city, country, now_local.date())

    user.namaz_city = city
    user.namaz_country = country

    result = await session.execute(
        select(ScheduledJob).where(
            ScheduledJob.user_id == user.id,
            ScheduledJob.job_type == DAILY_REFRESH_JOB_TYPE,
        )
    )
    daily_job = result.scalars().first()
    next_refresh = _next_local_time(DAILY_REFRESH_HOUR, DAILY_REFRESH_MINUTE, user.timezone)
    if daily_job is None:
        session.add(
            ScheduledJob(
                user_id=user.id,
                job_type=DAILY_REFRESH_JOB_TYPE,
                next_run_at=next_refresh,
                recurrence="daily",
                payload={},
            )
        )
    else:
        daily_job.is_active = True
        daily_job.next_run_at = next_refresh

    await session.commit()
    # Live immediately rather than waiting for tomorrow's 00:10 refresh -
    # schedules whatever prayers are still ahead today.
    await refresh_namaz_schedule(session, user)


async def disable_namaz_reminders(session: AsyncSession, user: User) -> bool:
    result = await session.execute(
        select(ScheduledJob).where(
            ScheduledJob.user_id == user.id,
            ScheduledJob.job_type == DAILY_REFRESH_JOB_TYPE,
            ScheduledJob.is_active.is_(True),
        )
    )
    daily_job = result.scalars().first()
    if daily_job is None:
        return False
    daily_job.is_active = False
    await _clear_prayer_reminders(session, user.id)
    # get_next_namaz's only "is this enabled at all" check is
    # `if not user.namaz_city` - leaving it set after disabling made that
    # check stay falsely "enabled" forever, so a later "когда намаз"
    # skipped the honest "не включены" reply and instead reported a
    # schedule that will never be populated again.
    user.namaz_city = None
    user.namaz_country = None
    await session.commit()
    return True


async def get_upcoming_prayers(session: AsyncSession, user_id: uuid.UUID) -> list[ScheduledJob]:
    """Active namaz_prayer_reminder jobs for this user, soonest first - the
    real scheduled times, not something the model should answer from its
    own general knowledge. Real bug found live: asked "when is the nearest
    namaz today" at 18:14, the model had no tool for this and just
    fabricated an answer (a prayer time that had already passed hours
    earlier). Already-fired prayers today are excluded automatically -
    the scheduler deactivates a one-off job (recurrence=None) once it
    fires, same as task_reminder.
    """
    result = await session.execute(
        select(ScheduledJob)
        .where(
            ScheduledJob.user_id == user_id,
            ScheduledJob.job_type == PRAYER_REMINDER_JOB_TYPE,
            ScheduledJob.is_active.is_(True),
        )
        .order_by(ScheduledJob.next_run_at)
    )
    return list(result.scalars().all())
