import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.focus_session import FocusSession, FocusSessionStatus

TERMINAL_STATUSES = (FocusSessionStatus.COMPLETED.value, FocusSessionStatus.ABANDONED.value)
MIN_SESSIONS_FOR_COACHING = 3


@dataclass
class FocusStats:
    completed: int
    abandoned: int
    interruption_reasons: list[str] = field(default_factory=list)
    outcome_notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.completed + self.abandoned

    @property
    def completion_rate(self) -> float | None:
        return self.completed / self.total if self.total else None


async def get_focus_stats(
    session: AsyncSession, user_id: uuid.UUID, since: datetime
) -> FocusStats:
    result = await session.execute(
        select(FocusSession).where(
            FocusSession.user_id == user_id,
            FocusSession.status.in_(TERMINAL_STATUSES),
            FocusSession.created_at >= since,
        )
    )
    sessions = list(result.scalars().all())

    completed = [s for s in sessions if s.status == FocusSessionStatus.COMPLETED.value]
    abandoned = [s for s in sessions if s.status == FocusSessionStatus.ABANDONED.value]

    return FocusStats(
        completed=len(completed),
        abandoned=len(abandoned),
        interruption_reasons=[s.interruption_reason for s in abandoned if s.interruption_reason],
        outcome_notes=[s.outcome_note for s in completed if s.outcome_note],
    )


def format_stats_summary(stats: FocusStats) -> str:
    if stats.total == 0:
        return "За этот период фокус-сессий не было."

    rate_pct = round(stats.completion_rate * 100)
    lines = [
        f"Всего фокус-сессий: {stats.total} (завершено: {stats.completed}, "
        f"сорвано: {stats.abandoned}, доля завершения: {rate_pct}%)."
    ]
    if stats.interruption_reasons:
        reasons = "; ".join(stats.interruption_reasons)
        lines.append(f"Причины срывов: {reasons}")
    if stats.outcome_notes:
        notes = "; ".join(stats.outcome_notes)
        lines.append(f"Что удавалось сделать: {notes}")
    return "\n".join(lines)


def format_coach_hint(stats: FocusStats) -> str | None:
    """A terse, decision-support-oriented summary for the exact moment a
    session is being proposed/started - not a report, just enough grounding
    for the model to offer encouragement based on the user's own history
    instead of generic motivation. None if there isn't enough history yet
    to say anything meaningful.
    """
    if stats.total < MIN_SESSIONS_FOR_COACHING:
        return None

    rate_pct = round(stats.completion_rate * 100)
    hint = f"За последнее время доля завершённых фокус-сессий: {rate_pct}%."
    if stats.interruption_reasons:
        top_reason, _ = Counter(stats.interruption_reasons).most_common(1)[0]
        hint += f" Самая частая причина срывов: «{top_reason}»."
    return hint
