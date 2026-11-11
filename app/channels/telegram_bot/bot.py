import logging
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.auth.service import get_or_create_user
from app.channels.telegram_bot.keyboards import (
    BTN_SUBSCRIPTION,
    MAIN_MENU,
    PROMPT_FOR_BUTTON,
    set_main_menu_button,
)
from app.config import get_settings
from app.core.billing.service import PRICE_RUB, start_checkout
from app.core.tasks.service import (
    create_task,
    format_task_list,
    format_today_overview,
    get_today_overview,
    list_tasks,
    list_tasks_for_date,
)
from app.database.models.user import User
from app.database.session import async_session_factory
from app.integrations.speech_to_text import SpeechToTextError
from app.integrations.speech_to_text import transcribe as transcribe_voice
from app.integrations.yookassa import YooKassaError
from app.llm.factory import get_llm_provider
from app.orchestrator.agent import handle_message
from app.subscriptions.gate import SubscriptionRequired, check_subscription

logger = logging.getLogger(__name__)

# Deterministic fast path for "what's on my plate today/tomorrow/Monday"
# style questions, bypassing the LLM entirely - same idea as the other
# button shortcuts below (and BTN_TASKS_TODAY/BTN_TASKS_TOMORROW are just
# plain text phrased to be caught by this matcher, not special-cased).
# Added after list_tasks_for_date (the tool built for exactly this) still
# didn't get called reliably for the plainest possible phrasing ("Какие у
# меня сегодня задачи" got answered with a multi-day dump instead) - a
# third instance of prompt wording alone not being enough for a specific
# tool-selection decision, so this one is now decided in code instead.
_DAY_QUERY_TASK_WORDS = re.compile(r"\bзадач\w*\b|\bдел\w*\b", re.IGNORECASE)
# Presence of any of these means the message is asking for an action
# (create/complete/edit/delete/reschedule), not a plain listing - must NOT
# be intercepted by this fast path, it needs the LLM's tool-calling.
# Real incident: "Также задача на завтра купить стиральный порошок" has
# none of the verbs below (no "добавь"/"поставь"/etc - it's phrased as a
# bare statement of a new task, common right after already adding one) -
# the fast path swallowed it as a listing request and dumped tomorrow's
# existing tasks instead of ever reaching create_task; the task was never
# saved. Enumerating every possible creation verb doesn't scale, but
# singular "задача"/"задачу" (nominative/accusative singular - describing
# ONE new item) reliably distinguishes creation-style phrasing from a
# listing query, which in Russian is near-universally plural ("задачи на
# завтра", "какие дела", "список задач") - genuinely ambiguous cases (the
# rarer singular-phrased query) fall through to the LLM instead, which
# already has explicit instructions for date-specific queries.
_DAY_QUERY_EXCLUDE_WORDS = re.compile(
    r"напомни|создай|добавь|отмет|выполн|сдела[лн]|удали|перенес|отмени|поставь|запиши|измени"
    r"|\bзадач[ау]\b|\bдел[оу]\b"
    # Real incident: "каждый день в 9 утра спрашивай у меня какие задачи мне
    # сегодня нужно закрыть" has "задачи" (plural) + "сегодня" like a genuine
    # listing query, and none of the action verbs above - the fast path
    # answered with today's overview instead of ever reaching
    # set_recurring_reminder, silently dropping the recurring check-in the
    # user actually asked for. "кажд*"/"ежедневно" mark a request to
    # configure standing/repeating behavior, not a one-off "what's due"
    # question - SYSTEM_PROMPT already has explicit set_recurring_reminder/
    # set_interval_reminder instructions for exactly this phrasing, this
    # just needs to not be intercepted before reaching the LLM.
    r"|кажд\w*|ежедневно",
    re.IGNORECASE,
)
_DAY_QUERY_MAX_LEN = 80
_WEEKDAY_PATTERNS = [
    (re.compile(r"\bпонедельник\w*\b", re.IGNORECASE), 0),
    (re.compile(r"\bвторник\w*\b", re.IGNORECASE), 1),
    (re.compile(r"\bсред[аеуы]\b", re.IGNORECASE), 2),
    (re.compile(r"\bчетверг\w*\b", re.IGNORECASE), 3),
    (re.compile(r"\bпятниц\w*\b", re.IGNORECASE), 4),
    (re.compile(r"\bсуббот\w*\b", re.IGNORECASE), 5),
    (re.compile(r"\bвоскресень\w*\b", re.IGNORECASE), 6),
]


# Deterministic fast path for the single most common message shape - an
# explicit new-task statement ("задача X"/"добавь задачу X") - bypassing
# the LLM entirely, same principle as _resolve_day_query_date above. Real
# incidents (2026-07-29/30): "Задача починить стул на кухне" and "задача
# купить подушку" both got answered with unrelated content (a stale
# task-list re-hash, a tomorrow's-calendar dump) instead of ever creating
# the task - not a tool-selection ambiguity, a stale pending-question hint
# or a wandering multi-tool detour derailed the turn before create_task
# was ever reached. Deliberately narrow: only the bare "задача <title>"
# shape with no time/date words, matching how this project already treats
# an undated task as the default (due_at is set later via an explicit
# follow-up like "сегодня", same flow as before) - anything mentioning a
# time/date/recurrence falls through to the LLM unchanged, same
# conservative approach as the day-query fast path.
_TASK_CREATE_PREFIX = re.compile(
    # [ау] only - "задача"/"задачу" (singular). "задачи" used to match too
    # ([ауи]) - real incident: "Задачи на неделю"/"Задачи на месяц" got
    # parsed as a title-less create_task call ("на неделю"), because plural
    # "задачи" is exactly the listing-query shape the surrounding comments
    # already call out as reliably distinct from a creation statement (see
    # _DAY_QUERY_EXCLUDE_WORDS's own singular-only \bзадач[ау]\b a few
    # lines up) - this prefix just never matched that same distinction.
    r"^\s*(?:добавь\s+)?задач[ау]\s*[:\-—]?\s*", re.IGNORECASE
)
_TASK_CREATE_EXCLUDE_WORDS = re.compile(
    # через\s+\d only caught the digit form ("через 3 дня") - real incident:
    # "Задача через час забрать посылку" has no digit right after "через"
    # at all, so this exclude list didn't fire and the fast path created a
    # literal "через час забрать посылку" task with no due_at, instead of
    # falling through to the LLM's relative-time parsing.
    r"напомни|через\s+(?:\d|час\w*|минут\w*|полчас\w*|пару|несколько)|завтра|послезавтра|сегодня|\bв\s*\d{1,2}([:.]\d{2})?\b"
    r"|понедельник\w*|вторник\w*|сред[аеуы]|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*"
    r"|кажд\w*|ежедневно"
    # Completion/edit words - "задачу выполнил"/"эту задачу удали" refers to
    # an EXISTING task, not a request to create a new one titled after the
    # verb; must fall through to the LLM (complete_task/update_task/
    # delete_task), same reasoning as the day-query fast path's exclude list.
    r"|выполн\w*|сдела[лн]\w*|удали\w*|перенес\w*|отмени\w*|отмет\w*|готово|закончил\w*|закрыл\w*",
    re.IGNORECASE,
)
_TASK_CREATE_MAX_LEN = 120


def _resolve_new_task_title(text: str) -> str | None:
    match = _TASK_CREATE_PREFIX.match(text)
    if not match or len(text) > _TASK_CREATE_MAX_LEN:
        return None
    title = text[match.end():].strip()
    if not title or _TASK_CREATE_EXCLUDE_WORDS.search(title.lower()):
        return None
    return title


# Deterministic fast path for a plain "show me all my tasks" listing with
# NO date word at all (day-query above only fires when a date IS present).
# Real incident, 2026-07-30: "покажи все мои задачи" got a completely
# fabricated reply about reminders that were never even discussed - the
# LLM path never even attempted list_tasks, it just wandered off into
# whatever memory_summary/hint context was loaded. Same conservative
# exclude-word gate as the day-query fast path (a plural task-word plus a
# listing verb, no create/complete/date words) - this is the plainest,
# highest-frequency query shape in the whole app, no reason to trust the
# LLM to always route it correctly when it demonstrably doesn't.
_LIST_ALL_VERBS = re.compile(r"покажи|выведи|список|перечисли|какие|мои\s+задач", re.IGNORECASE)


def _resolve_list_all_tasks(text: str) -> bool:
    if len(text) > _DAY_QUERY_MAX_LEN:
        return False
    lowered = text.lower()
    if _DAY_QUERY_EXCLUDE_WORDS.search(lowered) or not _DAY_QUERY_TASK_WORDS.search(lowered):
        return False
    return bool(_LIST_ALL_VERBS.search(lowered))


def _resolve_day_query_date(text: str, tz_name: str) -> date | None:
    if len(text) > _DAY_QUERY_MAX_LEN:
        return None
    lowered = text.lower()
    if _DAY_QUERY_EXCLUDE_WORDS.search(lowered) or not _DAY_QUERY_TASK_WORDS.search(lowered):
        return None

    today = datetime.now(timezone.utc).astimezone(ZoneInfo(tz_name)).date()
    if "послезавтра" in lowered:
        return today + timedelta(days=2)
    if "завтра" in lowered:
        return today + timedelta(days=1)
    if "сегодня" in lowered:
        return today
    for pattern, weekday in _WEEKDAY_PATTERNS:
        if pattern.search(lowered):
            return today + timedelta(days=(weekday - today.weekday()) % 7)
    return None

settings = get_settings()
bot = Bot(token=settings.telegram_bot_token)
dp = Dispatcher()
llm = get_llm_provider()


_SUBSCRIPTION_STATUS_RU = {
    "trialing": "пробный период",
    "active": "активна",
    "past_due": "просрочена",
    "canceled": "отменена",
}


async def _format_subscription(session, user: User) -> str:
    status_ru = _SUBSCRIPTION_STATUS_RU.get(user.subscription_status, user.subscription_status)
    lines = [f"Тариф: {user.subscription_plan}", f"Статус: {status_ru}"]
    if user.subscription_status == "trialing" and user.trial_ends_at:
        lines.append(f"Пробный период до: {user.trial_ends_at:%Y-%m-%d}")
    if user.current_period_end:
        lines.append(f"Действует до: {user.current_period_end:%Y-%m-%d}")
    # Offer a real way to pay whenever the subscription isn't simply active -
    # trialing (may as well let them lock in early), past_due, or canceled.
    # Previously this screen (and the trial-expired message below) just told
    # the user to "оформи подписку" with no actual way to do that.
    if user.subscription_status != "active":
        settings = get_settings()
        try:
            confirmation_url = await start_checkout(session, user, return_url=settings.app_base_url)
            lines.append(f"\nОплатить ({PRICE_RUB} ₽/мес): {confirmation_url}")
        except YooKassaError:
            logger.exception("failed to start checkout for user %s", user.id)
            lines.append("\nНе получилось создать ссылку на оплату — попробуй ещё раз чуть позже.")
    return "\n".join(lines)


@dp.message(CommandStart())
async def on_start(message: Message) -> None:
    async with async_session_factory() as session:
        await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )
    await message.answer(
        "Привет! Я твой ассистент-трекер и я помогу не забывать о важном — задачи, "
        "напоминания, фокус и цели на будущее, всё в одном месте. Можно написать или "
        "наговорить голосом, что нужно сделать. Загляни в «📆 Планер» (кнопка у поля "
        "ввода) — там удобно видеть и редактировать всё сразу.",
        reply_markup=MAIN_MENU,
    )


@dp.message()
async def on_message(message: Message) -> None:
    if message.voice:
        try:
            file_info = await bot.get_file(message.voice.file_id)
            buffer = await bot.download_file(file_info.file_path)
            # Telegram voice messages are already OGG/Opus, SpeechKit's
            # default format - no transcoding needed.
            text = await transcribe_voice(buffer.read())
        except SpeechToTextError:
            await message.answer(
                "Не получилось распознать голосовое сообщение — попробуй ещё раз или напиши текстом."
            )
            return
        except Exception:
            # Anything else (Telegram file-fetch failure, unexpected error
            # inside speech_to_text not already normalized to
            # SpeechToTextError) - same total-silence risk as the rest of
            # this handler, same fix.
            logger.exception("voice message handling failed for user %s", message.from_user.id)
            await message.answer(
                "Техническая ошибка при обработке голосового сообщения — попробуй ещё раз или напиши текстом."
            )
            return
        if not text:
            await message.answer(
                "Не разобрал, что сказано — попробуй ещё раз или напиши текстом."
            )
            return
    elif message.text:
        text = message.text
    else:
        await message.answer("Пока умею работать с текстом и голосовыми сообщениями.")
        return

    async with async_session_factory() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )

        try:
            check_subscription(user)
        except SubscriptionRequired:
            reply = "Пробный период закончился. Оформи подписку, чтобы продолжить пользоваться ассистентом."
            try:
                settings = get_settings()
                confirmation_url = await start_checkout(session, user, return_url=settings.app_base_url)
                reply += f"\n\nОплатить ({PRICE_RUB} ₽/мес): {confirmation_url}"
            except YooKassaError:
                logger.exception("failed to start checkout for user %s", user.id)
            await message.answer(reply)
            return

        # Some menu buttons are answered directly (deterministic, no LLM
        # round-trip needed for a plain status display); others just
        # forward a canned prompt into the normal pipeline since a tool for
        # that already exists (see PROMPT_FOR_BUTTON). BTN_TASKS_TODAY/
        # BTN_TASKS_TOMORROW aren't special-cased here - their text is
        # phrased so the day-query fast path below catches them.
        if text == BTN_SUBSCRIPTION:
            await message.answer(await _format_subscription(session, user))
            return

        new_task_title = _resolve_new_task_title(text)
        if new_task_title is not None:
            task, was_created = await create_task(
                session=session, user_id=user.id, title=new_task_title
            )
            if was_created:
                await message.answer(f"Добавил: «{task.title}».")
            else:
                await message.answer(
                    f"У тебя уже была похожая незавершённая задача «{task.title}» "
                    "- дополнил её вместо создания дубликата."
                )
            return

        day_query_date = _resolve_day_query_date(text, user.timezone)
        if day_query_date is not None:
            today_local = datetime.now(timezone.utc).astimezone(ZoneInfo(user.timezone)).date()
            if day_query_date == today_local:
                # "Today" splits into actual (still to do) vs completed,
                # unlike other days - see get_today_overview's docstring.
                actual, completed = await get_today_overview(session, user.id, user.timezone)
                await message.answer(format_today_overview(actual, completed, user.timezone))
            else:
                tasks = await list_tasks_for_date(session, user.id, day_query_date, user.timezone)
                await message.answer(format_task_list(tasks, user.timezone))
            return

        if _resolve_list_all_tasks(text):
            tasks = await list_tasks(session, user.id)
            await message.answer(format_task_list(tasks, user.timezone))
            return

        text = PROMPT_FOR_BUTTON.get(text, text)
        # Telegram's reply-to relationship isn't visible to the model
        # otherwise - "поменяй формат вот этого списка" sent as a reply to
        # an earlier bot message had no way to resolve what "этого" meant,
        # confirmed live (real incident, 2026-07-28).
        reply_to_text = (
            message.reply_to_message.text or message.reply_to_message.caption
            if message.reply_to_message
            else None
        )
        try:
            reply = await handle_message(session, user, llm, text, reply_to_text=reply_to_text)
        except Exception:
            # dispatch_tool_call already catches per-tool failures - this is
            # the outer net for anything else in the pipeline (the LLM call
            # itself, memory summarization, etc.). Without it, the user got
            # total silence with zero indication anything went wrong - only
            # visible by checking server logs after the fact.
            logger.exception("handle_message failed for user %s", user.id)
            reply = "Техническая ошибка при обработке сообщения — попробуй ещё раз."

    await message.answer(reply or "Готово.")


async def run_polling() -> None:
    # A webhook set from prior/other tooling blocks getUpdates outright, so
    # long polling can't start until it's cleared. deliberately NOT passing
    # drop_pending_updates=True: that flag discards every update Telegram
    # queued while the process was down (not just webhook-related ones) -
    # on every restart/deploy, not just the one-time stale-webhook case it
    # was added for. A user messaging during the ~20s restart window (model
    # loading) would get silent total silence with zero trace in our own
    # logs or DB, since Telegram drops the update before we ever see it.
    # Clearing the webhook alone is enough to unblock getUpdates.
    await bot.delete_webhook()
    await set_main_menu_button(bot)
    await dp.start_polling(bot)
