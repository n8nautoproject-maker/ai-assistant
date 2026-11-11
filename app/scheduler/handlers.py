"""job_type -> handler. Register a new proactive trigger by adding an entry
here; the poller in runner.py does not need to change.

Handlers that need the database open their own session rather than reusing
the poller's - keeps them decoupled from runner.py's job-row transaction,
same pattern the bot channel uses per incoming message.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.channels.telegram_bot.sender import send_message
from app.core.billing.service import charge_recurring
from app.core.focus.service import (
    abandon_session,
    get_open_session,
    mark_awaiting_followup,
)
from app.core.habits.service import format_stats_summary, get_focus_stats
from app.core.life_goals.service import carry_forward_life_goals, format_horizon
from app.core.memory.service import append_message
from app.core.namaz.service import PRAYER_NAMES_RU, refresh_namaz_schedule
from app.core.reminders.service import mark_awaiting_reply
from app.core.tasks.service import (
    carry_forward_overdue_tasks,
    clear_today_priority,
    format_task_list,
    get_overdue_pending_tasks,
    get_today_priority_tasks,
    list_tasks_for_date,
    mark_awaiting_confirmation,
)
from app.database.models.focus_session import FocusSession, FocusSessionStatus
from app.database.models.task import Task, TaskStatus
from app.database.models.user import SubscriptionStatus, User
from app.database.session import async_session_factory
from app.llm.factory import get_llm_provider

WEEKLY_SUMMARY_PROMPT = (
    "Ты — персональный ассистент пользователя. Ниже сырая статистика его "
    "фокус-сессий за последнюю неделю. Напиши короткое (3-5 предложений), "
    "дружелюбное сообщение по-русски: отметь закономерность, если она видна "
    "в данных (например, связь между временем начала и завершением, "
    "повторяющиеся причины срывов), без выдумывания фактов, которых нет в "
    "данных. Не используй markdown-форматирование."
)

JobHandler = Callable[[User, dict], Awaitable[None]]


async def _send_and_record(session, user: User, text: str) -> None:
    # Proactive messages used to vanish from context the instant they were
    # sent - only handle_message's own turns were ever persisted to
    # conversation_messages, so a later reply referring back to what a
    # check-in/reminder just said ("эта задача", "она") had nothing to
    # resolve against and the model had to guess or ask to clarify.
    # Recording the outgoing text here makes those references resolvable.
    await send_message(user.telegram_id, text)
    await append_message(session, user, role="assistant", content=text)


# Prefix markers on the generic mark_awaiting_reply/pop_pending_reminder_prompt
# text (app/core/reminders/service.py) - lets _reminder_reply_hint in
# app/orchestrator/agent.py tell which kind of proactive question a reply is
# answering without a dedicated state table per question type.
MORNING_PRIORITY_MARKER = "MORNING_PRIORITY_QUESTION::"
PRIORITY_PROGRESS_MARKER = "PRIORITY_PROGRESS_CHECKIN::"


async def _handle_morning_checkin(user: User, payload: dict) -> None:
    async with async_session_factory() as session:
        open_session = await get_open_session(session, user.id)
        if open_session is not None and open_session.status != FocusSessionStatus.PLANNED.value:
            await _send_and_record(
                session,
                user,
                "Доброе утро! У тебя ещё не закрыта вчерашняя фокус-сессия — продолжим с неё.",
            )
            return

        if open_session is not None:
            # PLANNED and never started (started_at stays None) - a stale,
            # ignored proposal from an earlier day, not real unfinished
            # work. Retiring it instead of re-proposing it forever fixes a
            # real bug: it used to sit in OPEN_STATUSES indefinitely and
            # get reported as "unclosed" every morning even though the
            # user never engaged with it - see docs/ARCHITECTURE.md.
            await abandon_session(
                session, user.id, interruption_reason="не принято, заменено новым предложением"
            )

        # Used to auto-pick the single earliest-due task as "today's main
        # task" with no way for the user to say otherwise - confusing when
        # the pick didn't match their own sense of priority (real feedback:
        # "с чего он взял что зашить футболку это главная задача"). Now the
        # user names it themselves; is_priority is reset first so a stale
        # pick from a previous day never lingers into today's question.
        await clear_today_priority(session, user.id)

        today_local = datetime.now(timezone.utc).astimezone(ZoneInfo(user.timezone)).date()
        today_tasks = await list_tasks_for_date(session, user.id, today_local, user.timezone)
        if not today_tasks:
            text = "Доброе утро! На сегодня задач со сроком нет — напиши, что сегодня главное, и начнём с этого."
            await _send_and_record(session, user, text)
            # This is a question, unlike the "here's the list" branch below -
            # the next reply needs _reminder_reply_hint's anchor to be
            # treated as an instruction to create a task, not just chat.
            # See the "сделать парсер и спарсить группы" bug.
            await mark_awaiting_reply(session, user, text)
            return

        listing = format_task_list(today_tasks, user.timezone)
        text = f"Доброе утро! Вот задачи на сегодня:\n{listing}\n\nКакая или какие из них в приоритете?"
        await _send_and_record(session, user, text)
        await mark_awaiting_reply(session, user, f"{MORNING_PRIORITY_MARKER}{text}")


async def _handle_priority_checkin(user: User, payload: dict) -> None:
    async with async_session_factory() as session:
        priority_tasks = await get_today_priority_tasks(session, user.id)
        if not priority_tasks:
            # Nothing was marked priority today (user never answered the
            # morning question, or named none) - a "how's it going?" with
            # nothing to point at would just be noise, so stay silent.
            return
        listing = "; ".join(f"«{t.title}»" for t in priority_tasks)
        text = f"Как продвигается: {listing}?"
        await _send_and_record(session, user, text)
        await mark_awaiting_reply(session, user, f"{PRIORITY_PROGRESS_MARKER}{listing}")


async def _handle_focus_followup(user: User, payload: dict) -> None:
    focus_session_id = uuid.UUID(payload["session_id"])
    async with async_session_factory() as session:
        focus_session = await session.get(FocusSession, focus_session_id)
        if focus_session is None or focus_session.status != FocusSessionStatus.ACTIVE.value:
            return
        await mark_awaiting_followup(session, focus_session)
        await _send_and_record(session, user, "Что удалось сделать?")


async def _handle_task_reminder(user: User, payload: dict) -> None:
    task_id = uuid.UUID(payload["task_id"])
    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        # Task may have been completed/deleted since the reminder was set -
        # a stale reminder for a done task would be noise, not a nudge.
        if task is None or task.status != TaskStatus.PENDING.value:
            return
        # Marks the task so the next incoming message is interpreted as an
        # answer to "получилось сделать?" instead of ambiguous free text -
        # see _task_followup_hint in app/orchestrator/agent.py.
        await mark_awaiting_confirmation(session, task)
        await _send_and_record(session, user, f"⏰ {task.title} — получилось сделать?")


async def _handle_evening_review(user: User, payload: dict) -> None:
    """Daily sweep for tasks whose due_at passed today (or earlier) and
    were never marked done or picked up by a task_reminder check - without
    this, a due-date-only task with no remind_at could sit overdue forever
    with nobody ever asking about it. Marks every overdue task awaiting
    confirmation at once; the reply (partial completions, "перенеси
    остальное") is handled by _task_followup_hint's multi-task branch, and
    whatever the user doesn't confirm done gets rescheduled to tomorrow
    from there via update_task - see app/orchestrator/agent.py.
    """
    async with async_session_factory() as session:
        overdue = await get_overdue_pending_tasks(session, user.id, user.timezone)
        if not overdue:
            return
        for task in overdue:
            await mark_awaiting_confirmation(session, task)
        listing = "; ".join(f"«{t.title}»" for t in overdue)
        await _send_and_record(
            session,
            user,
            f"Вечерний итог: не отмечено выполненным — {listing}. Что из "
            "этого получилось сделать? Остальное перенесу на завтра.",
        )


async def _handle_custom_reminder(user: User, payload: dict) -> None:
    async with async_session_factory() as session:
        await _send_and_record(session, user, payload["text"])
        # Unlike task_reminder/focus_followup, this push has no dedicated
        # state table to flag "awaiting reply" on - see
        # app.orchestrator.agent._reminder_reply_hint for how this gets
        # read back on the user's next message.
        await mark_awaiting_reply(session, user, payload["text"])


async def _handle_weekly_habit_summary(user: User, payload: dict) -> None:
    since = datetime.now(timezone.utc) - timedelta(days=7)
    async with async_session_factory() as session:
        stats = await get_focus_stats(session, user.id, since)

        if stats.total == 0:
            await _send_and_record(
                session,
                user,
                "На этой неделе фокус-сессий не было — как насчёт того, чтобы начать новую?",
            )
            return

        llm = get_llm_provider()
        response = await llm.chat(
            [
                {"role": "system", "content": WEEKLY_SUMMARY_PROMPT},
                {"role": "user", "content": format_stats_summary(stats)},
            ]
        )
        await _send_and_record(session, user, response.content or format_stats_summary(stats))


async def _handle_namaz_daily_refresh(user: User, payload: dict) -> None:
    async with async_session_factory() as session:
        await refresh_namaz_schedule(session, user)


# Deliberately silent (no push) - real incident 2026-08-13: a task due late
# the previous evening, never marked done, simply vanished from "Сегодня"
# at local midnight since that view only ever date-matches today. This
# rolls it (and anything else still-pending and stale) onto today so it
# reappears there without the user having to notice and manually move it -
# see carry_forward_overdue_tasks's own docstring for why this is a
# separate job from get_overdue_pending_tasks/evening_review rather than
# folded into it.
async def _handle_carry_forward_overdue(user: User, payload: dict) -> None:
    async with async_session_factory() as session:
        await carry_forward_overdue_tasks(session, user.id, user.timezone)


# Real request from a user 2026-08-12, after voice-created monthly goals
# fell through the chat entirely (see the fix in
# app/orchestrator/domains.py/tools_registry.py the same session): "как
# истекает месяц - спросить, переносить ли эти цели или они выполнены".
# Auto-carries forward by default (same safe-default reasoning as
# carry_forward_overdue_tasks for tasks) rather than requiring a reply the
# model would then have to parse - informs, doesn't interrogate. Marking
# something done is a one-tap ✓ in the Mini App's Жизнь tab either way.
async def _handle_life_goal_expiry_checkin(user: User, payload: dict) -> None:
    async with async_session_factory() as session:
        moved = await carry_forward_life_goals(session, user.id)
        if not moved:
            return
        listing = "; ".join(f"«{g.title}» ({format_horizon(g.horizon_months)})" for g in moved)
        await _send_and_record(
            session,
            user,
            f"Истёк срок и перенёс на следующий период: {listing}. Если что-то "
            "из этого уже сделано — отметьте ✓ во вкладке «Жизнь», и цель "
            "уйдёт в выполненные.",
        )


async def _handle_namaz_prayer_reminder(user: User, payload: dict) -> None:
    name = PRAYER_NAMES_RU.get(payload.get("prayer", ""), payload.get("prayer", ""))
    async with async_session_factory() as session:
        await _send_and_record(session, user, f"🕌 Время намаза: {name}")


async def _handle_subscription_renewal(user: User, payload: dict) -> None:
    """Fires daily per user (cheap no-op most days) - re-fetches the user
    fresh in its own session rather than trusting the poller's copy of
    `user`, same "each handler is self-contained" reasoning as every other
    handler in this file. Only actually attempts a charge on the two days
    that matter: the day a trial expires, or the day an active period ends
    - past_due/canceled users are NOT auto-retried every day (that would
    both spam ЮKassa with doomed charge attempts and spam the user with a
    "couldn't renew" message daily) - they have to explicitly pay again via
    /billing/checkout, same flow as a first-time subscriber.
    """
    async with async_session_factory() as session:
        fresh_user = await session.get(User, user.id)
        if fresh_user is None:
            return
        now = datetime.now(timezone.utc)

        if fresh_user.subscription_status == SubscriptionStatus.TRIALING.value:
            if not fresh_user.trial_ends_at or fresh_user.trial_ends_at > now:
                return
        elif fresh_user.subscription_status == SubscriptionStatus.ACTIVE.value:
            if not fresh_user.current_period_end or fresh_user.current_period_end > now:
                return
        else:
            return

        succeeded = await charge_recurring(session, fresh_user)
        if not succeeded:
            await _send_and_record(
                session,
                fresh_user,
                "Не получилось продлить подписку — привяжите карту заново через "
                "мини-приложение (вкладка «Ещё» → «Подписка»).",
            )


JOB_HANDLERS: dict[str, JobHandler] = {
    "morning_checkin": _handle_morning_checkin,
    "priority_checkin": _handle_priority_checkin,
    "focus_followup": _handle_focus_followup,
    "weekly_habit_summary": _handle_weekly_habit_summary,
    "task_reminder": _handle_task_reminder,
    "evening_review": _handle_evening_review,
    "custom_reminder": _handle_custom_reminder,
    "namaz_daily_refresh": _handle_namaz_daily_refresh,
    "namaz_prayer_reminder": _handle_namaz_prayer_reminder,
    "subscription_renewal": _handle_subscription_renewal,
    "carry_forward_overdue": _handle_carry_forward_overdue,
    "life_goal_expiry_checkin": _handle_life_goal_expiry_checkin,
}
