import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.integrations import service as integrations_service
from app.database.models.scheduled_job import ScheduledJob
from app.database.models.task import Task, TaskStatus
from app.integrations.google_oauth import GoogleAPIError

logger = logging.getLogger(__name__)

# How long a just-created pending task stays eligible to be auto-merged
# into by a later create_task call with a matching title, instead of
# becoming a literal duplicate - see create_task's docstring.
_DUPLICATE_MERGE_WINDOW = timedelta(hours=2)

# Tasks don't have an explicit end time - a fixed block keeps every synced
# event non-zero-length without asking the user to specify a duration they
# were never asked for anywhere else in the product.
_CALENDAR_EVENT_DURATION = timedelta(minutes=30)


async def _sync_calendar_event(session: AsyncSession, user_id: uuid.UUID, task: Task) -> None:
    """Auto-syncs a task to Google Calendar, deterministically - not an
    LLM decision (see create_calendar_event for the opt-in, explicit-request
    tool). Every task with a due_at/remind_at gets a linked event if the
    user has Google connected; the link (google_event_id) is what makes a
    second call update the same event instead of creating a duplicate, and
    a task that loses its date/gets completed/deleted gets its event
    removed instead of left stale.

    Best-effort by design: any failure here (not connected, token expired,
    transient API error) is logged and swallowed - a calendar sync problem
    must never block the task operation the user actually asked for.
    """
    moment = task.due_at or task.remind_at
    should_have_event = moment is not None and task.status == TaskStatus.PENDING.value

    try:
        if not should_have_event:
            if task.google_event_id:
                await integrations_service.delete_calendar_event(
                    session, user_id, task.google_event_id
                )
                task.google_event_id = None
                await session.commit()
            return

        if not await integrations_service.is_connected(session, user_id):
            return

        end = moment + _CALENDAR_EVENT_DURATION
        if task.google_event_id:
            try:
                await integrations_service.update_calendar_event(
                    session, user_id, task.google_event_id, task.title, moment, end
                )
                return
            except GoogleAPIError:
                # Event may have been deleted on the Google side (e.g. by
                # hand) - fall through and re-create rather than leaving
                # the task unsynced.
                task.google_event_id = None

        event = await integrations_service.create_calendar_event(
            session, user_id, task.title, moment, end
        )
        task.google_event_id = event.get("id")
        await session.commit()
    except integrations_service.NotConnectedError:
        return
    except Exception:
        logger.exception("calendar sync failed for task %s", task.id)


async def _find_recent_duplicate(
    session: AsyncSession, user_id: uuid.UUID, title: str
) -> Task | None:
    cutoff = datetime.now(timezone.utc) - _DUPLICATE_MERGE_WINDOW
    result = await session.execute(
        select(Task).where(
            Task.user_id == user_id,
            Task.status == TaskStatus.PENDING.value,
            Task.created_at >= cutoff,
        )
    )
    needle = title.lower()
    matches = [
        t
        for t in result.scalars().all()
        if needle in t.title.lower() or t.title.lower() in needle
    ]
    # Ambiguous (2+) is left to a fresh create rather than guessing which
    # one the user meant to extend - only an unambiguous single match is
    # safe to auto-merge into.
    return matches[0] if len(matches) == 1 else None


async def create_task(
    session: AsyncSession,
    user_id: uuid.UUID,
    title: str,
    description: str | None = None,
    due_at: datetime | None = None,
    project_id: uuid.UUID | None = None,
    remind_at: datetime | None = None,
) -> tuple[Task, bool]:
    """Creates a task - or, if the user (via the model) already has a
    matching-titled pending task from within the last couple hours, folds
    the new details into that one instead. Returns (task, was_created) so
    the caller can phrase the confirmation as a create vs. an update.

    This closes a gap prompt instructions alone didn't: "напомни про X"
    followed minutes later by "а ещё в 9 утра" should update the existing
    task, and update_task exists and is documented for exactly this - but
    the model kept calling create_task again anyway, producing real
    duplicate tasks live (the "Ответ от Т-Банка"/"Задача по Алимберг"
    incidents). Merging at the data layer doesn't depend on the model
    choosing the right tool.
    """
    existing = await _find_recent_duplicate(session, user_id, title)
    if existing is not None:
        if due_at is not None:
            existing.due_at = due_at
        if description is not None:
            existing.description = description
        if project_id is not None:
            existing.project_id = project_id
        if remind_at is not None:
            await _deactivate_task_reminder(session, existing.id)
            existing.remind_at = remind_at
            session.add(
                ScheduledJob(
                    user_id=user_id,
                    job_type="task_reminder",
                    next_run_at=remind_at,
                    payload={"task_id": str(existing.id)},
                )
            )
        await session.commit()
        await session.refresh(existing)
        await _sync_calendar_event(session, user_id, existing)
        # _sync_calendar_event may have committed again internally (new/
        # updated google_event_id) - that expires every attribute on
        # `existing` (SQLAlchemy's expire_on_commit default), and the route
        # layer serializes this object synchronously right after returning,
        # with no async context left to lazy-load them. Re-refresh so the
        # object handed back is never in an expired state.
        await session.refresh(existing)
        return existing, False

    task = Task(
        user_id=user_id,
        title=title,
        description=description,
        due_at=due_at,
        project_id=project_id,
        remind_at=remind_at,
    )
    session.add(task)
    await session.flush()

    if remind_at is not None:
        # One-time job (recurrence=None) - the scheduler deactivates it after
        # it fires once, see app/scheduler/runner.py.
        session.add(
            ScheduledJob(
                user_id=user_id,
                job_type="task_reminder",
                next_run_at=remind_at,
                payload={"task_id": str(task.id)},
            )
        )

    await session.commit()
    await session.refresh(task)
    await _sync_calendar_event(session, user_id, task)
    # See the matching comment in the merge branch above - _sync_calendar_event
    # can commit again internally and expire every attribute on `task`.
    await session.refresh(task)
    return task, True


async def list_tasks(session: AsyncSession, user_id: uuid.UUID) -> list[Task]:
    result = await session.execute(
        select(Task).where(Task.user_id == user_id).order_by(Task.created_at.desc())
    )
    return list(result.scalars().all())


async def list_tasks_for_date(
    session: AsyncSession, user_id: uuid.UUID, target_date: date, tz_name: str
) -> list[Task]:
    """Tasks whose due_at/remind_at falls on target_date (user-local date).

    Server-side date filtering instead of the model eyeballing dates in a
    full list dump - "какие у меня задачи на завтра" reproduced live
    returning tasks from four different days (24.07 through 27.07) because
    nothing filters list_tasks/plan_day to a single day; same underlying
    unreliability complete_tasks_due_today fixes for bulk completion.
    """
    tz = ZoneInfo(tz_name)
    result = await session.execute(
        select(Task).where(Task.user_id == user_id, Task.status != TaskStatus.CANCELLED.value)
    )
    matches = [
        t
        for t in result.scalars().all()
        if (t.remind_at or t.due_at) and (t.remind_at or t.due_at).astimezone(tz).date() == target_date
    ]
    # Chronological, like a real day schedule - every task here is
    # guaranteed to have remind_at or due_at set (the filter above requires
    # it), so this is a total order, not a "undated tasks first/last" tiebreak.
    matches.sort(key=lambda t: t.remind_at or t.due_at)
    return matches


def format_task_list(tasks: list[Task], tz_name: str) -> str:
    """Single canonical formatter, shared by the bot's direct "Мои задачи"
    display and the plan_day/list_tasks LLM tools - they used to have
    separate, drifting formatting logic, which is why time didn't reliably
    show up either way. due_at/remind_at are stored in UTC; converting to
    the user's own timezone here was missing before (times displayed were
    UTC, not local).

    Done tasks are excluded, same as cancelled - "Мои задачи" is meant to
    read as a current to-do list, not a growing archive of everything ever
    finished (done tasks never expire out of the DB on their own).
    """
    visible = [
        t
        for t in tasks
        if t.status not in (TaskStatus.CANCELLED.value, TaskStatus.DONE.value)
    ]
    if not visible:
        return "Задач нет."

    tz = ZoneInfo(tz_name)
    today = datetime.now(timezone.utc).astimezone(tz).date()
    lines = []
    for t in visible:
        line = f"- {t.title}"
        # remind_at is when the alarm fires, due_at is the deadline - show
        # whichever is set, remind_at first since it's the more concrete one.
        moment = t.remind_at or t.due_at
        if moment:
            local = moment.astimezone(tz)
            # A list can span multiple days (list_tasks has no date filter) -
            # showing only HH:MM made same-titled or same-time tasks from
            # different days indistinguishable, both to the reader and to
            # the model relaying this text, e.g. a done task from today
            # getting relayed as part of "tomorrow's list".
            if local.date() == today:
                line += f" ({local:%H:%M})"
            else:
                line += f" ({local:%d.%m %H:%M})"
        lines.append(line)
    return "\n".join(lines)


async def get_today_overview(
    session: AsyncSession, user_id: uuid.UUID, tz_name: str
) -> tuple[list[Task], list[Task]]:
    """Returns (actual, completed) for "Задачи на сегодня": actual is still-
    pending tasks due/reminded today, completed is tasks marked done today
    (by completion time, i.e. updated_at - there's no separate completed_at
    column, and nothing currently touches a DONE task's updated_at again
    after complete_task/complete_tasks_due_today set it, so it's a safe
    proxy). completed intentionally isn't limited to tasks that were due
    today - finishing an undated task today still belongs in "what I got
    done today".
    """
    tz = ZoneInfo(tz_name)
    today = datetime.now(timezone.utc).astimezone(tz).date()

    pending_today = await list_tasks_for_date(session, user_id, today, tz_name)
    actual = [t for t in pending_today if t.status == TaskStatus.PENDING.value]

    result = await session.execute(
        select(Task).where(Task.user_id == user_id, Task.status == TaskStatus.DONE.value)
    )
    completed = [
        t for t in result.scalars().all() if t.updated_at.astimezone(tz).date() == today
    ]
    return actual, completed


def format_today_overview(actual: list[Task], completed: list[Task], tz_name: str) -> str:
    tz = ZoneInfo(tz_name)
    lines = ["Актуальные задачи:"]
    if actual:
        for t in actual:
            moment = t.remind_at or t.due_at
            time_note = f" ({moment.astimezone(tz):%H:%M})" if moment else ""
            lines.append(f"- {t.title}{time_note}")
    else:
        lines.append("- нет")

    lines.append("")
    lines.append("Выполненные задачи:")
    if completed:
        # Real confusion, 2026-08-28: a user reading a bare "- Забрать
        # трафарет" here assumed it meant "just done, right now" and
        # reported it as stale/wrong data when the task had actually been
        # completed that same morning, just earlier - showing the actual
        # completion time removes the ambiguity either way.
        for t in completed:
            lines.append(f"- {t.title} ({t.updated_at.astimezone(tz):%H:%M})")
    else:
        lines.append("- нет")

    return "\n".join(lines)


async def get_overdue_pending_tasks(
    session: AsyncSession, user_id: uuid.UUID, tz_name: str
) -> list[Task]:
    """Pending tasks due today (local) that haven't fired past this moment
    yet - the evening_review job's input. remind_at-only tasks aren't
    included: those already get their own task_reminder check the moment
    they fire, and a task with neither moment set has no "overdue" to
    speak of.

    Deliberately bounded to *today's* due_at, not "any due_at < now" - an
    unbounded query pulled in every never-confirmed task from any past day
    (weeks of backlog) into one message every single evening, since a task
    that's swept in but not explicitly marked done just sits with its old
    due_at forever and gets re-swept the next day too. Real incident
    2026-08-09: a user's evening review dumped ~18 unrelated backlog items
    instead of the 2-3 actually due that day.

    Also excludes tasks already awaiting_confirmation=True - real incident
    2026-08-29: carry_forward_overdue_tasks (added to keep a stale task
    visible in "Сегодня" instead of silently vanishing) rolls an unfinished
    task's due_at onto today every midnight, which made it look "due
    today" every single day and re-enter THIS query every single evening
    forever, reintroducing the exact nightly-nag pattern the *bounded*
    query above was built to fix, just via a different mechanism. Once a
    task has been asked about here and never got a reply/action, asking
    again every night without the user having done anything about it is
    pure noise - it stays visible via the carry-forward (Сегодня tab,
    "Все"), it just stops re-pushing. The flag only clears on a genuine
    user response (see clear_awaiting_confirmation) or an explicit edit
    (update_task), so a real fresh due date the user sets themselves is
    unaffected - only the same never-answered row repeating is suppressed.
    """
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(timezone.utc).astimezone(tz)
    start_of_today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    result = await session.execute(
        select(Task).where(
            Task.user_id == user_id,
            Task.status == TaskStatus.PENDING.value,
            Task.due_at.is_not(None),
            Task.due_at >= start_of_today_local,
            Task.due_at < now_local,
            Task.awaiting_confirmation.is_(False),
        )
    )
    return list(result.scalars().all())


async def carry_forward_overdue_tasks(
    session: AsyncSession, user_id: uuid.UUID, tz_name: str
) -> int:
    """Rolls every still-pending task whose due_at fell on a past local day
    forward onto today (same time-of-day), so it stays visible under
    "Сегодня" in the Mini App instead of silently falling out of that view
    the moment the calendar date turns over - real incident 2026-08-13: a
    task due late the previous evening was never marked done, and simply
    vanished from "Сегодня" at midnight with nothing else pointing at it
    (it was still sitting in the DB, just no longer date-matched by any
    view except the undifferentiated "Все" tab).

    Deliberately does NOT touch remind_at-only tasks - a reminder is a
    one-time nudge, not a due date, and already has its own fire-once
    scheduled_job; there's no "carrying forward" that makes sense for it.
    Called once daily per user by the carry_forward_overdue scheduled job
    (see app/scheduler/handlers.py) - not by get_overdue_pending_tasks/
    evening_review, which stays scoped to *today's* due_at so it doesn't
    re-inherit the unbounded-backlog-dump bug this was already fixed for
    once (see that function's own docstring). Tasks this function moves
    become genuinely due today, so evening_review picking them up that
    same evening if still undone is correct, not a regression.
    """
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(timezone.utc).astimezone(tz)
    start_of_today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    result = await session.execute(
        select(Task).where(
            Task.user_id == user_id,
            Task.status == TaskStatus.PENDING.value,
            Task.due_at.is_not(None),
            Task.due_at < start_of_today_local,
        )
    )
    stale = list(result.scalars().all())
    if not stale:
        return 0

    for task in stale:
        old_local = task.due_at.astimezone(tz)
        days_stale = (start_of_today_local.date() - old_local.date()).days
        task.due_at = task.due_at + timedelta(days=days_stale)
        await _sync_calendar_event(session, user_id, task)
    await session.commit()
    return len(stale)


async def mark_awaiting_confirmation(session: AsyncSession, task: Task) -> None:
    task.awaiting_confirmation = True
    await session.commit()


async def get_awaiting_confirmation_tasks(
    session: AsyncSession, user_id: uuid.UUID
) -> list[Task]:
    """Returns every task currently awaiting a completion check - usually
    one (a single task_reminder just fired), but the evening_review job
    marks several at once, so callers must handle both.
    """
    result = await session.execute(
        select(Task).where(
            Task.user_id == user_id,
            Task.awaiting_confirmation.is_(True),
            Task.status == TaskStatus.PENDING.value,
        )
    )
    return list(result.scalars().all())


async def clear_awaiting_confirmation(session: AsyncSession, task: Task) -> None:
    task.awaiting_confirmation = False
    await session.commit()


def _same_slot(a: Task, b: Task) -> bool:
    return a.title == b.title and a.due_at == b.due_at and a.remind_at == b.remind_at


async def _resolve_pending_task(
    session: AsyncSession,
    user_id: uuid.UUID,
    task_id: uuid.UUID | None,
    title: str | None,
) -> tuple[list[Task], list[Task]]:
    """Returns (resolved, candidates). resolved is non-empty on an
    unambiguous match, OR when every match is an indistinguishable
    duplicate (identical title/due_at/remind_at) - in that case ALL of them
    are resolved, since asking the user to pick between literally identical
    rows gives them no real information to act on: a real incident had a
    user answer a disambiguation prompt with "все"/"обе"/"закрой их" twice
    in a row, and the model just repeated the same question verbatim both
    times instead of making progress. Real duplicates come from an
    already-known duplicate-task-creation bug elsewhere in this project's
    history, not from the user actually meaning two different things.

    candidates is non-empty only when genuinely ambiguous (2+ matches that
    differ from each other) - see the "Написать сообщение" duplicate-title
    bug this replaced, where exact-match .first() picked the wrong one of
    two. Shared by complete_task/update_task/delete_task.
    """
    if task_id is not None:
        task = await session.get(Task, task_id)
        if task is not None and task.user_id != user_id:
            task = None
        # The title branch below only ever considers PENDING tasks - this
        # branch didn't, even though every caller (complete_task/
        # update_task/delete_task/mark_priority_tasks, and every REST route
        # in api/routes/tasks.py always resolves by id) means the same
        # invariant: you can't complete/edit/re-cancel/prioritize a task
        # that isn't pending anymore. Without this, e.g. a stale double-tap
        # in the Mini App (task already completed in one tab) could set
        # is_priority=True on a DONE task via /tasks/priority, or PATCH
        # could silently revive a CANCELLED task's due date.
        if task is not None and task.status != TaskStatus.PENDING.value:
            task = None
        if task is None:
            return [], []
        return [task], []
    if title:
        result = await session.execute(
            select(Task).where(
                Task.user_id == user_id,
                Task.status == TaskStatus.PENDING.value,
            )
        )
        pending = result.scalars().all()
        # Two-way substring match: handles the LLM paraphrasing the title
        # (e.g. user says "написать сообщение в час ночи" for a task titled
        # "Написать сообщение") in either direction.
        needle = title.lower()
        matches = [
            t for t in pending if needle in t.title.lower() or t.title.lower() in needle
        ]
        if len(matches) == 1:
            return matches, []
        if len(matches) > 1 and all(_same_slot(matches[0], m) for m in matches[1:]):
            return matches, []
        return [], matches
    return [], []


async def complete_task(
    session: AsyncSession,
    user_id: uuid.UUID,
    task_id: uuid.UUID | None = None,
    title: str | None = None,
) -> tuple[Task | None, list[Task]]:
    resolved, candidates = await _resolve_pending_task(session, user_id, task_id, title)
    if not resolved:
        return None, candidates

    for task in resolved:
        task.status = TaskStatus.DONE.value
        task.awaiting_confirmation = False
        # clear_today_priority (called each morning) only looks at
        # is_priority, not status - a DONE task left with a stale True from
        # earlier the same day would get its is_priority reset the NEXT
        # morning instead, and that reset is a real UPDATE that bumps
        # updated_at, which doubles as "completed at" for done tasks
        # throughout this codebase. Clearing it here, at the one place a
        # task actually stops being "today's priority", keeps that
        # timestamp meaning what it's supposed to.
        task.is_priority = False
    await session.commit()
    for task in resolved:
        await session.refresh(task)
        await _sync_calendar_event(session, user_id, task)
        # _sync_calendar_event's own internal commit expires task's
        # attributes again - re-refresh before this is handed back for
        # response serialization outside any async-safe lazy-load context.
        await session.refresh(task)
    return resolved[0], []


async def complete_tasks_due_today(
    session: AsyncSession, user_id: uuid.UUID, tz_name: str
) -> list[Task]:
    """Bulk-completes every pending task whose due_at/remind_at falls on
    today (user-local date) - undated tasks are left untouched, same as
    the "Без даты" bucket the Mini App keeps separate from dated ones.

    Exists because the model doing this itself (enumerating today's tasks
    from memory/history, then calling complete_task once per title) proved
    unreliable in practice: it completed tomorrow's tasks too and missed
    one of today's, in the same live incident - it has no reliable way to
    tell "today" apart from "tomorrow" by eyeballing a chat transcript.
    """
    tz = ZoneInfo(tz_name)
    today = datetime.now(timezone.utc).astimezone(tz).date()
    result = await session.execute(
        select(Task).where(Task.user_id == user_id, Task.status == TaskStatus.PENDING.value)
    )
    due_today = [
        t
        for t in result.scalars().all()
        if (t.remind_at or t.due_at) and (t.remind_at or t.due_at).astimezone(tz).date() == today
    ]
    for t in due_today:
        t.status = TaskStatus.DONE.value
        t.awaiting_confirmation = False
        t.is_priority = False
    await session.commit()
    for t in due_today:
        await session.refresh(t)
        await _sync_calendar_event(session, user_id, t)
        await session.refresh(t)
    return due_today


async def _deactivate_task_reminder(session: AsyncSession, task_id: uuid.UUID) -> None:
    result = await session.execute(
        select(ScheduledJob).where(
            ScheduledJob.job_type == "task_reminder",
            ScheduledJob.is_active.is_(True),
            ScheduledJob.payload["task_id"].astext == str(task_id),
        )
    )
    for job in result.scalars().all():
        job.is_active = False


async def update_task(
    session: AsyncSession,
    user_id: uuid.UUID,
    task_id: uuid.UUID | None = None,
    title: str | None = None,
    new_title: str | None = None,
    due_at: datetime | None = ...,
    remind_at: datetime | None = ...,
    description: str | None = None,
) -> tuple[Task | None, list[Task]]:
    """Edits an existing pending task in place instead of forcing the caller
    to create a new one - a missing edit path used to make the model offer
    "I'll create a new task with the right time instead", which left the
    original task in place too and produced duplicate tasks in practice.

    due_at/remind_at use Ellipsis as the "not provided, leave unchanged"
    sentinel, not None - None is itself meaningful here (clear the date)
    and must be settable, not indistinguishable from "field omitted". Real
    incident 2026-08-21: clearing a task's date in the Mini App (TaskDetail
    sends due_at=null) silently did nothing because this function's old
    `if due_at is not None` check treated a clear exactly like an omitted
    field - the task kept its old date and never moved out of Сегодня/
    Завтра. Same gap already existed for the model's own update_task tool
    call (no way to ask to remove a deadline either) - see
    tools_registry.py's clear_due_at/clear_remind_at handling.
    """
    resolved, candidates = await _resolve_pending_task(session, user_id, task_id, title)
    if not resolved:
        return None, candidates

    for task in resolved:
        if new_title:
            task.title = new_title
        if description is not None:
            task.description = description
        if due_at is not ...:
            task.due_at = due_at
        if remind_at is not ...:
            # A stale scheduled_job for the old remind_at would still fire at
            # the wrong time otherwise - deactivate it whether we're
            # replacing it with a new time or clearing it outright.
            await _deactivate_task_reminder(session, task.id)
            task.remind_at = remind_at
            if remind_at is not None:
                session.add(
                    ScheduledJob(
                        user_id=user_id,
                        job_type="task_reminder",
                        next_run_at=remind_at,
                        payload={"task_id": str(task.id)},
                    )
                )
        # A reschedule (e.g. the evening review moving an undone task to
        # tomorrow) resolves whatever completion-check this task was
        # awaiting - leaving the flag set would misapply a stale hint to
        # the next message.
        task.awaiting_confirmation = False

    await session.commit()
    for task in resolved:
        await session.refresh(task)
        await _sync_calendar_event(session, user_id, task)
        await session.refresh(task)
    return resolved[0], []


async def clear_today_priority(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Resets yesterday's priority marks before the morning check-in asks
    again - is_priority means "today's priority", not a lasting property,
    so a stale True from a previous day must not linger once a new day's
    question is asked.
    """
    result = await session.execute(
        select(Task).where(Task.user_id == user_id, Task.is_priority.is_(True))
    )
    for t in result.scalars().all():
        t.is_priority = False
    await session.commit()


async def mark_priority_tasks(
    session: AsyncSession,
    user_id: uuid.UUID,
    task_ids: list[uuid.UUID] | None = None,
    titles: list[str] | None = None,
) -> tuple[list[Task], list[str]]:
    """Marks one or more pending tasks as today's priority, resolving each
    id/title independently via the same logic complete_task/update_task/
    delete_task already use. Returns (resolved, unresolved) - unresolved
    holds the raw identifier string for anything that didn't match exactly
    one task, so the caller/model can ask the user to clarify rather than
    silently dropping it.
    """
    resolved: list[Task] = []
    unresolved: list[str] = []
    for task_id in task_ids or []:
        matches, _candidates = await _resolve_pending_task(session, user_id, task_id, None)
        if matches:
            resolved.extend(matches)
        else:
            unresolved.append(str(task_id))
    for title in titles or []:
        matches, _candidates = await _resolve_pending_task(session, user_id, None, title)
        if matches:
            resolved.extend(matches)
        else:
            unresolved.append(title)

    for task in resolved:
        task.is_priority = True
    await session.commit()
    for task in resolved:
        await session.refresh(task)
    return resolved, unresolved


async def unmark_priority_task(session: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID) -> Task | None:
    """Turns today's priority flag back off for one task - mark_priority_tasks
    only ever sets it True, and clear_today_priority is an all-or-nothing
    reset for the morning check-in, neither gives a way to toggle a single
    task back off (needed by the Mini App's priority star button).
    """
    task = await session.get(Task, task_id)
    if task is None or task.user_id != user_id:
        return None
    task.is_priority = False
    await session.commit()
    await session.refresh(task)
    return task


async def get_today_priority_tasks(session: AsyncSession, user_id: uuid.UUID) -> list[Task]:
    result = await session.execute(
        select(Task).where(
            Task.user_id == user_id,
            Task.is_priority.is_(True),
            Task.status == TaskStatus.PENDING.value,
        )
    )
    return list(result.scalars().all())


async def delete_task(
    session: AsyncSession,
    user_id: uuid.UUID,
    task_id: uuid.UUID | None = None,
    title: str | None = None,
) -> tuple[Task | None, list[Task]]:
    """Soft-delete via status=cancelled, same as the duplicate-cleanup
    pattern used manually throughout this project - cancelled tasks are
    already filtered out of format_task_list entirely (unlike done, which
    still shows with a check mark), so this is a real disappear-from-view
    delete without touching schema or losing the row for audit purposes.
    """
    resolved, candidates = await _resolve_pending_task(session, user_id, task_id, title)
    if not resolved:
        return None, candidates

    for task in resolved:
        await _deactivate_task_reminder(session, task.id)
        task.status = TaskStatus.CANCELLED.value
        task.awaiting_confirmation = False
        task.is_priority = False
    await session.commit()
    for task in resolved:
        await session.refresh(task)
        await _sync_calendar_event(session, user_id, task)
        await session.refresh(task)
    return resolved[0], []
