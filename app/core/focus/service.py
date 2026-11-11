import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.focus_session import FocusSession, FocusSessionStatus
from app.database.models.scheduled_job import ScheduledJob

DEFAULT_DURATION_MIN = 90

# "planned" (offered, not yet started) and "awaiting_followup" (follow-up sent,
# no answer yet) are both open - only one session at a time should be in any
# of these states per user, since get_open_session/the orchestrator hint
# assume there's at most one to reason about.
OPEN_STATUSES = (
    FocusSessionStatus.PLANNED.value,
    FocusSessionStatus.ACTIVE.value,
    FocusSessionStatus.AWAITING_FOLLOWUP.value,
)


async def get_open_session(
    session: AsyncSession, user_id: uuid.UUID, for_update: bool = False
) -> FocusSession | None:
    stmt = (
        select(FocusSession)
        .where(FocusSession.user_id == user_id, FocusSession.status.in_(OPEN_STATUSES))
        .order_by(FocusSession.created_at.desc())
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return result.scalars().first()


async def start_session(
    session: AsyncSession,
    user_id: uuid.UUID,
    task_id: uuid.UUID | None = None,
    planned_duration_min: int = DEFAULT_DURATION_MIN,
) -> FocusSession:
    """Starts the user's planned session if one exists, else starts an ad-hoc one.

    Also schedules the focus_followup job - session start and the follow-up
    trigger are one atomic step from the caller's point of view.
    """
    # Locked for the same reason complete_session/abandon_session already
    # lock this read (see their own comments) - two near-simultaneous calls
    # (double-tap before the button disables, or a redelivered Telegram
    # update) could otherwise both see "no open session" and both insert
    # one, leaving an orphaned first row that nothing ever closes.
    focus_session = await get_open_session(session, user_id, for_update=True)
    if focus_session is not None and focus_session.status != FocusSessionStatus.PLANNED.value:
        return focus_session

    if focus_session is None:
        focus_session = FocusSession(user_id=user_id, task_id=task_id)
        session.add(focus_session)

    focus_session.status = FocusSessionStatus.ACTIVE.value
    focus_session.planned_duration_min = planned_duration_min
    focus_session.started_at = datetime.now(timezone.utc)
    await session.flush()

    session.add(
        ScheduledJob(
            user_id=user_id,
            job_type="focus_followup",
            next_run_at=focus_session.started_at + timedelta(minutes=planned_duration_min),
            payload={"session_id": str(focus_session.id)},
        )
    )
    await session.commit()
    await session.refresh(focus_session)
    return focus_session


async def mark_awaiting_followup(session: AsyncSession, focus_session: FocusSession) -> None:
    focus_session.status = FocusSessionStatus.AWAITING_FOLLOWUP.value
    await session.commit()


async def complete_session(
    session: AsyncSession, user_id: uuid.UUID, outcome_note: str
) -> FocusSession | None:
    # Locked: without it, two concurrent calls (double-send/redelivery, same
    # root cause as maybe_summarize's lock in app/core/memory/service.py) can
    # both read the same open session and both commit a terminal status/
    # ended_at, the second silently overwriting the first's outcome_note.
    focus_session = await get_open_session(session, user_id, for_update=True)
    if focus_session is None or focus_session.status == FocusSessionStatus.PLANNED.value:
        return None

    focus_session.status = FocusSessionStatus.COMPLETED.value
    focus_session.outcome_note = outcome_note
    focus_session.ended_at = datetime.now(timezone.utc)
    await _cancel_followup_job(session, focus_session.id)
    await session.commit()
    await session.refresh(focus_session)
    return focus_session


async def abandon_session(
    session: AsyncSession, user_id: uuid.UUID, interruption_reason: str
) -> FocusSession | None:
    # Unlike complete_session, PLANNED is a valid starting point here - a
    # proposed-but-never-started session can be declined/retired, it just
    # can't be "completed" (nothing to report a result on). Without this,
    # an ignored PLANNED session had no way to ever leave OPEN_STATUSES and
    # would resurface every morning check-in indefinitely - see the "висит
    # с 18 июля" bug in docs/ARCHITECTURE.md.
    focus_session = await get_open_session(session, user_id, for_update=True)
    if focus_session is None:
        return None

    focus_session.status = FocusSessionStatus.ABANDONED.value
    focus_session.interruption_reason = interruption_reason
    focus_session.ended_at = datetime.now(timezone.utc)
    await _cancel_followup_job(session, focus_session.id)
    await session.commit()
    await session.refresh(focus_session)
    return focus_session


async def list_sessions(session: AsyncSession, user_id: uuid.UUID) -> list[FocusSession]:
    result = await session.execute(
        select(FocusSession)
        .where(FocusSession.user_id == user_id)
        .order_by(FocusSession.created_at.desc())
    )
    return list(result.scalars().all())


async def _cancel_followup_job(session: AsyncSession, focus_session_id: uuid.UUID) -> None:
    result = await session.execute(
        select(ScheduledJob).where(
            ScheduledJob.job_type == "focus_followup",
            ScheduledJob.is_active.is_(True),
            ScheduledJob.payload["session_id"].astext == str(focus_session_id),
        )
    )
    for job in result.scalars().all():
        job.is_active = False
