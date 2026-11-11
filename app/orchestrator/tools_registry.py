"""Maps LLM function-calling tool names to (schema, handler) pairs.

Handlers call plain core services (app/core/...) - they contain no LLM
logic themselves, which keeps business rules unit-testable independently
of the agent loop. Add new modules (Focus, Habits, ...) by registering
their tools here; the agent loop and prompt do not need to change.
"""

import calendar
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.core.crm import service as crm_service
from app.core.focus import service as focus_service
from app.core.habits.service import format_coach_hint, format_stats_summary, get_focus_stats
from app.core.integrations.service import (
    NotConnectedError,
    UnsupportedFileTypeError,
    create_calendar_event,
    create_connect_url,
    import_drive_document,
    list_calendar_events,
    list_emails,
    search_drive_files,
    send_email,
)
from app.core.knowledge import service as knowledge_service
from app.core.life_balance import service as life_balance_service
from app.core.life_goals import service as life_goals_service
from app.core.memory.service import upsert_fact
from app.core.namaz import service as namaz_service
from app.core.planner import service as planner_service
from app.core.reminders import service as reminders_service
from app.integrations.aladhan import AladhanAPIError
from app.core.tasks.service import (
    complete_task,
    complete_tasks_due_today,
    create_task,
    delete_task,
    format_task_list,
    list_tasks,
    list_tasks_for_date,
    mark_priority_tasks,
    update_task,
)
from app.database.models.focus_session import FocusSessionStatus
from app.database.models.user import User
from app.embeddings.factory import get_embedding_provider
from app.integrations.google_oauth import GoogleAPIError
from app.integrations.web_search import WebSearchError
from app.integrations.web_search import search as serper_search

HABIT_INSIGHTS_WINDOW_DAYS = 30


def _parse_datetime(value: str, tz_name: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt


def _safe_uuid(value: str | None) -> uuid.UUID | None:
    """Real incident, 2026-07-29: the model called start_focus_session with
    task_id='todo' - a placeholder string, not a real id, for a param whose
    schema already says it's optional ("если известен"). uuid.UUID() raised
    ValueError uncaught at the call site (dispatch_tool_call's try/except
    only wraps the handler call, not argument parsing before it), which
    left the DB session in a broken transaction state for the rest of that
    turn - real users saw a cascade of unrelated failures/garbled replies
    right after. Treating an unparseable id as "none given" instead of
    crashing lets every resolve_pending_task-style lookup fall back to
    title matching (or a clean "not found"), same as if the model had just
    omitted the field like the schema already tells it it's allowed to.
    """
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


# next_occurrence/first_interval_occurrence moved to app/core/reminders/service.py
# (pure reminders-domain date math, needed by the REST reminders route too) -
# kept as module-level aliases here so every existing call site below is
# unchanged.
_next_occurrence = reminders_service.next_occurrence
_first_interval_occurrence = reminders_service.first_interval_occurrence


RECURRENCE_LABELS = {"daily": "каждый день", "weekly": "каждую неделю"}

ToolHandler = Callable[[AsyncSession, User, dict], Awaitable[str]]


async def _handle_create_task(session: AsyncSession, user: User, arguments: dict) -> str:
    project_id = None
    project_name = arguments.get("project")
    if project_name:
        project = await planner_service.get_or_create_project(session, user.id, project_name)
        project_id = project.id

    remind_at = None
    if arguments.get("remind_at"):
        remind_at = _parse_datetime(arguments["remind_at"], user.timezone)

    due_at = None
    if arguments.get("due_at"):
        due_at = _parse_datetime(arguments["due_at"], user.timezone)

    task, was_created = await create_task(
        session=session,
        user_id=user.id,
        title=arguments["title"],
        description=arguments.get("description"),
        project_id=project_id,
        due_at=due_at,
        remind_at=remind_at,
    )
    reminder_note = f" Напомню в {remind_at:%H:%M}." if remind_at else ""
    if was_created:
        return f"Задача создана: «{task.title}» (id={task.id}).{reminder_note}"
    return (
        f"У тебя уже была похожая незавершённая задача «{task.title}» "
        f"(id={task.id}) - дополнил её новыми деталями вместо создания "
        f"дубликата.{reminder_note}"
    )


def _format_task_candidates(candidates: list, tz_name: str) -> str:
    # Numbered + creation date, not just title/time - two tasks created a
    # minute apart from the pre-update_task duplicate-creation bug can have
    # identical title AND identical due/remind time, which used to make the
    # disambiguation question show the same-looking option twice with no
    # way for the user to actually tell them apart - see the "Сфотографи-
    # роваться для загранпаспорта" bug.
    tz = ZoneInfo(tz_name)
    parts = []
    for i, t in enumerate(candidates, start=1):
        moment = t.remind_at or t.due_at
        time_note = f", {moment.astimezone(tz):%H:%M}" if moment else ""
        parts.append(
            f"{i}) «{t.title}»{time_note}, создана {t.created_at.astimezone(tz):%d.%m %H:%M} "
            f"(task_id={t.id})"
        )
    return "; ".join(parts)


async def _handle_complete_task(session: AsyncSession, user: User, arguments: dict) -> str:
    task_id_raw = arguments.get("task_id")
    task, candidates = await complete_task(
        session,
        user_id=user.id,
        task_id=_safe_uuid(task_id_raw),
        title=arguments.get("title"),
    )
    if task is not None:
        return f"Отметил «{task.title}» как выполненную."
    if candidates:
        options = _format_task_candidates(candidates, user.timezone)
        return (
            f"Нашлось несколько подходящих незавершённых задач: {options}. "
            "Не показывай пользователю task_id - опиши варианты по номеру, "
            "времени и дате создания, уточни, какую именно отметить "
            "выполненной, а когда пользователь ответит - вызови ещё раз с "
            "нужным task_id. Не выбирай сам."
        )
    return "Не нашёл такую незавершённую задачу."


async def _handle_update_task(session: AsyncSession, user: User, arguments: dict) -> str:
    task_id_raw = arguments.get("task_id")
    # Ellipsis ("don't touch this field") unless the model explicitly gave
    # a new value or asked to clear it - see update_task's own docstring
    # for why None can't double as both "clear" and "not mentioned" here.
    due_at = ...
    if arguments.get("clear_due_at"):
        due_at = None
    elif arguments.get("due_at"):
        due_at = _parse_datetime(arguments["due_at"], user.timezone)
    remind_at = ...
    if arguments.get("clear_remind_at"):
        remind_at = None
    elif arguments.get("remind_at"):
        remind_at = _parse_datetime(arguments["remind_at"], user.timezone)

    task, candidates = await update_task(
        session,
        user_id=user.id,
        task_id=_safe_uuid(task_id_raw),
        title=arguments.get("title"),
        new_title=arguments.get("new_title"),
        due_at=due_at,
        remind_at=remind_at,
        description=arguments.get("description"),
    )
    if task is not None:
        return f"Обновил «{task.title}»."
    if candidates:
        options = _format_task_candidates(candidates, user.timezone)
        return (
            f"Нашлось несколько подходящих незавершённых задач: {options}. "
            "Не показывай пользователю task_id - опиши варианты по номеру, "
            "времени и дате создания, уточни, какую именно менять, а когда "
            "пользователь ответит - вызови ещё раз с нужным task_id. Не "
            "выбирай сам."
        )
    return "Не нашёл такую незавершённую задачу."


async def _handle_complete_all_today_tasks(session: AsyncSession, user: User, arguments: dict) -> str:
    tasks = await complete_tasks_due_today(session, user.id, user.timezone)
    if not tasks:
        return "На сегодня не нашлось незавершённых задач с указанным временем."
    titles = "; ".join(f"«{t.title}»" for t in tasks)
    return f"Отметил как выполненные {len(tasks)} задач(и) на сегодня: {titles}."


async def _handle_delete_task(session: AsyncSession, user: User, arguments: dict) -> str:
    task_id_raw = arguments.get("task_id")
    task, candidates = await delete_task(
        session,
        user_id=user.id,
        task_id=_safe_uuid(task_id_raw),
        title=arguments.get("title"),
    )
    if task is not None:
        return f"Удалил «{task.title}» из списка задач."
    if candidates:
        options = _format_task_candidates(candidates, user.timezone)
        return (
            f"Нашлось несколько подходящих незавершённых задач: {options}. "
            "Не показывай пользователю task_id - опиши варианты по номеру, "
            "времени и дате создания, уточни, какую именно удалить, а когда "
            "пользователь ответит - вызови ещё раз с нужным task_id. Не "
            "выбирай сам."
        )
    return "Не нашёл такую незавершённую задачу."


async def _handle_mark_priority_tasks(session: AsyncSession, user: User, arguments: dict) -> str:
    raw_ids = arguments.get("task_ids") or []
    raw_titles = arguments.get("titles") or []
    parsed_ids = [_safe_uuid(i) for i in raw_ids]
    valid_ids = [i for i in parsed_ids if i is not None]
    # A malformed id (not a real UUID) is surfaced back to the model as
    # unresolved, same as a title that matched nothing - not silently
    # dropped and not a crash (see _safe_uuid's docstring for the incident
    # this avoids).
    bad_ids = [raw for raw, parsed in zip(raw_ids, parsed_ids) if parsed is None]
    resolved, unresolved = await mark_priority_tasks(
        session,
        user_id=user.id,
        task_ids=valid_ids,
        titles=raw_titles,
    )
    unresolved = unresolved + bad_ids
    if not resolved and not unresolved:
        return "Не получил ни одного названия задачи - уточни, что именно в приоритете."
    parts = []
    if resolved:
        names = ", ".join(f"«{t.title}»" for t in resolved)
        parts.append(f"Отметил в приоритете на сегодня: {names}.")
    if unresolved:
        names = ", ".join(f"«{u}»" for u in unresolved)
        parts.append(
            f"Не нашёл незавершённую задачу с таким названием: {names} - "
            "уточни у пользователя точную формулировку."
        )
    return " ".join(parts)


async def _handle_create_project(session: AsyncSession, user: User, arguments: dict) -> str:
    project, was_created = await planner_service.create_project(
        session, user_id=user.id, name=arguments["name"], description=arguments.get("description")
    )
    if was_created:
        return f"Проект «{project.name}» создан."
    return f"Проект «{project.name}» уже был - использую его."


async def _handle_plan_day(session: AsyncSession, user: User, arguments: dict) -> str:
    tasks = await planner_service.get_daily_plan(session, user.id, user.timezone)
    return format_task_list(tasks, user.timezone)


async def _handle_list_tasks(session: AsyncSession, user: User, arguments: dict) -> str:
    tasks = await list_tasks(session, user.id)
    return format_task_list(tasks, user.timezone)


async def _handle_list_tasks_for_date(session: AsyncSession, user: User, arguments: dict) -> str:
    target_date = datetime.fromisoformat(arguments["date"]).date()
    tasks = await list_tasks_for_date(session, user.id, target_date, user.timezone)
    return format_task_list(tasks, user.timezone)


async def _replace_matching_reminders(session: AsyncSession, user: User, text: str) -> None:
    # Neither set_*_reminder call took an id to update, so a repeated or
    # slightly-rephrased request for "the same" reminder (e.g. the model
    # re-calling the tool after a stray confirmation message like "работают,
    # все ок") used to just stack another active job on top - both fired
    # forever, a minute or so apart. Cancelling any existing match first
    # makes re-issuing the same request a replace, not a duplicate.
    matches = await reminders_service.find_recurring_reminders(session, user.id, text)
    for match in matches:
        await reminders_service.cancel_recurring_reminder(session, user.id, match.id)


async def _handle_set_recurring_reminder(session: AsyncSession, user: User, arguments: dict) -> str:
    recurrence = arguments.get("recurrence", "daily")
    next_run_at = _next_occurrence(arguments["time"], user.timezone)
    await _replace_matching_reminders(session, user, arguments["text"])
    await reminders_service.create_recurring_reminder(
        session,
        user_id=user.id,
        text=arguments["text"],
        next_run_at=next_run_at,
        recurrence=recurrence,
    )
    freq = RECURRENCE_LABELS.get(recurrence, recurrence)
    return f"Готово, буду присылать «{arguments['text']}» {freq} в {arguments['time']}."


async def _handle_set_interval_reminder(session: AsyncSession, user: User, arguments: dict) -> str:
    window_start = arguments["window_start"]
    window_end = arguments["window_end"]
    interval_minutes = arguments["interval_minutes"]
    next_run_at = _first_interval_occurrence(
        window_start, window_end, interval_minutes, user.timezone
    )
    await _replace_matching_reminders(session, user, arguments["text"])
    await reminders_service.create_interval_reminder(
        session,
        user_id=user.id,
        text=arguments["text"],
        next_run_at=next_run_at,
        interval_minutes=interval_minutes,
        window_start=window_start,
        window_end=window_end,
    )
    return (
        f"Готово, буду присылать «{arguments['text']}» каждые {interval_minutes} мин "
        f"с {window_start} до {window_end}."
    )


async def _handle_list_reminders(session: AsyncSession, user: User, arguments: dict) -> str:
    reminders = await reminders_service.list_recurring_reminders(session, user.id)
    if not reminders:
        return "Повторяющихся напоминаний пока нет."
    tz = ZoneInfo(user.timezone)
    lines = []
    for r in reminders:
        text = r.payload.get("text", "")
        if r.recurrence == "interval":
            lines.append(
                f"- «{text}» - каждые {r.payload.get('interval_minutes')} мин "
                f"с {r.payload.get('window_start')} до {r.payload.get('window_end')} (id={r.id})"
            )
            continue
        freq = RECURRENCE_LABELS.get(r.recurrence, r.recurrence)
        local = r.next_run_at.astimezone(tz)
        lines.append(f"- «{text}» - {freq} в {local:%H:%M} (id={r.id})")
    return "\n".join(lines)


async def _handle_cancel_reminder(session: AsyncSession, user: User, arguments: dict) -> str:
    reminder_id = _safe_uuid(arguments.get("reminder_id"))
    if reminder_id:
        ok = await reminders_service.cancel_recurring_reminder(session, user.id, reminder_id)
        return "Отменил." if ok else "Не нашёл такое напоминание."

    query = arguments.get("query")
    if not query:
        return "Не указано, какое напоминание отменить."
    matches = await reminders_service.find_recurring_reminders(session, user.id, query)
    if len(matches) == 1:
        await reminders_service.cancel_recurring_reminder(session, user.id, matches[0].id)
        return f"Отменил напоминание «{matches[0].payload.get('text', '')}»."
    if not matches:
        return "Не нашёл подходящее повторяющееся напоминание."
    options = "; ".join(f"«{m.payload.get('text', '')}»" for m in matches)
    return (
        f"Нашлось несколько подходящих напоминаний: {options}. "
        "Уточни у пользователя, какое именно отменить, не выбирай сам."
    )


async def _handle_enable_namaz_reminders(session: AsyncSession, user: User, arguments: dict) -> str:
    city = arguments["city"]
    country = arguments.get("country", "Russia")
    try:
        await namaz_service.enable_namaz_reminders(session, user, city, country)
    except AladhanAPIError:
        return (
            "Не получилось получить расписание намаза для этого города - "
            "попробуй другое написание (например латиницей, как в адресной "
            "строке карт)."
        )
    return (
        f"Готово, буду присылать напоминания о времени намаза (5 раз в день) "
        f"для города {city}."
    )


async def _handle_disable_namaz_reminders(session: AsyncSession, user: User, arguments: dict) -> str:
    ok = await namaz_service.disable_namaz_reminders(session, user)
    return "Отключил напоминания о намазе." if ok else "Напоминания о намазе и так не были включены."


async def _handle_get_next_namaz(session: AsyncSession, user: User, arguments: dict) -> str:
    if not user.namaz_city:
        return "Напоминания о намазе не включены - сначала используй enable_namaz_reminders."
    prayers = await namaz_service.get_upcoming_prayers(session, user.id)
    if not prayers:
        return (
            "На сегодня активных напоминаний о намазе больше не осталось - "
            "расписание на завтра обновится ночью."
        )
    tz = ZoneInfo(user.timezone)
    next_prayer = prayers[0]
    prayer_name = namaz_service.PRAYER_NAMES_RU.get(
        next_prayer.payload.get("prayer", ""), next_prayer.payload.get("prayer", "")
    )
    local_time = next_prayer.next_run_at.astimezone(tz)
    return f"Ближайший намаз ({prayer_name}) в {user.namaz_city} — сегодня в {local_time:%H:%M}."


async def _handle_save_life_balance_rating(session: AsyncSession, user: User, arguments: dict) -> str:
    ratings = {
        domain: int(arguments[domain])
        for domain in life_balance_service.DOMAIN_LABELS_RU
        if domain in arguments
    }
    await life_balance_service.save_rating(session, user.id, ratings)
    history = await life_balance_service.get_recent_ratings(session, user.id, limit=2)
    return life_balance_service.format_wheel_summary(history, user.timezone)


async def _handle_get_life_balance_history(session: AsyncSession, user: User, arguments: dict) -> str:
    history = await life_balance_service.get_recent_ratings(session, user.id, limit=2)
    return life_balance_service.format_wheel_summary(history, user.timezone)


_MONTH_NAMES_RU = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


def _parse_target_month(raw: str | None) -> date | None:
    """"YYYY-MM" (whatever plausible-looking format the model sends) -> the
    last day of that month, same normalization the Mini App's own
    <input type="month"> does (see monthInputToLastDay in frontend/src/
    api.ts) - target_date always stores a specific day, not a bare month.
    """
    if not raw:
        return None
    try:
        year_str, month_str = raw.split("-")[:2]
        year, month = int(year_str), int(month_str)
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, last_day)
    except (ValueError, TypeError):
        return None


async def _handle_save_life_goal(session: AsyncSession, user: User, arguments: dict) -> str:
    horizon_raw = arguments.get("horizon_months")
    target_date = _parse_target_month(arguments.get("target_month"))
    goal = await life_goals_service.save_goal(
        session,
        user.id,
        # A specific month overrides any horizon, same precedence the
        # Mini App's own picker uses - see target_date_for_horizon.
        horizon_months=None if target_date else (int(horizon_raw) if horizon_raw is not None else None),
        title=arguments["title"],
        description=arguments.get("description"),
        target_date=target_date,
    )
    if target_date:
        return f"Цель на {_MONTH_NAMES_RU[target_date.month - 1]} {target_date.year} сохранена: «{goal.title}»."
    if goal.horizon_months is None:
        return f"Цель на всю жизнь сохранена: «{goal.title}»."
    return f"Цель {life_goals_service.format_horizon(goal.horizon_months).lower()} сохранена: «{goal.title}»."


async def _handle_list_life_goals(session: AsyncSession, user: User, arguments: dict) -> str:
    goals = await life_goals_service.list_goals(session, user.id)
    return life_goals_service.format_goals(goals)


def _format_goal_candidates(candidates: list) -> str:
    parts = [
        f"{i}) «{g.title}» ({life_goals_service.format_horizon(g.horizon_months)})"
        for i, g in enumerate(candidates, start=1)
    ]
    return "; ".join(parts)


async def _resolve_goal_or_reply(session: AsyncSession, user: User, title: str | None):
    """Shared by update_life_goal: (goal, None) on an unambiguous match, or
    (None, reply_text) when the caller should just return that text as-is
    (not found / genuinely ambiguous)."""
    if not title:
        return None, "Укажи название цели, которую нужно изменить."
    goal, candidates = await life_goals_service.resolve_goal_by_title(session, user.id, title)
    if goal is not None:
        return goal, None
    if candidates:
        options = _format_goal_candidates(candidates)
        return None, (
            f"Нашлось несколько подходящих целей: {options}. Уточни, какую именно "
            "ты имел в виду, по номеру или формулировке."
        )
    return None, "Не нашёл такую цель среди активных."


async def _handle_update_life_goal(session: AsyncSession, user: User, arguments: dict) -> str:
    goal, reply = await _resolve_goal_or_reply(session, user, arguments.get("title"))
    if goal is None:
        return reply

    target_date = _parse_target_month(arguments.get("target_month"))
    horizon_raw = arguments.get("horizon_months")
    clear_horizon = bool(arguments.get("clear_horizon"))
    new_title = arguments.get("new_title")
    has_description = "description" in arguments

    if clear_horizon or target_date is not None:
        # Explicit None clears the horizon back to "Задачи на жизнь" -
        # update_goal already supports this (see its own docstring), the
        # tool just never had a way to ask for it: without clear_horizon,
        # "убери у цели срок" had nothing to pass but Ellipsis ("no
        # change"), so the goal silently kept its old horizon.
        horizon_months = None
    elif horizon_raw is not None:
        horizon_months = int(horizon_raw)
    else:
        horizon_months = ...

    # update_goal is a true partial-update - Ellipsis for horizon_months/
    # description/target_date means "leave alone". If the call ends up with
    # nothing at all to change (LLM called this with only a resolvable
    # title, no other argument), silently claiming "Обновил цель" would be
    # exactly the false-confirmation class this project's hallucination
    # detector otherwise exists to catch, just from a tool handler instead
    # of the model's own prose.
    if horizon_months is ... and target_date is None and not new_title and not has_description:
        return "Не понял, что именно изменить в цели - укажи новую формулировку, срок или заметку."

    updated = await life_goals_service.update_goal(
        session,
        user.id,
        goal.id,
        horizon_months=horizon_months,
        title=new_title,
        description=arguments.get("description") if has_description else ...,
        target_date=target_date if target_date is not None else ...,
    )
    return f"Обновил цель «{updated.title}»."


async def _handle_complete_life_goal(session: AsyncSession, user: User, arguments: dict) -> str:
    done = bool(arguments.get("done", True))
    title = arguments.get("title")
    if not title:
        return "Укажи название цели."
    # Undoing "выполнено" needs to search among already-done goals instead
    # of active ones.
    resolver = life_goals_service.resolve_goal_by_title if done else life_goals_service.resolve_done_goal_by_title
    goal, candidates = await resolver(session, user.id, title)
    if goal is None:
        if candidates:
            return (
                f"Нашлось несколько подходящих целей: {_format_goal_candidates(candidates)}. "
                "Уточни, какую именно ты имел в виду."
            )
        return f"Не нашёл такую цель среди {'активных' if done else 'выполненных'}."
    updated = await life_goals_service.set_goal_done(session, user.id, goal.id, done)
    return f"Отметил «{updated.title}» {'выполненной' if done else 'снова активной'}."


async def _handle_start_focus_session(session: AsyncSession, user: User, arguments: dict) -> str:
    existing = await focus_service.get_open_session(session, user.id)
    if existing is not None and existing.status != FocusSessionStatus.PLANNED.value:
        return "У тебя уже есть незавершённая фокус-сессия — сначала закрой её."

    task_id_raw = arguments.get("task_id")
    duration = arguments.get("planned_duration_min") or focus_service.DEFAULT_DURATION_MIN
    await focus_service.start_session(
        session,
        user_id=user.id,
        task_id=_safe_uuid(task_id_raw),
        planned_duration_min=duration,
    )
    return f"Погнали. Следующие {duration} минут — глубокая работа, я напомню о себе позже."


async def _handle_complete_focus_session(session: AsyncSession, user: User, arguments: dict) -> str:
    focus_session = await focus_service.complete_session(
        session, user_id=user.id, outcome_note=arguments["outcome_note"]
    )
    if focus_session is None:
        return "Сейчас нет активной фокус-сессии, которую можно завершить."
    return "Отлично, записал. Можно отдохнуть перед следующим блоком."


async def _handle_abandon_focus_session(session: AsyncSession, user: User, arguments: dict) -> str:
    focus_session = await focus_service.abandon_session(
        session, user_id=user.id, interruption_reason=arguments["interruption_reason"]
    )
    if focus_session is None:
        return "Сейчас нет активной фокус-сессии, которую можно закрыть."

    result = "Понял, зафиксировал, что помешало."
    # Coach moment: right after a setback is when data-grounded support
    # matters most, not a report - see HABIT_INSIGHTS_WINDOW_DAYS usage below.
    since = datetime.now(timezone.utc) - timedelta(days=HABIT_INSIGHTS_WINDOW_DAYS)
    stats = await get_focus_stats(session, user.id, since)
    coach_hint = format_coach_hint(stats)
    if coach_hint:
        result += (
            f" В ответе можешь по-человечески поддержать пользователя, опираясь "
            f"на этот факт из его истории (не выдумывай другие цифры): {coach_hint}"
        )
    return result


async def _handle_remember_fact(session: AsyncSession, user: User, arguments: dict) -> str:
    await upsert_fact(session, user_id=user.id, key=arguments["key"], value=arguments["value"])
    return "Запомнил."


async def _handle_save_note(session: AsyncSession, user: User, arguments: dict) -> str:
    try:
        document = await knowledge_service.save_document(
            session,
            get_embedding_provider(),
            user_id=user.id,
            title=arguments["title"],
            content=arguments["content"],
        )
    except knowledge_service.NoteTooLongError:
        return "Заметка слишком длинная - сократи её и попробуй сохранить ещё раз."
    except knowledge_service.EmptyNoteError:
        return "Текст заметки пустой - нечего сохранять."
    return f"Заметка «{document.title}» сохранена в базу знаний."


async def _handle_search_knowledge(session: AsyncSession, user: User, arguments: dict) -> str:
    chunks = await knowledge_service.search(
        session, get_embedding_provider(), user_id=user.id, query=arguments["query"]
    )
    if not chunks:
        return "В базе знаний ничего не найдено по этому запросу."
    return "\n---\n".join(chunk.content for chunk in chunks)


async def _handle_create_contact(session: AsyncSession, user: User, arguments: dict) -> str:
    contact, was_created = await crm_service.create_contact(
        session,
        user_id=user.id,
        name=arguments["name"],
        phone=arguments.get("phone"),
        email=arguments.get("email"),
        company=arguments.get("company"),
        notes=arguments.get("notes"),
    )
    if was_created:
        return f"Контакт «{contact.name}» сохранён."
    # A same-name contact already existed and got updated instead of a new
    # one being created - only fields that were previously empty actually
    # changed (create_contact never overwrites a filled field), so if this
    # is really a different person with the same name, say so plainly
    # rather than letting the model imply a fresh contact was made.
    return (
        f"Контакт «{contact.name}» уже был - дополнил его данные новыми "
        "деталями (то, что уже было заполнено, не менял). Если это на "
        "самом деле другой человек с тем же именем - скажи об этом, "
        "различить их по одному имени я не могу."
    )


async def _handle_log_interaction(session: AsyncSession, user: User, arguments: dict) -> str:
    await crm_service.log_interaction(
        session,
        user_id=user.id,
        contact_name=arguments["contact_name"],
        note=arguments["note"],
        company=arguments.get("company"),
        phone=arguments.get("phone"),
        email=arguments.get("email"),
    )
    return f"Записал общение с «{arguments['contact_name']}»."


async def _handle_connect_google_account(session: AsyncSession, user: User, arguments: dict) -> str:
    url = await create_connect_url(session, user.id)
    return f"Перейди по ссылке, чтобы подключить Google-аккаунт (календарь, диск, почта): {url}"


async def _handle_create_calendar_event(session: AsyncSession, user: User, arguments: dict) -> str:
    try:
        start = _parse_datetime(arguments["start"], user.timezone)
        end = _parse_datetime(arguments["end"], user.timezone)
        event = await create_calendar_event(
            session,
            user.id,
            summary=arguments["summary"],
            start=start,
            end=end,
            description=arguments.get("description"),
        )
    except NotConnectedError:
        url = await create_connect_url(session, user.id)
        return f"Google Calendar ещё не подключён. Перейди по ссылке: {url}"
    except GoogleAPIError:
        return "Не получилось создать событие в календаре — попробуй ещё раз позже."
    return f"Событие «{event.get('summary', arguments['summary'])}» создано в календаре."


async def _handle_list_calendar_events(session: AsyncSession, user: User, arguments: dict) -> str:
    try:
        time_min = _parse_datetime(arguments["time_min"], user.timezone)
        time_max = _parse_datetime(arguments["time_max"], user.timezone)
        events = await list_calendar_events(session, user.id, time_min, time_max)
    except NotConnectedError:
        url = await create_connect_url(session, user.id)
        return f"Google Calendar ещё не подключён. Перейди по ссылке: {url}"
    except GoogleAPIError:
        return "Не получилось получить события календаря — попробуй ещё раз позже."

    if not events:
        return "На этот период событий в календаре нет."
    lines = []
    for e in events:
        start = e.get("start", {}).get("dateTime") or e.get("start", {}).get("date", "")
        lines.append(f"- {e.get('summary', '(без названия)')} ({start})")
    return "\n".join(lines)


async def _handle_import_drive_document(session: AsyncSession, user: User, arguments: dict) -> str:
    query = arguments["name"]
    try:
        matches = await search_drive_files(session, user.id, query)
    except NotConnectedError:
        url = await create_connect_url(session, user.id)
        return f"Google-аккаунт ещё не подключён. Перейди по ссылке: {url}"
    except GoogleAPIError:
        return "Не получилось найти файлы на Google Диске — попробуй ещё раз позже."

    if not matches:
        return f"На Google Диске не нашлось файлов с названием «{query}»."
    if len(matches) > 1:
        names = "; ".join(m["name"] for m in matches)
        return f"Нашлось несколько файлов: {names}. Уточни название."

    match = matches[0]
    try:
        document = await import_drive_document(
            session,
            get_embedding_provider(),
            user_id=user.id,
            file_id=match["id"],
            file_name=match["name"],
            mime_type=match["mimeType"],
        )
    except UnsupportedFileTypeError:
        return f"«{match['name']}» — неподдерживаемый тип файла (сейчас работают только Google Docs и текстовые файлы)."
    except GoogleAPIError:
        return "Не получилось скачать файл с Google Диска — попробуй ещё раз позже."
    return f"«{document.title}» импортирован в базу знаний с Google Диска."


async def _handle_list_emails(session: AsyncSession, user: User, arguments: dict) -> str:
    try:
        messages = await list_emails(
            session, user.id, query=arguments.get("query"), max_results=10
        )
    except NotConnectedError:
        url = await create_connect_url(session, user.id)
        return f"Google-аккаунт ещё не подключён. Перейди по ссылке: {url}"
    except GoogleAPIError:
        return "Не получилось получить письма — попробуй ещё раз позже."

    if not messages:
        return "Писем по этому запросу не нашлось."
    lines = [
        f"- От: {m['from']} | {m['subject']} ({m['date']})\n  {m['snippet']}" for m in messages
    ]
    return "\n".join(lines)


async def _handle_send_email(session: AsyncSession, user: User, arguments: dict) -> str:
    try:
        await send_email(
            session,
            user.id,
            to=arguments["to"],
            subject=arguments["subject"],
            body=arguments["body"],
        )
    except NotConnectedError:
        url = await create_connect_url(session, user.id)
        return f"Google-аккаунт ещё не подключён. Перейди по ссылке: {url}"
    except GoogleAPIError:
        return "Не получилось отправить письмо — попробуй ещё раз позже."
    return f"Письмо отправлено на {arguments['to']}."


async def _handle_get_habit_insights(session: AsyncSession, user: User, arguments: dict) -> str:
    since = datetime.now(timezone.utc) - timedelta(days=HABIT_INSIGHTS_WINDOW_DAYS)
    stats = await get_focus_stats(session, user.id, since)
    return format_stats_summary(stats)


async def _handle_get_contact_info(session: AsyncSession, user: User, arguments: dict) -> str:
    result = await crm_service.get_contact_with_history(
        session, user_id=user.id, name=arguments["name"]
    )
    if result is None:
        return f"Контакт «{arguments['name']}» не найден."
    contact, interactions = result
    parts = [f"Контакт: {contact.name}"]
    if contact.phone:
        parts.append(f"Телефон: {contact.phone}")
    if contact.email:
        parts.append(f"Email: {contact.email}")
    if contact.company:
        parts.append(f"Компания: {contact.company}")
    if contact.notes:
        parts.append(f"Заметки: {contact.notes}")
    if interactions:
        # created_at is stored UTC - displaying it raw showed the wrong
        # calendar date for users east of UTC shortly after local midnight,
        # same bug already fixed for the task list formatter.
        tz = ZoneInfo(user.timezone)
        history = "\n".join(
            f"- {i.created_at.astimezone(tz):%Y-%m-%d}: {i.note}" for i in interactions
        )
        parts.append(f"История общения:\n{history}")
    return "\n".join(parts)


async def _handle_web_search(session: AsyncSession, user: User, arguments: dict) -> str:
    try:
        result = await serper_search(arguments["query"])
    except WebSearchError as e:
        logger.error("web_search failed: %s", e)
        return "Не получилось выполнить поиск в интернете — попробуй ещё раз позже."

    parts = []
    if result.get("answer"):
        parts.append(result["answer"])
    for item in result.get("results", [])[:5]:
        parts.append(f"- {item.get('title', '')}: {item.get('content', '')} ({item.get('url', '')})")
    return "\n".join(parts) if parts else "По этому запросу ничего не нашлось."


TOOLS: dict[str, dict] = {
    "create_task": {
        "schema": {
            "type": "function",
            "function": {
                "name": "create_task",
                "description": "Создать задачу для пользователя.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Короткое название задачи"},
                        "description": {"type": "string", "description": "Необязательные детали"},
                        "project": {
                            "type": "string",
                            "description": (
                                "Название проекта, если задача к нему относится - "
                                "если проекта с таким именем ещё нет, он будет создан"
                            ),
                        },
                        "remind_at": {
                            "type": "string",
                            "description": (
                                "Точное время напоминания в формате ISO 8601 (например "
                                "2026-07-18T08:40:00), если пользователь просит напомнить "
                                "в конкретный момент - тогда реально придёт push-уведомление. "
                                "Разово, без повтора."
                            ),
                        },
                        "due_at": {
                            "type": "string",
                            "description": (
                                "Дедлайн задачи в формате ISO 8601. Указывай, если "
                                "пользователь называет момент, к которому что-то должно "
                                "быть сделано (например «к 10 утра быть у Ромы» - due_at "
                                "на 10:00). ВАЖНО: указывай due_at и тогда, когда названа "
                                "только дата без конкретного времени суток - «сегодня», "
                                "«завтра», день недели («задача на сегодня получить "
                                "загранник», «сходить сегодня на тренировку», без слова "
                                "«задача») - в этом случае ставь due_at на полночь (00:00) "
                                "этой даты. Реальная ошибка: due_at пропускали именно для "
                                "таких формулировок без явного времени, из-за чего задача "
                                "сохранялась, но не попадала ни в дневной план, ни в "
                                "утренний чек-ин, ни в список «задачи на сегодня/завтра» - "
                                "выглядело так, будто она вообще не создалась. Это не то "
                                "же самое, что remind_at - due_at не создаёт "
                                "push-уведомление. Если у задачи есть и дедлайн, и момент "
                                "напоминания - передавай оба."
                            ),
                        },
                    },
                    "required": ["title"],
                },
            },
        },
        "handler": _handle_create_task,
    },
    "complete_task": {
        "schema": {
            "type": "function",
            "function": {
                "name": "complete_task",
                "description": (
                    "Отметить задачу как выполненную. Вызывай, когда пользователь "
                    "говорит, что сделал что-то из списка дел, или подтверждает "
                    "выполнение задачи, о которой недавно спросили."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "UUID задачи, если известен из контекста",
                        },
                        "title": {
                            "type": "string",
                            "description": "Название задачи, если id неизвестен",
                        },
                    },
                },
            },
        },
        "handler": _handle_complete_task,
    },
    "complete_all_today_tasks": {
        "schema": {
            "type": "function",
            "function": {
                "name": "complete_all_today_tasks",
                "description": (
                    "Отметить как выполненные ВСЕ незавершённые задачи, у "
                    "которых дедлайн или напоминание сегодня (по местному "
                    "времени пользователя) - вызывай для запросов вида «все "
                    "задачи на сегодня выполнены/сделаны». Не пытайся "
                    "перечислять задачи по памяти и вызывать complete_task "
                    "по одной на каждую - у тебя нет надёжного способа "
                    "отличить сегодняшние задачи от завтрашних просто по "
                    "истории разговора, этот инструмент считает даты "
                    "правильно."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        "handler": _handle_complete_all_today_tasks,
    },
    "update_task": {
        "schema": {
            "type": "function",
            "function": {
                "name": "update_task",
                "description": (
                    "Изменить существующую незавершённую задачу - название, "
                    "дедлайн (due_at) или время напоминания (remind_at). "
                    "Вызывай вместо create_task, когда пользователь просит "
                    "поправить/дополнить уже существующую задачу (например "
                    "добавить время напоминания к задаче, у которой его не "
                    "было) - создание новой задачи вместо правки оставит "
                    "старую висеть и задвоит список. Если просят убрать/"
                    "снять дедлайн или напоминание вообще (не перенести, а "
                    "именно убрать) - используй clear_due_at/clear_remind_at "
                    "(true), а не due_at/remind_at."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "UUID задачи, если известен из контекста",
                        },
                        "title": {
                            "type": "string",
                            "description": "Текущее название задачи, если id неизвестен",
                        },
                        "new_title": {
                            "type": "string",
                            "description": "Новое название, если его нужно изменить",
                        },
                        "due_at": {
                            "type": "string",
                            "description": "Новый дедлайн в формате ISO 8601",
                        },
                        "remind_at": {
                            "type": "string",
                            "description": (
                                "Новое время напоминания в формате ISO 8601 - "
                                "отменяет и заменяет ранее запланированное "
                                "напоминание для этой задачи."
                            ),
                        },
                        "clear_due_at": {
                            "type": "boolean",
                            "description": "true - убрать дедлайн у задачи совсем, не заменяя его новым",
                        },
                        "clear_remind_at": {
                            "type": "boolean",
                            "description": "true - убрать напоминание у задачи совсем, не заменяя его новым",
                        },
                        "description": {
                            "type": "string",
                            "description": "Новая заметка/описание к задаче - заменяет прежнюю целиком",
                        },
                    },
                },
            },
        },
        "handler": _handle_update_task,
    },
    "delete_task": {
        "schema": {
            "type": "function",
            "function": {
                "name": "delete_task",
                "description": (
                    "Удалить задачу из списка пользователя насовсем "
                    "(например пользователь просит убрать/удалить ненужную "
                    "или ошибочно созданную задачу - в отличие от "
                    "complete_task, здесь ничего не выполнено, задача "
                    "просто больше не нужна)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "UUID задачи, если известен из контекста",
                        },
                        "title": {
                            "type": "string",
                            "description": "Название задачи, если id неизвестен",
                        },
                    },
                },
            },
        },
        "handler": _handle_delete_task,
    },
    "mark_priority_tasks": {
        "schema": {
            "type": "function",
            "function": {
                "name": "mark_priority_tasks",
                "description": (
                    "Отметить одну или несколько задач как приоритетные на "
                    "сегодня. Вызывай в ответ на утренний вопрос «какая "
                    "задача сегодня в приоритете», а также если пользователь "
                    "сам явно называет задачу(и) приоритетной вне этого "
                    "вопроса. Если названия неточные - сначала вызови "
                    "list_tasks/list_tasks_for_date, чтобы получить точные "
                    "task_id, не угадывай."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "UUID задач, если известны из контекста",
                        },
                        "titles": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Названия задач, если id неизвестны",
                        },
                    },
                },
            },
        },
        "handler": _handle_mark_priority_tasks,
    },
    "set_recurring_reminder": {
        "schema": {
            "type": "function",
            "function": {
                "name": "set_recurring_reminder",
                "description": (
                    "Создать ПОВТОРЯЮЩЕЕСЯ напоминание - присылает text в "
                    "указанное время каждый день/неделю, до отмены. Вызывай, "
                    "когда пользователь просит напоминать/спрашивать о чём-то "
                    "регулярно, а не один раз (для разового - create_task с "
                    "remind_at)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Текст, который будет присылаться (вопрос или напоминание)",
                        },
                        "time": {
                            "type": "string",
                            "description": "Время в формате ЧЧ:ММ по локальному времени пользователя",
                        },
                        "recurrence": {
                            "type": "string",
                            "enum": ["daily", "weekly"],
                            "description": "Периодичность, по умолчанию daily",
                        },
                    },
                    "required": ["text", "time"],
                },
            },
        },
        "handler": _handle_set_recurring_reminder,
    },
    "set_interval_reminder": {
        "schema": {
            "type": "function",
            "function": {
                "name": "set_interval_reminder",
                "description": (
                    "Создать напоминание, которое присылается с фиксированным "
                    "интервалом внутри окна времени каждый день, до отмены - "
                    "например «каждые полтора часа с 9 до 18 спрашивай, что я "
                    "делаю». Для одного фиксированного времени в день/неделю "
                    "используй set_recurring_reminder вместо этого."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Текст, который будет присылаться (вопрос или напоминание)",
                        },
                        "interval_minutes": {
                            "type": "integer",
                            "description": "Интервал между напоминаниями в минутах",
                        },
                        "window_start": {
                            "type": "string",
                            "description": "Начало окна, ЧЧ:ММ по локальному времени пользователя",
                        },
                        "window_end": {
                            "type": "string",
                            "description": "Конец окна, ЧЧ:ММ по локальному времени пользователя",
                        },
                    },
                    "required": ["text", "interval_minutes", "window_start", "window_end"],
                },
            },
        },
        "handler": _handle_set_interval_reminder,
    },
    "list_reminders": {
        "schema": {
            "type": "function",
            "function": {
                "name": "list_reminders",
                "description": "Получить список активных повторяющихся напоминаний пользователя (обычных и интервальных).",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        "handler": _handle_list_reminders,
    },
    "cancel_reminder": {
        "schema": {
            "type": "function",
            "function": {
                "name": "cancel_reminder",
                "description": (
                    "Отменить повторяющееся напоминание, созданное "
                    "set_recurring_reminder или set_interval_reminder. Укажи "
                    "reminder_id, если он известен из контекста (например из "
                    "list_reminders), иначе query - фрагмент текста напоминания."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reminder_id": {
                            "type": "string",
                            "description": "UUID напоминания, если известен",
                        },
                        "query": {
                            "type": "string",
                            "description": "Фрагмент текста напоминания, если id неизвестен",
                        },
                    },
                },
            },
        },
        "handler": _handle_cancel_reminder,
    },
    "enable_namaz_reminders": {
        "schema": {
            "type": "function",
            "function": {
                "name": "enable_namaz_reminders",
                "description": (
                    "Включить напоминания о времени намаза (5 раз в день: "
                    "Фаджр, Зухр, Аср, Магриб, Иша) - времена рассчитываются "
                    "заново на каждый день по указанному городу. Вызывай, "
                    "когда пользователь просит напоминания о намазе/молитве "
                    "и называет свой город. Если город не назван - сначала "
                    "спроси, не вызывай тул с угаданным городом."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                            "description": "Город пользователя (можно по-русски или латиницей)",
                        },
                        "country": {
                            "type": "string",
                            "description": "Страна, по умолчанию Russia",
                        },
                    },
                    "required": ["city"],
                },
            },
        },
        "handler": _handle_enable_namaz_reminders,
    },
    "disable_namaz_reminders": {
        "schema": {
            "type": "function",
            "function": {
                "name": "disable_namaz_reminders",
                "description": "Отключить ранее включённые напоминания о времени намаза.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        "handler": _handle_disable_namaz_reminders,
    },
    "get_next_namaz": {
        "schema": {
            "type": "function",
            "function": {
                "name": "get_next_namaz",
                "description": (
                    "Узнать ближайший ещё не наступивший намаз сегодня (название "
                    "и точное время) для пользователя с включёнными "
                    "напоминаниями. Всегда вызывай этот тул для вопросов вида "
                    "«когда ближайший/следующий намаз» - не отвечай по памяти "
                    "или общими знаниями, реальное расписание зависит от даты "
                    "и города и могло уже частично пройти за сегодня."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        "handler": _handle_get_next_namaz,
    },
    "save_life_balance_rating": {
        "schema": {
            "type": "function",
            "function": {
                "name": "save_life_balance_rating",
                "description": (
                    "Сохранить оценку 'колеса баланса' - по 8 стандартным "
                    "сферам жизни, каждая от 1 до 10. Перед вызовом спроси "
                    "пользователя оценку по каждой сфере по очереди, не "
                    "придумывай числа сам. После сохранения покажи "
                    "пользователю результат из ответа тула как есть - там "
                    "уже есть сравнение с прошлой оценкой, если она была."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "health": {"type": "integer", "description": "Здоровье, 1-10"},
                        "career": {"type": "integer", "description": "Карьера/работа, 1-10"},
                        "finance": {"type": "integer", "description": "Финансы, 1-10"},
                        "relationships": {
                            "type": "integer",
                            "description": "Отношения/семья, 1-10",
                        },
                        "personal_growth": {
                            "type": "integer",
                            "description": "Личностный рост, 1-10",
                        },
                        "recreation": {
                            "type": "integer",
                            "description": "Отдых/развлечения, 1-10",
                        },
                        "environment": {
                            "type": "integer",
                            "description": "Окружение/дом, 1-10",
                        },
                        "meaning": {
                            "type": "integer",
                            "description": "Смысл/духовность, 1-10",
                        },
                    },
                    "required": [
                        "health",
                        "career",
                        "finance",
                        "relationships",
                        "personal_growth",
                        "recreation",
                        "environment",
                        "meaning",
                    ],
                },
            },
        },
        "handler": _handle_save_life_balance_rating,
    },
    "get_life_balance_history": {
        "schema": {
            "type": "function",
            "function": {
                "name": "get_life_balance_history",
                "description": (
                    "Получить последнюю оценку колеса баланса и сравнение с "
                    "предыдущей, если она есть."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        "handler": _handle_get_life_balance_history,
    },
    "save_life_goal": {
        "schema": {
            "type": "function",
            "function": {
                "name": "save_life_goal",
                "description": (
                    "Сохранить цель пользователя в разделе «Жизнь» (не в "
                    "обычные задачи с дедлайном - это про ориентиры/цели, а "
                    "не текущие дела). Покрывает любой горизонт, который "
                    "предлагает сама «Жизнь» в приложении: от недели до 30 "
                    "лет, конкретный календарный месяц, или без срока вообще "
                    "('Задачи на жизнь'). Используй это для ЛЮБОЙ фразы вида "
                    "«добавь цель...»/«цель на месяц...»/«хочу через 5 "
                    "лет...», а не create_task, даже если по звучанию похоже "
                    "на дело."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "horizon_months": {
                            "type": "integer",
                            "enum": [0, 1, 3, 6, 12, 36, 60, 120, 240, 360],
                            "description": (
                                "Горизонт цели: 0 = на неделю (до ближайшего "
                                "воскресенья), 1/3/6 = на 1/3/6 месяцев, "
                                "12/36/60/120/240/360 = на 1/3/5/10/20/30 "
                                "лет (год отсчитывается календарно: 12 = до "
                                "конца текущего года, 36 = до конца года+2, "
                                "и т.д.). Не передавай вместе с target_month. "
                                "Не передавай вообще ни этот параметр, ни "
                                "target_month, если цель на всю жизнь без "
                                "конкретного срока."
                            ),
                        },
                        "target_month": {
                            "type": "string",
                            "description": (
                                "Для цели на конкретный календарный месяц "
                                "(«цель на сентябрь», «в октябре хочу...») - "
                                "месяц в формате YYYY-MM. Не передавай вместе "
                                "с horizon_months."
                            ),
                        },
                        "title": {"type": "string", "description": "Короткая формулировка цели"},
                        "description": {
                            "type": "string",
                            "description": "Необязательные детали/уточнения",
                        },
                    },
                    "required": ["title"],
                },
            },
        },
        "handler": _handle_save_life_goal,
    },
    "list_life_goals": {
        "schema": {
            "type": "function",
            "function": {
                "name": "list_life_goals",
                "description": "Показать все сохранённые жизненные цели, сгруппированные по горизонту.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        "handler": _handle_list_life_goals,
    },
    "update_life_goal": {
        "schema": {
            "type": "function",
            "function": {
                "name": "update_life_goal",
                "description": (
                    "Изменить уже существующую цель в разделе «Жизнь» - "
                    "формулировку, горизонт/срок или заметку. Не для "
                    "создания новой цели (для этого save_life_goal) и не "
                    "для отметки выполненной (для этого complete_life_goal)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Текущая формулировка цели, по которой её найти",
                        },
                        "new_title": {
                            "type": "string",
                            "description": "Новая формулировка, если её нужно изменить",
                        },
                        "horizon_months": {
                            "type": "integer",
                            "enum": [0, 1, 3, 6, 12, 36, 60, 120, 240, 360],
                            "description": (
                                "Новый горизонт - те же значения, что у "
                                "save_life_goal (0 = неделя, 1/3/6 = "
                                "месяцы, 12+ = года). Не передавай вместе "
                                "с target_month."
                            ),
                        },
                        "target_month": {
                            "type": "string",
                            "description": "Новый конкретный месяц в формате YYYY-MM, вместо horizon_months",
                        },
                        "clear_horizon": {
                            "type": "boolean",
                            "description": (
                                "true, чтобы убрать срок/горизонт и вернуть цель в "
                                "«Задачи на жизнь» без конкретной даты. Не передавай "
                                "вместе с horizon_months или target_month."
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": "Новая заметка к цели",
                        },
                    },
                    "required": ["title"],
                },
            },
        },
        "handler": _handle_update_life_goal,
    },
    "complete_life_goal": {
        "schema": {
            "type": "function",
            "function": {
                "name": "complete_life_goal",
                "description": (
                    "Отметить цель в разделе «Жизнь» выполненной, или "
                    "вернуть уже выполненную обратно в активные."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Формулировка цели, по которой её найти",
                        },
                        "done": {
                            "type": "boolean",
                            "description": "true (по умолчанию) - отметить выполненной; false - вернуть в активные",
                        },
                    },
                    "required": ["title"],
                },
            },
        },
        "handler": _handle_complete_life_goal,
    },
    "create_project": {
        "schema": {
            "type": "function",
            "function": {
                "name": "create_project",
                "description": (
                    "Создать проект для группировки задач. Вызывай, когда "
                    "пользователь явно просит завести новый проект/цель, а не "
                    "просто упоминает его при создании задачи (для этого "
                    "достаточно параметра project в create_task)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Название проекта"},
                        "description": {"type": "string", "description": "Описание проекта"},
                    },
                    "required": ["name"],
                },
            },
        },
        "handler": _handle_create_project,
    },
    "plan_day": {
        "schema": {
            "type": "function",
            "function": {
                "name": "plan_day",
                "description": (
                    f"Получить топ-{planner_service.DAILY_PLAN_LIMIT} ближайших "
                    "незавершённых задач для дневного плана (не полный список - "
                    "используй list_tasks, если пользователь просит показать/"
                    "обновить весь список задач). Вызывай, когда пользователь "
                    "просит помочь распланировать день или спрашивает, с чего "
                    "начать."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        "handler": _handle_plan_day,
    },
    "list_tasks": {
        "schema": {
            "type": "function",
            "function": {
                "name": "list_tasks",
                "description": (
                    "Получить ПОЛНЫЙ список текущих незавершённых задач "
                    "пользователя (выполненные и отменённые не включены - "
                    "это рабочий список дел, не архив). Вызывай на запрос "
                    "вида «покажи/обнови/пришли список задач» без указания "
                    "конкретного дня - в отличие от plan_day, здесь нет "
                    "ограничения на количество. Если пользователь спрашивает "
                    "про конкретный день (сегодня/завтра/понедельник/дату) - "
                    "используй list_tasks_for_date вместо этого, не "
                    "фильтруй полный список сам."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        "handler": _handle_list_tasks,
    },
    "list_tasks_for_date": {
        "schema": {
            "type": "function",
            "function": {
                "name": "list_tasks_for_date",
                "description": (
                    "Получить задачи ровно на указанную дату (сегодня, "
                    "завтра, конкретный день недели вроде «в понедельник», "
                    "конкретное число). Вычисли ISO-дату сам по текущей "
                    "дате из контекста и передай её - не пытайся сам "
                    "фильтровать вывод list_tasks/plan_day по датам, у "
                    "тебя нет надёжного способа отличить сегодня/завтра/"
                    "другой день, глядя на список вперемешку - это "
                    "реально приводило к неправильным ответам."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {
                            "type": "string",
                            "description": "Дата в формате YYYY-MM-DD",
                        },
                    },
                    "required": ["date"],
                },
            },
        },
        "handler": _handle_list_tasks_for_date,
    },
    "start_focus_session": {
        "schema": {
            "type": "function",
            "function": {
                "name": "start_focus_session",
                "description": (
                    "Начать фокус-сессию (блок глубокой работы). Вызывай, когда "
                    "пользователь подтверждает готовность начать - в том числе в "
                    "ответ на предложенную ранее сессию."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "UUID задачи, над которой идёт сессия, если известен",
                        },
                        "planned_duration_min": {
                            "type": "integer",
                            "description": "Длительность блока в минутах, по умолчанию 90",
                        },
                    },
                },
            },
        },
        "handler": _handle_start_focus_session,
    },
    "complete_focus_session": {
        "schema": {
            "type": "function",
            "function": {
                "name": "complete_focus_session",
                "description": (
                    "Завершить текущую фокус-сессию как успешную, когда пользователь "
                    "рассказывает, что удалось сделать."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "outcome_note": {
                            "type": "string",
                            "description": "Краткое описание того, что было сделано",
                        },
                    },
                    "required": ["outcome_note"],
                },
            },
        },
        "handler": _handle_complete_focus_session,
    },
    "abandon_focus_session": {
        "schema": {
            "type": "function",
            "function": {
                "name": "abandon_focus_session",
                "description": (
                    "Закрыть текущую фокус-сессию как несостоявшуюся, когда пользователь "
                    "сообщает, что не работал, и объясняет, что помешало."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "interruption_reason": {
                            "type": "string",
                            "description": "Что помешало поработать",
                        },
                    },
                    "required": ["interruption_reason"],
                },
            },
        },
        "handler": _handle_abandon_focus_session,
    },
    "remember_fact": {
        "schema": {
            "type": "function",
            "function": {
                "name": "remember_fact",
                "description": (
                    "Сохранить короткий факт о пользователе на будущее (имя, "
                    "предпочтения, важные детали жизни). Вызывай, когда "
                    "пользователь сообщает что-то стоящее запомнить надолго - "
                    "не для разовых деталей текущей задачи."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Короткий идентификатор факта, например 'имя' или 'домашнее_животное'",
                        },
                        "value": {
                            "type": "string",
                            "description": "Значение факта",
                        },
                    },
                    "required": ["key", "value"],
                },
            },
        },
        "handler": _handle_remember_fact,
    },
    "save_note": {
        "schema": {
            "type": "function",
            "function": {
                "name": "save_note",
                "description": (
                    "Сохранить заметку/документ пользователя в базу знаний для "
                    "последующего поиска. Вызывай, когда пользователь просит "
                    "сохранить текст, конспект, инструкцию или ТЗ - не для "
                    "коротких фактов о самом пользователе (для этого remember_fact)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Короткое название заметки"},
                        "content": {"type": "string", "description": "Полный текст заметки"},
                    },
                    "required": ["title", "content"],
                },
            },
        },
        "handler": _handle_save_note,
    },
    "search_knowledge": {
        "schema": {
            "type": "function",
            "function": {
                "name": "search_knowledge",
                "description": (
                    "Найти релевантные фрагменты в сохранённых заметках/документах "
                    "пользователя по смыслу запроса. Вызывай, когда пользователь "
                    "спрашивает о чём-то, что мог раньше записывать в базу знаний."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Что искать"},
                    },
                    "required": ["query"],
                },
            },
        },
        "handler": _handle_search_knowledge,
    },
    "create_contact": {
        "schema": {
            "type": "function",
            "function": {
                "name": "create_contact",
                "description": (
                    "Сохранить новый контакт/клиента в CRM. Вызывай, когда "
                    "пользователь просит добавить контакт или сообщает данные "
                    "нового человека для сохранения."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Имя контакта"},
                        "phone": {"type": "string", "description": "Телефон, если известен"},
                        "email": {"type": "string", "description": "Email, если известен"},
                        "company": {"type": "string", "description": "Компания, если известна"},
                        "notes": {"type": "string", "description": "Прочие заметки о контакте"},
                    },
                    "required": ["name"],
                },
            },
        },
        "handler": _handle_create_contact,
    },
    "log_interaction": {
        "schema": {
            "type": "function",
            "function": {
                "name": "log_interaction",
                "description": (
                    "Записать факт общения с контактом (звонок, встреча, переписка). "
                    "Если контакта с таким именем ещё нет - он будет создан "
                    "автоматически. Вызывай, когда пользователь рассказывает о "
                    "разговоре или встрече с кем-то."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "contact_name": {"type": "string", "description": "Имя контакта"},
                        "note": {
                            "type": "string",
                            "description": "Краткое описание того, что обсуждалось/произошло",
                        },
                        "company": {
                            "type": "string",
                            "description": "Компания контакта, если упомянута и ещё не известна",
                        },
                        "phone": {"type": "string", "description": "Телефон, если упомянут"},
                        "email": {"type": "string", "description": "Email, если упомянут"},
                    },
                    "required": ["contact_name", "note"],
                },
            },
        },
        "handler": _handle_log_interaction,
    },
    "get_contact_info": {
        "schema": {
            "type": "function",
            "function": {
                "name": "get_contact_info",
                "description": (
                    "Получить карточку контакта и историю общения с ним. Вызывай, "
                    "когда пользователь спрашивает о конкретном человеке из CRM."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Имя контакта"},
                    },
                    "required": ["name"],
                },
            },
        },
        "handler": _handle_get_contact_info,
    },
    "get_habit_insights": {
        "schema": {
            "type": "function",
            "function": {
                "name": "get_habit_insights",
                "description": (
                    "Получить статистику фокус-сессий пользователя за последние "
                    f"{HABIT_INSIGHTS_WINDOW_DAYS} дней (доля завершения, причины "
                    "срывов, что удавалось сделать). Вызывай, когда пользователь "
                    "спрашивает о своей продуктивности, привычках или прогрессе - "
                    "и сформулируй закономерность своими словами по данным, а не "
                    "просто перечисляй цифры."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        "handler": _handle_get_habit_insights,
    },
    "connect_google_account": {
        "schema": {
            "type": "function",
            "function": {
                "name": "connect_google_account",
                "description": (
                    "Дать пользователю ссылку для подключения Google-аккаунта "
                    "(календарь, диск, почта - одним разрешением). Вызывай, когда "
                    "пользователь явно просит подключить Google. Для отдельных "
                    "действий (события, файлы, письма) вызывать не нужно - "
                    "соответствующие инструменты сами предложат ссылку, если "
                    "аккаунт ещё не подключён."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        "handler": _handle_connect_google_account,
    },
    "create_calendar_event": {
        "schema": {
            "type": "function",
            "function": {
                "name": "create_calendar_event",
                "description": "Создать событие в Google Calendar пользователя.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "Название события"},
                        "start": {
                            "type": "string",
                            "description": "Начало в формате ISO 8601, например 2026-07-20T14:00:00",
                        },
                        "end": {
                            "type": "string",
                            "description": "Конец в формате ISO 8601",
                        },
                        "description": {"type": "string", "description": "Описание события"},
                    },
                    "required": ["summary", "start", "end"],
                },
            },
        },
        "handler": _handle_create_calendar_event,
    },
    "list_calendar_events": {
        "schema": {
            "type": "function",
            "function": {
                "name": "list_calendar_events",
                "description": "Получить события из Google Calendar пользователя за период.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "time_min": {
                            "type": "string",
                            "description": "Начало периода в формате ISO 8601",
                        },
                        "time_max": {
                            "type": "string",
                            "description": "Конец периода в формате ISO 8601",
                        },
                    },
                    "required": ["time_min", "time_max"],
                },
            },
        },
        "handler": _handle_list_calendar_events,
    },
    "import_drive_document": {
        "schema": {
            "type": "function",
            "function": {
                "name": "import_drive_document",
                "description": (
                    "Найти файл на Google Диске по названию и импортировать его "
                    "текст в базу знаний (для последующего поиска через "
                    "search_knowledge). Поддерживаются только Google Docs и "
                    "текстовые файлы - не PDF/таблицы/изображения."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Название файла или его часть",
                        },
                    },
                    "required": ["name"],
                },
            },
        },
        "handler": _handle_import_drive_document,
    },
    "list_emails": {
        "schema": {
            "type": "function",
            "function": {
                "name": "list_emails",
                "description": (
                    "Получить список последних писем в Gmail пользователя, "
                    "опционально по поисковому запросу (синтаксис поиска Gmail, "
                    "например 'from:ivan', 'is:unread', 'subject:счёт')."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Поисковый запрос в синтаксисе Gmail, необязателен",
                        },
                    },
                },
            },
        },
        "handler": _handle_list_emails,
    },
    "send_email": {
        "schema": {
            "type": "function",
            "function": {
                "name": "send_email",
                "description": (
                    "Отправить письмо через Gmail пользователя. Это необратимое "
                    "действие, видимое третьей стороне - перед вызовом обязательно "
                    "покажи пользователю получателя, тему и текст письма и дождись "
                    "явного подтверждения («да, отправляй» и подобное), не отправляй "
                    "по одной лишь формулировке просьбы без подтверждения точного текста."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Email получателя"},
                        "subject": {"type": "string", "description": "Тема письма"},
                        "body": {"type": "string", "description": "Текст письма"},
                    },
                    "required": ["to", "subject", "body"],
                },
            },
        },
        "handler": _handle_send_email,
    },
    "web_search": {
        "schema": {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "Найти актуальную информацию в интернете - погода, новости, "
                    "факты и всё, чего ты не знаешь сама или что может быть "
                    "устаревшим. Вызывай, когда пользователь спрашивает о чём-то "
                    "актуальном на сейчас, а не о своих задачах/данных в системе."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Поисковый запрос"},
                    },
                    "required": ["query"],
                },
            },
        },
        "handler": _handle_web_search,
    },
}

# Disabled 2026-07-19 - drive.readonly is a restricted OAuth scope (needs a
# paid, annually-renewed CASA audit), dropped from SCOPES in
# google_oauth.py to keep Google verification in the cheap "sensitive"
# tier for calendar.events (the feature users actually asked for). Delete
# this line to re-enable Drive import once drive.readonly comes back.
del TOOLS["import_drive_document"]

# Same reason, same date - gmail.readonly/gmail.send are also restricted
# scopes and were dropped from SCOPES alongside drive.readonly. list_emails/
# send_email were left registered at the time (unlike Drive) since a
# handful of users who'd connected Google *before* the narrowing still had
# a valid Gmail grant on their existing token - but any newly-connecting
# user has no Gmail scope at all, so the model would call these, hit a
# Google 403, and surface a confusing failure instead of an honest "can't
# do that". Disabled outright for the same reason as Drive: delete this
# line (and re-add gmail.readonly/gmail.send to SCOPES) once Gmail goes
# through CASA, rather than leaving it half-working for an unpredictable
# subset of users.
del TOOLS["list_emails"]
del TOOLS["send_email"]


def get_tool_schemas(names: "frozenset[str] | None" = None) -> list[dict]:
    # names=None (every other existing caller, e.g. scripts/regression_test_llm.py)
    # keeps returning every schema, unfiltered - the domain split
    # (app/orchestrator/domains.py) is the only caller that passes a subset.
    if names is None:
        return [tool["schema"] for tool in TOOLS.values()]
    # Sorted, not iterated in frozenset order - Anthropic prompt caching
    # (see app/llm/anthropic_provider.py) treats the tools list as part of
    # the cached prefix, and set iteration order isn't guaranteed identical
    # across two separately-built frozensets with the same elements (same
    # domain combination selected on two different messages). An unstable
    # order would silently defeat caching - the exact "iterating a set"
    # anti-pattern - with no error, just a cache that never hits.
    return [TOOLS[name]["schema"] for name in sorted(names) if name in TOOLS]


async def dispatch_tool_call(
    session: AsyncSession, user: User, name: str, arguments: dict
) -> str:
    tool = TOOLS.get(name)
    if tool is None:
        return f"Неизвестный инструмент: {name}"
    try:
        return await tool["handler"](session, user, arguments)
    except Exception:
        # A handler crashing (bad UUID/date from the model, a downstream API
        # error not already caught, etc.) used to propagate all the way up
        # through handle_message with nothing catching it - the user got
        # total silence, only visible in server logs. Rolling back keeps the
        # session usable for the rest of this turn (further tool calls,
        # append_message) instead of leaving it in a broken transaction
        # state after a failed write.
        logger.exception("tool %s failed for user %s, arguments=%s", name, user.id, arguments)
        await session.rollback()
        return (
            f"Технический сбой при вызове «{name}» - действие не выполнено. "
            "Сообщи пользователю честно, что произошла техническая ошибка и "
            "стоит попробовать ещё раз или переформулировать - не выдумывай, "
            "что действие всё же произошло."
        )
