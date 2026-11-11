import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.reminders.service import window_instance
from app.database.models.scheduled_job import ScheduledJob
from app.database.models.user import User
from app.database.session import async_session_factory
from app.scheduler.handlers import JOB_HANDLERS
from app.subscriptions.gate import SubscriptionRequired, check_subscription

logger = logging.getLogger(__name__)

RECURRENCE_DELTAS = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}


def _advance_past(candidate: datetime, delta: timedelta, now: datetime) -> datetime:
    """Keeps candidate a whole number of `delta` steps ahead until it's
    strictly past `now`, in one arithmetic jump - instead of the poller
    re-firing the job on every ~60s tick (POLL_INTERVAL_SECONDS) until it
    organically catches up one period at a time. Without this, a worker
    outage longer than a single period (a deploy, a restart, a crash)
    makes every poll after recovery see the job as still due and fire the
    handler again, once per missed period, in a tight burst - a real
    incident for interval reminders (several duplicate pushes within a
    couple of minutes) and for any daily/weekly job after a longer outage.
    """
    if candidate > now:
        return candidate
    periods_missed = (now - candidate) // delta + 1
    return candidate + periods_missed * delta


def _next_interval_run_at(job: ScheduledJob, user_timezone: str) -> datetime:
    """Next fire time for an "interval" job - a fixed number of minutes after
    the last run, except when that would land outside the current window
    instance (per window_instance, which handles an overnight window like
    "20:00-02:00" via a wraparound duration): then it rolls to the next
    window's start instead of firing outside the window (e.g. "every 90 min
    from 9 to 18" must not fire at 19:30)."""
    payload = job.payload
    interval = timedelta(minutes=payload["interval_minutes"])
    candidate_utc = _advance_past(
        job.next_run_at + interval, interval, datetime.now(timezone.utc)
    )

    tz = ZoneInfo(user_timezone)
    candidate_local = candidate_utc.astimezone(tz)
    anchor, end = window_instance(candidate_local, payload["window_start"], payload["window_end"])
    if anchor <= candidate_local <= end:
        return candidate_utc
    return anchor.astimezone(timezone.utc)


def _next_run_at(job: ScheduledJob, user_timezone: str) -> datetime | None:
    """Computes the next fire time for recurring jobs.

    Adds a fixed delta in UTC, which drifts against user local time across
    DST changes. Acceptable for MVP; revisit once jobs are scheduled from
    user.timezone directly rather than from a stored UTC instant.
    """
    if job.recurrence == "interval":
        return _next_interval_run_at(job, user_timezone)
    if job.recurrence not in RECURRENCE_DELTAS:
        return None
    delta = RECURRENCE_DELTAS[job.recurrence]
    return _advance_past(job.next_run_at + delta, delta, datetime.now(timezone.utc))


async def poll_due_jobs() -> None:
    async with async_session_factory() as session:
        due_jobs = await _fetch_due_jobs(session)
        for job in due_jobs:
            await _run_job(session, job)


async def _fetch_due_jobs(session: AsyncSession) -> list[ScheduledJob]:
    result = await session.execute(
        select(ScheduledJob).where(
            ScheduledJob.is_active.is_(True),
            ScheduledJob.next_run_at <= datetime.now(timezone.utc),
        )
    )
    return list(result.scalars().all())


async def _run_job(session: AsyncSession, job: ScheduledJob) -> None:
    handler = JOB_HANDLERS.get(job.job_type)
    if handler is None:
        logger.warning("no handler registered for job_type=%s", job.job_type)
        job.is_active = False
        await session.commit()
        return

    user = await session.get(User, job.user_id)
    if user is None:
        job.is_active = False
        await session.commit()
        return

    # Persist the job's own next-fire state BEFORE running the handler's
    # side effect (almost always a Telegram send). A process kill between
    # "the handler sent the message" and "next_run_at got committed" (a
    # worker restart mid-deploy, OOM, systemctl restart - all routine) used
    # to leave the job still due, so the very next poll after recovery fired
    # the handler again and re-sent the same proactive message/check-in.
    # Committing this first means the worst case if the handler itself then
    # crashes is a single missed occurrence, not a duplicate one - a safer
    # failure direction for anything that pushes a message to the user.
    next_run = _next_run_at(job, user.timezone)
    if next_run is None:
        job.is_active = False
    else:
        job.next_run_at = next_run
    await session.commit()

    try:
        check_subscription(user)
    except SubscriptionRequired:
        # Real gap found live: interactive messages already refuse a lapsed
        # trial (app/subscriptions/gate.py, checked in bot.py/API), but
        # every proactive job here (morning_checkin, evening_review,
        # custom_reminder, and weekly_habit_summary - which calls the LLM)
        # kept firing regardless, indefinitely, for a user who can't even
        # reply. Skipping (not deactivating) the job here means it resumes
        # automatically the moment they resubscribe, with no re-seeding.
        pass
    else:
        try:
            await handler(user, job.payload)
        except Exception:
            logger.exception("job %s failed for user %s", job.job_type, user.id)
