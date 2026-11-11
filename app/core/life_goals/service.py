import calendar
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.life_goal import LifeGoal

# Sentinel horizon_months value for "На неделю" - not a real month count, but
# reusing the existing horizon_months slot avoids a second discriminator
# column for what's still fundamentally "which relative bucket is this
# goal in".
WEEK_HORIZON = 0

# horizon_months >= this many months anchors to a real calendar year
# (confirmed with the user: "цель на год" means the current calendar year,
# Jan 1 - Dec 31, not a rolling 12-month window from creation date).
YEAR_SCALE_MONTHS = 12


def _end_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _months_after(year: int, month: int, months_ahead: int) -> tuple[int, int]:
    total = (year * 12 + (month - 1)) + months_ahead
    return total // 12, total % 12 + 1


def target_date_for_horizon(
    horizon_months: int | None, explicit_target_date: date | None, today: date | None = None
) -> date | None:
    """Computes the calendar deadline a goal's horizon implies.

    An explicit target_date (user picked a specific month, e.g. "Сентябрь
    2026") always wins. Otherwise: WEEK_HORIZON anchors to the end of the
    current Mon-Sun calendar week; a short relative horizon (1/3/6 months)
    anchors to the end of the Nth calendar month counting the current
    month as month 1 ("1 месяц" created any time in August targets
    2026-08-31, not "30 days from today"); a year-scale horizon (12/36/60/
    ... months) anchors to Dec 31 of the Nth calendar year counting the
    current year as year 1, same reasoning. The lifetime bucket (None) has
    no calendar anchor.
    """
    if explicit_target_date is not None:
        return explicit_target_date
    today = today or date.today()
    if horizon_months == WEEK_HORIZON:
        return today + timedelta(days=6 - today.weekday())  # weekday(): Mon=0 .. Sun=6
    if horizon_months is not None and horizon_months >= YEAR_SCALE_MONTHS:
        years = horizon_months // 12
        return date(today.year + years - 1, 12, 31)
    if horizon_months is not None:
        year, month = _months_after(today.year, today.month, horizon_months - 1)
        return _end_of_month(year, month)
    return None


def next_target_date(horizon_months: int | None, current_target: date) -> date | None:
    """The next period's deadline once the current one has passed, for
    carrying a not-yet-done relative-horizon goal forward instead of
    letting it just sit expired - see carry_forward_life_goals. Only
    relative horizons (week/N-months/N-years) recur this way; a
    specific-month pick (horizon_months is None) is a one-off deadline the
    user chose explicitly, not something to auto-extend.
    """
    if horizon_months is None:
        return None
    if horizon_months == WEEK_HORIZON:
        return current_target + timedelta(days=7)
    if horizon_months >= YEAR_SCALE_MONTHS:
        return date(current_target.year + horizon_months // 12, 12, 31)
    year, month = _months_after(current_target.year, current_target.month, horizon_months)
    return _end_of_month(year, month)


async def save_goal(
    session: AsyncSession,
    user_id: uuid.UUID,
    horizon_months: int | None,
    title: str,
    description: str | None = None,
    target_date: date | None = None,
) -> LifeGoal:
    goal = LifeGoal(
        user_id=user_id,
        horizon_months=horizon_months,
        title=title,
        description=description,
        target_date=target_date_for_horizon(horizon_months, target_date),
    )
    session.add(goal)
    await session.commit()
    await session.refresh(goal)
    return goal


async def update_goal(
    session: AsyncSession,
    user_id: uuid.UUID,
    goal_id: uuid.UUID,
    horizon_months: int | None = ...,
    title: str | None = None,
    description: str | None = ...,
    target_date: date | None = ...,
) -> LifeGoal | None:
    """Edits an existing goal in place - lets the user change the horizon
    later (e.g. a "1 месяц" goal that turns out to need "полгода") instead
    of deleting and recreating it. horizon_months/description/target_date
    use Ellipsis as the "not provided" sentinel (unlike title) since None
    is itself a meaningful value for each (None horizon = "Задачи на
    жизнь", None description = no description, None target_date = clear a
    manually-picked month) and must be settable, not just skippable.
    """
    goal = await session.get(LifeGoal, goal_id)
    if goal is None or goal.user_id != user_id:
        return None
    if title is not None:
        goal.title = title
    if description is not ...:
        goal.description = description
    # Recompute the calendar anchor whenever either input to it changes -
    # not just on an explicit target_date, so switching horizon_months
    # from "1 месяц" to "1 год" also picks up the calendar-year anchor
    # without a separate manual step.
    if horizon_months is not ... or target_date is not ...:
        new_horizon = horizon_months if horizon_months is not ... else goal.horizon_months
        # A target_date the caller just cleared (explicitly passed None)
        # falls back to the auto-computed anchor rather than staying
        # cleared - only a real date value counts as "explicit" input to
        # target_date_for_horizon.
        explicit = target_date if target_date not in (..., None) else None
        goal.horizon_months = new_horizon
        goal.target_date = target_date_for_horizon(new_horizon, explicit)
        # carry_forward_life_goals sets this True but nothing ever cleared
        # it - a goal auto-rolled forward once stayed permanently grouped
        # under "Перенесённые" in the Mini App even after being
        # consciously re-scheduled here. A deliberate edit to the horizon/
        # deadline is a fresh commitment to the new period.
        goal.carried_over = False
    await session.commit()
    await session.refresh(goal)
    return goal


async def carry_forward_life_goals(session: AsyncSession, user_id: uuid.UUID) -> list[LifeGoal]:
    """Rolls every not-done, relative-horizon goal whose target_date has
    passed onto its next period (see next_target_date) instead of leaving
    it expired with nothing pointing at it - same "default to keeping it
    visible, not silently dropping it" reasoning as
    app.core.tasks.service.carry_forward_overdue_tasks. Returns the goals
    that were actually moved, for the caller to inform the user about
    (see app/scheduler/handlers.py) - real request from a user 2026-08-12:
    "как истекает месяц - спросить, переносить ли эти цели или они
    выполнены". Marks carried_over=True so the Mini App can group these
    under "Перенесённые с прошлого месяца" instead of blending them back
    into a fresh-looking bucket - see frontend/src/LifeGoals.tsx.
    """
    result = await session.execute(
        select(LifeGoal).where(
            LifeGoal.user_id == user_id,
            LifeGoal.is_done.is_(False),
            LifeGoal.horizon_months.is_not(None),
            LifeGoal.target_date.is_not(None),
        )
    )
    today = date.today()
    moved = []
    for goal in result.scalars().all():
        if goal.target_date >= today:
            continue
        new_target = next_target_date(goal.horizon_months, goal.target_date)
        if new_target is None:
            continue
        goal.target_date = new_target
        goal.carried_over = True
        moved.append(goal)
    if moved:
        await session.commit()
    return moved


async def set_goal_done(
    session: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID, done: bool
) -> LifeGoal | None:
    goal = await session.get(LifeGoal, goal_id)
    if goal is None or goal.user_id != user_id:
        return None
    goal.is_done = done
    if done:
        # A clean close-out shouldn't keep showing under "Перенесённые"
        # (implies still open and overdue) once it's actually finished -
        # same reasoning as the identical reset in update_goal above.
        goal.carried_over = False
    await session.commit()
    await session.refresh(goal)
    return goal


async def list_goals(session: AsyncSession, user_id: uuid.UUID) -> list[LifeGoal]:
    """Ascending by horizon (1-month goals before 30-year ones), then by
    creation order within the same horizon. NULL horizon ("Задачи на
    жизнь") sorts last - it's not an earlier or nearer-term goal, it's the
    open-ended bucket that belongs after every dated one."""
    result = await session.execute(
        select(LifeGoal)
        .where(LifeGoal.user_id == user_id)
        .order_by(LifeGoal.horizon_months.asc().nulls_last(), LifeGoal.created_at.asc())
    )
    return list(result.scalars().all())


async def _resolve_goal_by_title(
    session: AsyncSession, user_id: uuid.UUID, title: str, is_done: bool
) -> tuple[LifeGoal | None, list[LifeGoal]]:
    result = await session.execute(
        select(LifeGoal).where(LifeGoal.user_id == user_id, LifeGoal.is_done.is_(is_done))
    )
    goals = result.scalars().all()
    needle = title.lower()
    matches = [g for g in goals if needle in g.title.lower() or g.title.lower() in needle]
    if len(matches) == 1:
        return matches[0], []
    if len(matches) > 1:
        return None, matches
    return None, []


async def resolve_goal_by_title(
    session: AsyncSession, user_id: uuid.UUID, title: str
) -> tuple[LifeGoal | None, list[LifeGoal]]:
    """Same two-way-substring matching as tasks._resolve_pending_task (the
    model paraphrasing a goal's title should still resolve it), scoped to
    the LLM's update_life_goal/complete_life_goal tools - there was no
    voice/chat path to edit or complete an existing goal at all before
    this (only create/list), the same category of gap already fixed once
    for save_life_goal's horizon options. Only not-yet-done goals are
    searched - "отметь цель выполненной" naming an already-done goal isn't
    a real use case worth the ambiguity of matching against a much larger
    pool including finished goals.
    """
    return await _resolve_goal_by_title(session, user_id, title, is_done=False)


async def resolve_done_goal_by_title(
    session: AsyncSession, user_id: uuid.UUID, title: str
) -> tuple[LifeGoal | None, list[LifeGoal]]:
    """Same as resolve_goal_by_title but for undoing a completion
    ("верни цель в активные") - searches already-done goals instead."""
    return await _resolve_goal_by_title(session, user_id, title, is_done=True)


def format_horizon(months: int | None) -> str:
    if months is None:
        return "Задачи на жизнь"
    if months == WEEK_HORIZON:
        return "На неделю"
    if months < 12:
        return f"На {months} {month_word(months)}" if months != 6 else "На полгода"
    years = months // 12
    return f"На {years} {year_word(years)}"


def format_goals(goals: list[LifeGoal]) -> str:
    # Already-completed goals would just be clutter in a spoken/chat
    # listing - the Mini App is where finished goals stay visible
    # (struck through) for the record, this is the "what's still open"
    # view the model reads from.
    open_goals = [g for g in goals if not g.is_done]
    if not open_goals:
        return "Целей пока нет."

    lines = []
    current_horizon: int | None = -1  # sentinel distinct from any real value, including None
    for g in open_goals:
        if g.horizon_months != current_horizon:
            current_horizon = g.horizon_months
            lines.append(f"{format_horizon(current_horizon)}:")
        line = f"- {g.title}"
        if g.description:
            line += f" ({g.description})"
        lines.append(line)
    return "\n".join(lines)


def month_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "месяц"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "месяца"
    return "месяцев"


def year_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "год"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "года"
    return "лет"
