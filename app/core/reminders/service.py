import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.scheduled_job import ScheduledJob
from app.database.models.user import User


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))

# Reuses scheduled_jobs/recurrence - the same mechanism the morning check-in
# and weekly habit summary already run on (see app/scheduler/runner.py's
# RECURRENCE_DELTAS) - a custom_reminder job is just a user-authored text
# message on that same daily/weekly cadence, not a separate system.
JOB_TYPE = "custom_reminder"


def window_instance(
    reference_local: datetime, window_start: str, window_end: str
) -> tuple[datetime, datetime]:
    """Returns the (start, end) of the interval-reminder window instance
    containing reference_local, or of the next upcoming instance if
    reference_local falls outside every window.

    Modeled as a start anchor + a wraparound duration (window_end minus
    window_start, mod 24h) rather than comparing hour:minute directly -
    that naive comparison assumes window_start < window_end within one
    calendar day and silently breaks for an overnight window like
    "20:00-02:00" (window_end's hour is numerically smaller). Shared by
    tools_registry.py (first-fire time) and scheduler/runner.py (next-fire
    time) so both treat overnight windows the same way.
    """
    start_hour, start_minute = (int(part) for part in window_start.split(":"))
    end_hour, end_minute = (int(part) for part in window_end.split(":"))
    start_of_day_minutes = start_hour * 60 + start_minute
    end_of_day_minutes = end_hour * 60 + end_minute
    duration = timedelta(minutes=(end_of_day_minutes - start_of_day_minutes) % 1440 or 1440)

    anchor_today = reference_local.replace(
        hour=start_hour, minute=start_minute, second=0, microsecond=0
    )
    for anchor in (anchor_today - timedelta(days=1), anchor_today):
        end = anchor + duration
        if anchor <= reference_local <= end:
            return anchor, end

    next_anchor = anchor_today if anchor_today > reference_local else anchor_today + timedelta(days=1)
    return next_anchor, next_anchor + duration


def next_occurrence(time_str: str, tz_name: str) -> datetime:
    """Next local HH:MM from now, in the user's timezone - today if that
    time hasn't passed yet, otherwise tomorrow. Shared by the LLM tool
    handler (tools_registry.py) and the REST reminders route - moved here
    (rather than duplicated in both) since it's pure reminders-domain date
    math, not orchestrator-specific."""
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(timezone.utc).astimezone(tz)
    hour, minute = (int(part) for part in time_str.split(":"))
    candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now_local:
        candidate += timedelta(days=1)
    return candidate


def first_interval_occurrence(
    window_start: str, window_end: str, interval_minutes: int, tz_name: str
) -> datetime:
    """First fire time for a new interval reminder: if now falls inside the
    window (handled by window_instance above, not a naive hour comparison),
    starts one interval from now (not immediately); otherwise starts at the
    next window's start. Shared the same way as next_occurrence above."""
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(timezone.utc).astimezone(tz)
    anchor, end = window_instance(now_local, window_start, window_end)
    if anchor <= now_local <= end:
        return now_local + timedelta(minutes=interval_minutes)
    return anchor


async def create_recurring_reminder(
    session: AsyncSession,
    user_id: uuid.UUID,
    text: str,
    next_run_at: datetime,
    recurrence: str,
) -> ScheduledJob:
    job = ScheduledJob(
        user_id=user_id,
        job_type=JOB_TYPE,
        next_run_at=next_run_at,
        recurrence=recurrence,
        payload={"text": text},
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def create_interval_reminder(
    session: AsyncSession,
    user_id: uuid.UUID,
    text: str,
    next_run_at: datetime,
    interval_minutes: int,
    window_start: str,
    window_end: str,
) -> ScheduledJob:
    """Same job_type/handler as create_recurring_reminder - only the
    recurrence value ("interval") and payload shape differ. runner.py's
    _next_interval_run_at reads interval_minutes/window_start/window_end
    from payload to compute each next fire time."""
    job = ScheduledJob(
        user_id=user_id,
        job_type=JOB_TYPE,
        next_run_at=next_run_at,
        recurrence="interval",
        payload={
            "text": text,
            "interval_minutes": interval_minutes,
            "window_start": window_start,
            "window_end": window_end,
        },
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def list_recurring_reminders(
    session: AsyncSession, user_id: uuid.UUID
) -> list[ScheduledJob]:
    result = await session.execute(
        select(ScheduledJob)
        .where(
            ScheduledJob.user_id == user_id,
            ScheduledJob.job_type == JOB_TYPE,
            ScheduledJob.is_active.is_(True),
        )
        .order_by(ScheduledJob.next_run_at)
    )
    return list(result.scalars().all())


async def find_recurring_reminders(
    session: AsyncSession, user_id: uuid.UUID, query: str
) -> list[ScheduledJob]:
    """Word-set match on the reminder text (one side's whole words must all
    appear as whole words in the other) - lets the model refer to a
    reminder by paraphrased wording instead of needing the id up front.

    Deliberately NOT raw substring matching (what this replaced): "звонить"
    is a character-substring of "позвонить маме" but a different word, and
    the old two-way substring check matched it anyway - live testing found
    this would silently cancel an unrelated existing reminder ("Позвонить
    маме") as a side effect of creating a new, unrelated one ("звонить"),
    with no confirmation. Same bug class already fixed for task-title
    matching. A missed paraphrase now falls through to "no match" /
    "create a second reminder" instead of a wrong silent cancel - a
    duplicate reminder is a much smaller mistake than losing one.
    """
    reminders = await list_recurring_reminders(session, user_id)
    query_words = _words(query)
    if not query_words:
        return []
    return [
        r
        for r in reminders
        if query_words <= _words(r.payload.get("text", ""))
        or _words(r.payload.get("text", "")) <= query_words
    ]


async def mark_awaiting_reply(session: AsyncSession, user: User, text: str) -> None:
    """Called right after a custom_reminder push is sent. There's no
    dedicated state table for these (unlike tasks/focus_sessions), so the
    "a reply to this is expected next" flag lives directly on the user row -
    a single slot, not a queue.

    Uses an UPDATE by id rather than mutating `user` in place: the caller
    (scheduler/handlers.py) opens its own fresh session per handler, separate
    from the session that loaded `user` in runner.py's poller loop, so the
    passed-in `user` instance isn't tracked by `session` here and a plain
    attribute assignment would silently no-op on commit.

    Only claims the slot if it's currently empty. Two proactive jobs can
    land in the same ~60s poller tick for the same user (e.g. the seeded
    daily custom_reminder and the user's own reminder both landing on the
    same round hour) - an unconditional overwrite here let the second one
    silently clobber the first's pending context, so a reply to whichever
    message the user actually saw FIRST in chat got matched against the
    SECOND job's text instead. Leaving the first claim in place matches
    real chat order; the second message still sends normally, it just
    doesn't get automatic reply-detection for that one poller tick.
    """
    await session.execute(
        update(User)
        .where(User.id == user.id, User.pending_reminder_prompt.is_(None))
        .values(pending_reminder_prompt=text)
    )
    await session.commit()


async def pop_pending_reminder_prompt(session: AsyncSession, user: User) -> str | None:
    """Fire-once: returns and clears the pending prompt so it doesn't linger
    and get misapplied to a later, unrelated message - same pattern as
    clear_awaiting_confirmation for tasks."""
    text = user.pending_reminder_prompt
    if text is None:
        return None
    user.pending_reminder_prompt = None
    await session.commit()
    return text


async def cancel_recurring_reminder(
    session: AsyncSession, user_id: uuid.UUID, reminder_id: uuid.UUID
) -> bool:
    job = await session.get(ScheduledJob, reminder_id)
    if job is None or job.user_id != user_id or job.job_type != JOB_TYPE or not job.is_active:
        return False
    job.is_active = False
    await session.commit()
    return True
